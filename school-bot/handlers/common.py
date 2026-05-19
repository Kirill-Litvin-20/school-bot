import re
from pathlib import Path

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, InputMediaDocument, InputMediaPhoto, Message

from data import load_reviews_from_folder
from keyboards import (
    get_main_menu_keyboard,
    get_review_card_keyboard,
    get_teacher_card_keyboard,
)
from states import ApplicationForm
from shared.database import (
    get_active_invitee_discount_percent,
    get_active_promo_for_student_id,
    get_active_review_cards,
    get_attendance_summary_for_student,
    get_recent_attendance_for_student,
    get_teacher_cards_by_subject,
)

BOT_DIR = Path(__file__).resolve().parent.parent.parent  # Points to /opt/school-system/


async def flow_edit(
    callback: CallbackQuery,
    state: FSMContext,
    text: str,
    reply_markup=None,
    parse_mode: str | None = None,
) -> None:
    """Edit the current flow message in-place and save its ID to FSM state."""
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        await state.update_data(_flow_msg_id=callback.message.message_id)
    except Exception:
        sent = await callback.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        await state.update_data(_flow_msg_id=sent.message_id)


async def flow_message(
    message: Message,
    state: FSMContext,
    text: str,
    reply_markup=None,
    parse_mode: str | None = None,
) -> None:
    """Delete previous bot prompt, send new one, save its ID to FSM state."""
    data = await state.get_data()
    prev_id = data.get("_flow_msg_id")
    if prev_id:
        try:
            await message.bot.delete_message(message.chat.id, prev_id)
        except Exception:
            pass
    sent = await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
    await state.update_data(_flow_msg_id=sent.message_id)


def is_valid_telegram_username(text: str) -> bool:
    return bool(re.fullmatch(r"@[A-Za-z0-9_]{5,32}", text.strip()))


def is_valid_phone(text: str) -> bool:
    cleaned = re.sub(r"[^\d+]", "", text.strip())

    if cleaned.startswith("+"):
        digits = cleaned[1:]
        return digits.isdigit() and 10 <= len(digits) <= 15

    return cleaned.isdigit() and 10 <= len(cleaned) <= 15


def resolve_local_path(path_value: str) -> str:
    path = Path(path_value)
    if path.is_absolute():
        return str(path)
    return str((BOT_DIR / path).resolve())


def resolve_review_media(media_type: str | None, media_local_path: str | None, media_file_id: str | None):
    """
    Resolve review media for display.

    Returns:
    - FSInputFile object if local file exists
    - Telegram file_id string if local doesn't exist but file_id available
    - None if no media available
    """
    import logging

    # Try local file first (permanent storage)
    if media_local_path:
        try:
            abs_path = resolve_local_path(media_local_path)
            resolved_path = Path(abs_path)
            if resolved_path.exists():
                return FSInputFile(abs_path)
            else:
                logging.warning(f"Local media file not found: {media_local_path}")
        except Exception as e:
            logging.warning(f"Error resolving local media path '{media_local_path}': {e}")

    # Fallback to Telegram file_id (temporary storage)
    if media_file_id:
        logging.debug(f"Falling back to Telegram file_id (local path unavailable)")
        return media_file_id

    # No media available
    return None


def get_teacher_cards_for_subject(subject: str) -> list[dict]:
    normalized: list[dict] = []

    for card in get_teacher_cards_by_subject(subject):
        teacher_name = (card.get("name") or "").strip()
        if not teacher_name:
            continue
        normalized.append(
            {
                "name": teacher_name,
                "description": card.get("description") or "Описание преподавателя будет добавлено позже.",
                "photo": card.get("photo"),
                "telegram_id": card.get("telegram_id"),
            }
        )

    return normalized


def get_review_cards() -> list[dict]:
    cards: list[dict] = []

    # Load only from database (respects is_active flag for proper deletion)
    for card in get_active_review_cards(limit=500):
        cards.append(
            {
                "id": card.get("id"),
                "description": card.get("description") or "Отзыв",
                "media_type": card.get("media_type"),
                "media_file_id": card.get("media_file_id"),
                "media_local_path": card.get("media_local_path"),
                "links": card.get("links") or [],
            }
        )

    # Note: Folder-based reviews removed - use database for all review management
    # This ensures deleted reviews are properly hidden (is_active=0)

    return cards


def _get_photo_media(photo_ref: str | None):
    if not photo_ref:
        return None
    resolved = Path(resolve_local_path(photo_ref))
    if resolved.exists():
        return FSInputFile(str(resolved))
    import logging
    logging.warning(
        "Teacher photo file not found locally, falling back to telegram ref. "
        "stored=%s resolved=%s",
        photo_ref,
        resolved,
    )
    return photo_ref


def format_tariff_type(tariff_type: str) -> str:
    return "Разовое занятие" if tariff_type == "single" else "Пакет занятий"


def format_payment_status(status: str) -> str:
    return {
        "pending": "Ожидает проверки",
        "processing": "На проверке",
        "approved": "Подтверждена",
        "rejected": "Отклонена",
        "expired": "Просрочена",
    }.get(status, status)


def build_application_text(data: dict) -> str:
    teacher_text = data.get("teacher_choice", "Не указано")
    if data.get("teacher_choice") == "Выбрать конкретного":
        teacher_text = f"{data['teacher_name']}"

    subjects = data.get("subjects", [])
    subjects_text = ", ".join(subjects) if subjects else "-"

    text = (
        "📌 <b>Новая заявка</b>\n\n"
        f"👤 <b>Кто оставил заявку:</b> {data.get('user_type', '-')}\n"
        f"📝 <b>Имя:</b> {data.get('name', '-')}\n"
        f"🏫 <b>Класс:</b> {data.get('school_class', '-')}\n"
        f"🎯 <b>Цель:</b> {data.get('goal', '-')}\n"
        f"📚 <b>Формат занятий:</b> {data.get('lesson_type', '-')}\n"
        f"📖 <b>Предметы:</b> {subjects_text}\n"
        f"👨‍🏫 <b>Преподаватель:</b> {teacher_text}\n"
        f"📞 <b>Способ связи:</b> {data.get('contact_method', '-')}\n"
        f"🔗 <b>Контакт:</b> {data.get('contact_value', '-')}\n"
        f"💬 <b>Комментарий:</b> {data.get('comment', '-')}"
    )

    referral_code = data.get("referral_code")
    if referral_code:
        text += f"\n🎁 <b>Реферал от:</b> tg_id={referral_code}"

    return text


def build_recent_payments_text(recent_payments: list[tuple]) -> str:
    if not recent_payments:
        return "История оплат пока отсутствует."

    lines = ["<b>Последние оплаты:</b>"]

    for index, payment in enumerate(recent_payments, start=1):
        payment_id, status, caption_text, created_at, _updated_at, lessons_added = payment
        lines.append(
            f"{index}. Оплата #{payment_id}\n"
            f"   Статус: <b>{format_payment_status(status)}</b>\n"
            f"   Дата: {created_at}\n"
            f"   Начислено занятий: <b>{lessons_added}</b>\n"
            f"   Комментарий: {caption_text if caption_text else '-'}"
        )

    return "\n".join(lines)


def _format_attendance_status(status: str) -> str:
    return {
        "present": "✅ был",
        "completed": "✅ был",
        "absent": "❌ пропуск",
        "missed": "❌ пропуск",
        "skipped": "❌ пропуск",
        "cancelled": "↩️ отменено",
    }.get(status, status or "—")


_MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн",
                 "июл", "авг", "сен", "окт", "ноя", "дек"]


def _fmt_short_date(date_str: str) -> str:
    try:
        y, m, d = date_str.split("-")
        return f"{int(d)} {_MONTHS_SHORT[int(m) - 1]}"
    except Exception:
        return date_str or "—"


def _fmt_short_datetime(datetime_str: str) -> str:
    try:
        date_part, time_part = datetime_str.split(" ", 1)
        y, m, d = date_part.split("-")
        hh, mm = time_part.split(":")[:2]
        return f"{int(d)} {_MONTHS_SHORT[int(m) - 1]} {hh}:{mm}"
    except Exception:
        return _fmt_short_date(datetime_str[:10]) if datetime_str else "—"


def _fmt_payment_status_short(status: str) -> str:
    return {
        "pending":    "⏳ ожидает",
        "processing": "🔄 проверяется",
        "approved":   "✅ принята",
        "rejected":   "❌ отклонена",
        "expired":    "⌛ просрочена",
    }.get(status, status)


def build_cabinet_text(
    student_name: str,
    directions: list[tuple],
    recent_payments: list[tuple],
    student_id: int | None = None,
) -> str:
    positive_balance = sum(d[3] for d in directions if d[3] > 0)
    debt_total = sum(-d[3] for d in directions if d[3] < 0)

    lines = [f"👤 <b>{student_name}</b>", ""]

    # --- Баланс ---
    if debt_total > 0:
        lines.append(f"🔴 <b>Долг:</b> {debt_total} зан.   |   ✅ <b>Баланс:</b> {positive_balance} зан.")
    else:
        lines.append(f"✅ <b>Баланс:</b> {positive_balance} зан.")

    # --- Скидки / промокод ---
    if student_id is not None:
        discount_percent = get_active_invitee_discount_percent(student_id)
        if discount_percent:
            lines.append(f"🎁 Реферальная скидка <b>{discount_percent}%</b> на первую оплату")
        promo = get_active_promo_for_student_id(student_id)
        if promo:
            _, code, dtype, dvalue, _ = promo
            unit = "%" if dtype == "percent" else "₽"
            lines.append(f"🎟 Промокод <b>{code}</b> — скидка {int(float(dvalue))}{unit}")

    # --- Направления ---
    if directions:
        lines.append("")
        lines.append("📚 <b>Направления</b>")
        for direction in directions:
            _, teacher_name, subject_name, lesson_balance, _ = direction
            if lesson_balance < 0:
                bal = f"⚠️ долг {-lesson_balance} зан."
            elif lesson_balance == 0:
                bal = "0 зан."
            else:
                bal = f"{lesson_balance} зан."
            lines.append(f"  • {subject_name} <i>({teacher_name})</i> — {bal}")
    else:
        lines.append("")
        lines.append("📚 Направления ещё не назначены.")

    # --- Последние занятия (до 3) ---
    if student_id is not None and directions:
        recent = get_recent_attendance_for_student(student_id, limit=3)
        if recent:
            lines.append("")
            lines.append("🗓 <b>Последние занятия</b>")
            for entry in recent:
                date_view = _fmt_short_date((entry["lesson_date"] or "")[:10])
                lines.append(
                    f"  • {date_view} — {entry['subject_name']}: "
                    f"{_format_attendance_status(entry['status'])}"
                )

    # --- Последние оплаты (до 4) ---
    if recent_payments:
        lines.append("")
        lines.append("💳 <b>Последние оплаты</b>")
        for payment in recent_payments[:4]:
            _, status, _, created_at, _, lessons_added = payment[:6]
            source_platform = payment[6] if len(payment) > 6 else "telegram"
            date_view = _fmt_short_datetime(str(created_at) if created_at else "")
            status_label = _fmt_payment_status_short(status)
            lessons_str = f" <b>+{lessons_added} зан.</b>" if lessons_added else ""
            lines.append(f"  • {date_view} — {status_label}{lessons_str}")

    return "\n".join(lines)


def build_admin_contacts_text() -> str:
    return (
        "<b>Напишите администратору:</b> "
        "<a href=\"https://t.me/integral_school_ru\">@integral_school_ru</a>"
    )


def build_multi_students_warning(students_count: int) -> str:
    if students_count <= 1:
        return ""
    return (
        "\n\n⚠️ В базе найдено несколько карточек с этим Telegram ID. "
        "Сейчас отображается самая актуальная запись."
    )


def build_payment_caption(
    payment_request_id: int,
    full_name: str | None,
    username: str | None,
    telegram_user_id: int | None,
    caption_text: str | None,
    status_text: str,
    referral_discount_percent: int | None = None,
    payment_type_label: str | None = None,
    promo_label: str | None = None,
    direction_label: str | None = None,
) -> str:
    text = (
        f"💳 <b>Оплата #{payment_request_id}</b>\n\n"
        f"📌 <b>Статус:</b> {status_text}\n"
        f"👤 <b>Имя в Telegram:</b> {full_name if full_name else '-'}\n"
        f"🔗 <b>Username:</b> {username if username else 'не указан'}\n"
        f"🆔 <b>Telegram ID:</b> <code>{telegram_user_id if telegram_user_id else '-'}</code>"
    )

    if payment_type_label:
        text += f"\n💰 <b>Тип оплаты:</b> {payment_type_label}"

    if direction_label:
        text += f"\n📚 <b>Направление:</b> {direction_label}"

    if promo_label:
        text += f"\n🎟 <b>Промокод:</b> {promo_label}"

    if referral_discount_percent:
        text += (
            f"\n🎁 <b>Реферальная скидка {referral_discount_percent}%</b> "
            "— ученик платит первое занятие со скидкой, учтите при сверке суммы."
        )

    if caption_text:
        text += f"\n💬 <b>Комментарий:</b> {caption_text}"

    return text


async def show_main_menu(message_obj: Message, state: FSMContext):
    data = await state.get_data()
    user_type = data.get("user_type")

    await state.clear()
    if user_type:
        await state.update_data(user_type=user_type)

    await message_obj.answer(
        "📋 Пожалуйста, выберите нужный раздел:",
        reply_markup=get_main_menu_keyboard(),
    )
    await state.set_state(ApplicationForm.menu)


async def send_teacher_card(
    message_obj: Message, subject: str, index: int, state: FSMContext
):
    teachers = get_teacher_cards_for_subject(subject)
    teacher = teachers[index]

    text = (
        f"Преподаватель: {teacher['name']}\n"
        f"Предмет: {subject}\n\n"
        f"{teacher['description']}"
    )

    await state.update_data(
        selected_teacher_subject=subject,
        selected_teacher_index=index,
    )

    photo = _get_photo_media(teacher.get("photo"))
    if photo is None:
        await message_obj.answer(
            text,
            reply_markup=get_teacher_card_keyboard(index, len(teachers)),
        )
        return

    try:
        await message_obj.answer_photo(
            photo=photo,
            caption=text,
            reply_markup=get_teacher_card_keyboard(index, len(teachers)),
        )
    except Exception:
        # Fallback when Telegram cannot serve stored file_id/path.
        await message_obj.answer(
            text,
            reply_markup=get_teacher_card_keyboard(index, len(teachers)),
        )


async def edit_teacher_card(
    callback: CallbackQuery, subject: str, index: int, state: FSMContext
):
    teachers = get_teacher_cards_for_subject(subject)
    teacher = teachers[index]

    text = (
        f"Преподаватель: {teacher['name']}\n"
        f"Предмет: {subject}\n\n"
        f"{teacher['description']}"
    )

    await state.update_data(
        selected_teacher_subject=subject,
        selected_teacher_index=index,
    )

    photo = _get_photo_media(teacher.get("photo"))
    if photo is None:
        await callback.message.edit_text(
            text,
            reply_markup=get_teacher_card_keyboard(index, len(teachers)),
        )
        return

    try:
        await callback.message.edit_media(
            media=InputMediaPhoto(media=photo, caption=text),
            reply_markup=get_teacher_card_keyboard(index, len(teachers)),
        )
    except Exception:
        await callback.message.edit_text(
            text,
            reply_markup=get_teacher_card_keyboard(index, len(teachers)),
        )


async def send_review_card(message_obj: Message, index: int, state: FSMContext):
    reviews = get_review_cards()

    if not reviews:
        await message_obj.answer("Отзывы пока не добавлены.")
        return

    review = reviews[index]
    total = len(reviews)

    links = review.get("links") or []
    links_text = "\n".join(f"{pos}. {link}" for pos, link in enumerate(links, start=1))
    caption = f"Отзыв {index + 1} из {total}\n\n{review.get('description', '')}".strip()
    if links_text:
        caption = f"{caption}\n\nСсылки:\n{links_text}"

    await state.update_data(selected_review_index=index)

    media_type = review.get("media_type")
    media_ref = review.get("media_file_id")
    media_local = review.get("media_local_path")

    # Try to send media if present using resolved media
    if media_type:
        try:
            media = resolve_review_media(media_type, media_local, media_ref)

            if media_type == "photo" and media:
                await message_obj.answer_photo(
                    photo=media,
                    caption=caption,
                    reply_markup=get_review_card_keyboard(index, total),
                )
                return
            elif media_type == "document" and media:
                await message_obj.answer_document(
                    document=media,
                    caption=caption,
                    reply_markup=get_review_card_keyboard(index, total),
                )
                return
        except Exception as e:
            import logging
            logging.error(f"Error showing review media: {e}, media_type='{media_type}'")

    # Fallback: send text only
    await message_obj.answer(
        caption,
        reply_markup=get_review_card_keyboard(index, total),
    )


async def edit_review_card(callback: CallbackQuery, index: int, state: FSMContext):
    reviews = get_review_cards()

    if not reviews:
        await callback.message.answer("Отзывы пока не добавлены.")
        return

    review = reviews[index]
    total = len(reviews)

    links = review.get("links") or []
    links_text = "\n".join(f"{pos}. {link}" for pos, link in enumerate(links, start=1))
    caption = f"Отзыв {index + 1} из {total}\n\n{review.get('description', '')}".strip()
    if links_text:
        caption = f"{caption}\n\nСсылки:\n{links_text}"

    await state.update_data(selected_review_index=index)

    media_type = review.get("media_type")
    media_ref = review.get("media_file_id")
    media_local = review.get("media_local_path")

    # Try to edit with media if present using resolved media
    if media_type:
        try:
            media = resolve_review_media(media_type, media_local, media_ref)

            if media_type == "photo" and media:
                await callback.message.edit_media(
                    media=InputMediaPhoto(media=media, caption=caption),
                    reply_markup=get_review_card_keyboard(index, total),
                )
                return
            elif media_type == "document" and media:
                await callback.message.edit_media(
                    media=InputMediaDocument(media=media, caption=caption),
                    reply_markup=get_review_card_keyboard(index, total),
                )
                return
        except Exception as e:
            import logging
            logging.error(f"Error editing review media: {e}, media_type='{media_type}'")

    # Fallback: edit text only
    await callback.message.edit_text(
        caption,
        reply_markup=get_review_card_keyboard(index, total),
    )
