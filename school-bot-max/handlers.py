"""MAX bot handlers.

The dispatcher calls these functions with parsed update data. All state is
kept in the shared `max_fsm_state` table (PostgreSQL) so the MAX bot has no
dependency on aiogram's FSM infrastructure.
"""

from __future__ import annotations

import logging
import re
import sys
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from aiogram import Bot as TelegramBot
from aiogram.types import BufferedInputFile

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import (
    APPLICATIONS_CHAT_ID,
    LESSON_PRICE,
    MAX_BOT_USERNAME,
    PACKAGE_PRICES,
    PAYMENT_ACCOUNT_HOLDER,
    PAYMENT_BANK_NAME,
    PAYMENT_BANK_NUMBER,
    PAYMENT_PHOTO_FILE_ID,
    PAYMENTS_CHAT_ID,
    TARIFF_PHOTO_FILE_ID,
    TG_BOT_TOKEN,
    TG_BOT_USERNAME,
)
from keyboards import (
    back_kb,
    back_menu_kb,
    cabinet_kb,
    class_kb,
    contact_method_kb,
    faq_back_kb,
    faq_kb,
    get_all_subject_names,
    goal_kb,
    lesson_type_kb,
    main_menu_kb,
    offers_kb,
    package_selection_kb,
    review_card_kb,
    subjects_kb,
    teacher_card_kb,
    teacher_choice_kb,
    teacher_subjects_kb,
    teachers_list_kb,
    user_type_kb,
)
from shared.database import (
    apply_promo_code_for_student,
    clear_max_fsm_state,
    consume_account_link_code,
    create_account_link_code,
    create_payment_request_max,
    find_students_by_max_id,
    find_students_by_telegram_id,
    get_active_invitee_discount_percent,
    get_active_promo_for_max_user,
    get_active_promo_for_student_id,
    get_active_review_cards,
    get_max_fsm_state,
    get_recent_attendance_for_student,
    get_recent_payment_history_by_student_id,
    get_student_directions,
    get_student_lesson_by_id,
    get_teacher_catalog_name_subject_pairs,
    link_max_to_student,
    set_max_fsm_state,
    try_auto_link_max_by_phone,
)
from shared.max_api import MaxApiClient, btn, keyboard
from states import (
    APP_CLASS,
    APP_COMMENT,
    APP_CONTACT_METHOD,
    APP_CONTACT_VALUE,
    APP_GOAL,
    APP_LESSON_TYPE,
    APP_NAME,
    APP_SUBJECTS,
    APP_TEACHER_CHOICE,
    APP_TEACHER_NAME,
    APP_USER_TYPE,
    DEBT_DIRECTION_CHOICE,
    DIRECTION_CHOICE,
    ENTER_PROMO,
    LINK_CODE,
    LINK_PHONE,
    MENU,
    PACKAGE_SELECTION,
    PAYMENT_PROOF,
    PAYMENT_TYPE_CHOICE,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _is_valid_phone(text: str) -> bool:
    cleaned = re.sub(r"[^\d+]", "", text.strip())
    if cleaned.startswith("+"):
        return cleaned[1:].isdigit() and 10 <= len(cleaned[1:]) <= 15
    return cleaned.isdigit() and 10 <= len(cleaned) <= 15


def _is_valid_tg_username(text: str) -> bool:
    return bool(re.fullmatch(r"@[A-Za-z0-9_]{5,32}", text.strip()))


def _format_payment_status(status: str) -> str:
    return {
        "pending":    "⏳ ожидает",
        "processing": "🔄 проверяется",
        "approved":   "✅ принята",
        "rejected":   "❌ отклонена",
        "expired":    "⌛ просрочена",
    }.get(status, status)


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


def _format_attendance_status(status: str) -> str:
    return {
        "present": "✅ был",
        "completed": "✅ был",
        "absent": "❌ пропуск",
        "missed": "❌ пропуск",
        "skipped": "❌ пропуск",
        "cancelled": "↩️ отменено",
    }.get(status, status or "—")


_payment_photo_url_cache: str | None = None
_tariff_photo_url_cache: str | None = None


async def _get_tariff_photo_url() -> str | None:
    """Download the tariff photo from Telegram once, cache locally, return public URL."""
    global _tariff_photo_url_cache
    if _tariff_photo_url_cache is not None:
        return _tariff_photo_url_cache or None
    if not TARIFF_PHOTO_FILE_ID or not TG_BOT_TOKEN:
        _tariff_photo_url_cache = ""
        return None
    try:
        assets_dir = ROOT_DIR / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        target_path = assets_dir / "tariff_photo.jpg"
        if not target_path.exists():
            tg_bot = TelegramBot(token=TG_BOT_TOKEN)
            tg_file = await tg_bot.get_file(TARIFF_PHOTO_FILE_ID)
            await tg_bot.download_file(tg_file.file_path, destination=target_path)
            await tg_bot.session.close()
        if target_path.exists():
            _tariff_photo_url_cache = f"{_SERVER_BASE_URL}/assets/tariff_photo.jpg"
            return _tariff_photo_url_cache
    except Exception as exc:
        logger.warning("Failed to download tariff photo: %s", exc)
    _tariff_photo_url_cache = ""
    return None


async def _get_payment_photo_url() -> str | None:
    """Download the payment banner from Telegram once, cache locally, return public URL."""
    global _payment_photo_url_cache
    if _payment_photo_url_cache is not None:
        return _payment_photo_url_cache or None
    if not PAYMENT_PHOTO_FILE_ID or not TG_BOT_TOKEN:
        _payment_photo_url_cache = ""
        return None
    try:
        assets_dir = ROOT_DIR / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        target_path = assets_dir / "payment_photo.jpg"
        if not target_path.exists():
            tg_bot = TelegramBot(token=TG_BOT_TOKEN)
            tg_file = await tg_bot.get_file(PAYMENT_PHOTO_FILE_ID)
            await tg_bot.download_file(tg_file.file_path, destination=target_path)
            await tg_bot.session.close()
        if target_path.exists():
            _payment_photo_url_cache = f"{_SERVER_BASE_URL}/assets/payment_photo.jpg"
            return _payment_photo_url_cache
    except Exception as exc:
        logger.warning("Failed to download payment photo: %s", exc)
    _payment_photo_url_cache = ""
    return None


def _build_cabinet_text(student_name: str, directions: list, payments: list, student_id: int | None = None, tg_linked: bool = False) -> str:
    positive = sum(d[3] for d in directions if d[3] > 0)
    debt = sum(-d[3] for d in directions if d[3] < 0)

    lines = [f"👤 {student_name}", ""]

    if debt > 0:
        lines.append(f"🔴 Долг: {debt} зан.   |   ✅ Баланс: {positive} зан.")
    else:
        lines.append(f"✅ Баланс: {positive} зан.")

    if tg_linked:
        lines.append("📱 Telegram: ✅ подключён")
    else:
        lines.append("📱 Telegram: ➖ не привязан")

    if student_id is not None:
        discount_percent = get_active_invitee_discount_percent(student_id)
        if discount_percent:
            lines.append(f"🎁 Реферальная скидка {discount_percent}% на первую оплату")
        promo = get_active_promo_for_student_id(student_id)
        if promo:
            _, code, dtype, dvalue, *_ = promo
            unit = "%" if dtype == "percent" else "₽"
            lines.append(f"🎟 Промокод {code} — скидка {int(float(dvalue))}{unit}")

    if directions:
        lines.extend(["", "📚 Направления"])
        for d in directions:
            _, teacher, subject, balance, _ = d
            if balance < 0:
                bal = f"⚠️ долг {-balance} зан."
            elif balance == 0:
                bal = "0 зан."
            else:
                bal = f"{balance} зан."
            lines.append(f"  • {subject} ({teacher}) — {bal}")
    else:
        lines.extend(["", "📚 Направления ещё не назначены."])

    if student_id is not None and directions:
        recent = get_recent_attendance_for_student(student_id, limit=3)
        if recent:
            lines.extend(["", "🗓 Последние занятия"])
            for entry in recent:
                date_view = _fmt_short_date((entry["lesson_date"] or "")[:10])
                lines.append(
                    f"  • {date_view} — {entry['subject_name']}: "
                    f"{_format_attendance_status(entry['status'])}"
                )

    if payments:
        lines.extend(["", "💳 Последние оплаты"])
        for payment in payments[:4]:
            _, status, _, created_at, _, lessons_added = payment[:6]
            date_view = _fmt_short_datetime(str(created_at) if created_at else "")
            status_label = _format_payment_status(status)
            lessons_str = f" +{lessons_added} зан." if lessons_added else ""
            lines.append(f"  • {date_view} — {status_label}{lessons_str}")

    return "\n".join(lines)

def _build_application_text(data: dict) -> str:
    teacher_text = data.get("teacher_choice", "—")
    if data.get("teacher_choice") == "Выбрать конкретного":
        teacher_text = data.get("teacher_name", "—")
    subjects_text = ", ".join(data.get("subjects", [])) or "—"
    return (
        "📌 Новая заявка (из MAX)\n\n"
        f"👤 Кто оставил: {data.get('user_type', '—')}\n"
        f"📝 Имя: {data.get('name', '—')}\n"
        f"🏫 Класс: {data.get('school_class', '—')}\n"
        f"🎯 Цель: {data.get('goal', '—')}\n"
        f"📚 Формат: {data.get('lesson_type', '—')}\n"
        f"📖 Предметы: {subjects_text}\n"
        f"👨‍🏫 Преподаватель: {teacher_text}\n"
        f"📞 Способ связи: {data.get('contact_method', '—')}\n"
        f"🔗 Контакт: {data.get('contact_value', '—')}\n"
        f"💬 Комментарий: {data.get('comment', '—')}"
    )


async def _reply(
    api: MaxApiClient, user_id: int, message_id: str | None, text: str, kb=None
) -> dict:
    """Edit message_id if available, otherwise send a new message."""
    if message_id:
        try:
            return await api.edit_message(message_id, text, kb)
        except Exception as exc:
            logger.debug("edit_message mid=%s failed: %s", message_id, exc)
    return await api.send_message(user_id, text, kb)


async def _reply_payment(
    api: MaxApiClient, user_id: int, message_id: str | None, text: str, kb=None
) -> dict:
    """Show payment details. If a payment photo is available, send it as a photo message."""
    photo_url = await _get_payment_photo_url()
    if photo_url:
        if message_id:
            try:
                await api.delete_message(message_id)
            except Exception:
                pass
        try:
            return await api.send_photo_url(user_id, photo_url, caption=text, attachments=kb or [])
        except Exception as exc:
            logger.warning("Payment photo send failed: %s", exc)
    return await _reply(api, user_id, message_id, text, kb)


async def _show_menu(
    api: MaxApiClient, user_id: int, data: dict | None = None, message_id: str | None = None
) -> None:
    set_max_fsm_state(user_id, MENU, data or {})
    await _reply(api, user_id, message_id, "📋 Выберите нужный раздел:", main_menu_kb())


_SERVER_BASE_URL = "http://151.243.176.132"


async def _send_teacher_card(
    api: MaxApiClient,
    user_id: int,
    message_id: str | None,
    subject: str,
    cards: list[dict],
    index: int,
) -> None:
    card = cards[index]
    name = (card.get("name") or "").strip()
    description = card.get("description") or "Описание будет добавлено позже."
    text = f"👨‍🏫 {name}\n📚 Предмет: {subject}\n\n{description}"
    kb = teacher_card_kb(index, len(cards))
    photo_path = (card.get("photo") or "").strip()
    if photo_path:
        photo_url = f"{_SERVER_BASE_URL}/{photo_path}"
        photo_att = {"type": "image", "payload": {"url": photo_url}}
        if message_id:
            try:
                await api.edit_message(message_id, text, [photo_att] + kb)
                return
            except Exception:
                await api.delete_message(message_id)
        try:
            await api.send_photo_url(user_id, photo_url, caption=text, attachments=kb)
            return
        except Exception as exc:
            logger.warning("Teacher photo send failed %s: %s", photo_url, exc)
    await _reply(api, user_id, message_id, text, kb)


async def _ensure_review_local_path(card: dict) -> str | None:
    """Return local path for a review photo, downloading from Telegram if needed."""
    local = (card.get("media_local_path") or "").strip()
    if local:
        return local
    file_id = card.get("media_file_id")
    review_id = card.get("id")
    if not file_id or not review_id:
        return None
    try:
        reviews_dir = ROOT_DIR / "assets" / "reviews"
        reviews_dir.mkdir(parents=True, exist_ok=True)
        filename = f"review_{int(time.time())}_{uuid4().hex[:8]}.jpg"
        target_path = reviews_dir / filename
        tg_bot = TelegramBot(token=TG_BOT_TOKEN)
        tg_file = await tg_bot.get_file(file_id)
        await tg_bot.download_file(tg_file.file_path, destination=target_path)
        await tg_bot.session.close()
        if target_path.exists():
            local_path = f"assets/reviews/{filename}"
            from shared.database import get_connection
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("UPDATE review_cards SET media_local_path = ? WHERE id = ?", (local_path, review_id))
            conn.commit()
            conn.close()
            card["media_local_path"] = local_path
            return local_path
    except Exception as exc:
        logger.warning("Failed to download review photo file_id=%s: %s", file_id, exc)
    return None


async def _send_review_card(
    api: MaxApiClient,
    user_id: int,
    message_id: str | None,
    cards: list[dict],
    index: int,
) -> None:
    card = cards[index]
    total = len(cards)
    links = card.get("links") or []
    links_text = "\n".join(f"{pos}. {link}" for pos, link in enumerate(links, start=1))
    caption = f"Отзыв {index + 1} из {total}\n\n{card.get('description', '')}".strip()
    if links_text:
        caption = f"{caption}\n\nСсылки:\n{links_text}"
    kb = review_card_kb(index, total)
    media_type = card.get("media_type")
    if media_type in ("photo", "image"):
        local = await _ensure_review_local_path(card)
        if local:
            photo_url = f"{_SERVER_BASE_URL}/{local}"
            if message_id:
                try:
                    await api.edit_message(message_id, caption, [{"type": "image", "payload": {"url": photo_url}}] + kb)
                    return
                except Exception:
                    await api.delete_message(message_id)
            try:
                await api.send_photo_url(user_id, photo_url, caption=caption, attachments=kb)
                return
            except Exception as exc:
                logger.warning("Review photo send failed %s: %s", photo_url, exc)
    await _reply(api, user_id, message_id, caption, kb)


# ──────────────────────────────────────────────────────────────────────────────
# Entry points called by bot.py dispatcher
# ──────────────────────────────────────────────────────────────────────────────

async def handle_bot_started(api: MaxApiClient, user_id: int, name: str, username: str | None, payload: str = "") -> None:
    """Fired when the user taps 'Start' for the first time."""
    clear_max_fsm_state(user_id)

    # Track MAX referral if payload contains ref info
    if payload and payload.startswith("ref_"):
        try:
            inviter_max_id = int(payload[4:])
            if inviter_max_id != user_id:
                from shared.database import capture_referral_max
                capture_referral_max(inviter_max_id, user_id)
        except (ValueError, TypeError):
            pass

    # Check if already linked
    students = find_students_by_max_id(user_id)
    if students:
        student_name = students[0][1]
        set_max_fsm_state(user_id, MENU)
        await api.send_message(
            user_id,
            f"👋 Добро пожаловать, {student_name}!\n\nВаш личный кабинет уже привязан.",
            main_menu_kb(),
        )
        return

    set_max_fsm_state(user_id, LINK_PHONE)
    await api.send_message(
        user_id,
        "👋 Добро пожаловать в бот онлайн школы «Интеграл»!\n\n"
        "Для привязки вашего личного кабинета введите номер телефона, "
        "который вы указывали при записи (например: +79001234567).\n\n"
        "Если у вас нет кабинета в нашей системе — нажмите кнопку ниже и оставьте заявку через меню.",
        keyboard([btn("Пропустить →", "back_to_menu")]),
    )


async def handle_text(
    api: MaxApiClient,
    user_id: int,
    username: str | None,
    name: str,
    text: str,
    user_message_mid: str | None = None,
) -> None:
    state, data = get_max_fsm_state(user_id)

    if not state:
        state = MENU
        data = {}

    text = text.strip()

    # ── /start / /menu commands ──────────────────────────────────────────
    if text in ("/start", "/menu") or state == MENU:
        await handle_bot_started(api, user_id, name, username)
        return

    if text == "/skip" and state == LINK_PHONE:
        await _show_menu(api, user_id)
        return

    # ── /link CODE ────────────────────────────────────────────────────────
    if text.lower().startswith("/link"):
        parts = text.split(maxsplit=1)
        code = parts[1].strip() if len(parts) > 1 else ""
        await _handle_link_code(api, user_id, username, code)
        return

    # ── state machine ─────────────────────────────────────────────────────
    if state == LINK_PHONE:
        await _handle_link_phone_text(api, user_id, username, text)

    elif state == LINK_CODE:
        await _handle_link_code(api, user_id, username, text)

    elif state == APP_USER_TYPE:
        low = text.lower()
        if "родител" in low:
            data["user_type"] = "Родитель"
        elif "учен" in low:
            data["user_type"] = "Ученик"
        else:
            await api.send_message(user_id, "❓ Напишите: ученик или родитель.", user_type_kb())
            return
        set_max_fsm_state(user_id, APP_NAME, data)
        await api.send_message(user_id, "📝 Напишите, как к вам обращаться (имя или имя + фамилия):", back_kb())

    elif state == APP_NAME:
        data["name"] = text
        set_max_fsm_state(user_id, APP_CLASS, data)
        await api.send_message(user_id, "🏫 Выберите класс:", class_kb())

    elif state == APP_COMMENT:
        data["comment"] = text if text.lower() not in ("-", "нет") else "-"
        await _submit_application(api, user_id, data)

    elif state == APP_CONTACT_VALUE:
        await _handle_contact_value(api, user_id, data, text)

    elif state == APP_TEACHER_NAME:
        data["teacher_name"] = text
        set_max_fsm_state(user_id, APP_CONTACT_METHOD, data)
        await api.send_message(user_id, "📞 Как с вами связаться?", contact_method_kb())

    elif state == ENTER_PROMO:
        code = text.strip().upper()
        students = find_students_by_max_id(user_id)
        if not students or not code:
            set_max_fsm_state(user_id, MENU)
            await api.send_message(user_id, "⚠️ Не удалось применить промокод. Попробуйте через меню.", main_menu_kb())
            return
        student_id = students[0][0]
        ok, result = apply_promo_code_for_student(student_id, code)
        if ok:
            parts = result.split(":")
            dtype, dvalue = parts[0], parts[1]
            unit = "%" if dtype == "percent" else "₽"
            msg = (
                f"✅ Промокод {code} активирован!\n"
                f"Скидка {int(float(dvalue))}{unit} будет применена при следующей оплате."
            )
        elif result == "not_found":
            msg = f"❌ Промокод {code} не найден. Проверьте правильность написания."
        elif result == "inactive":
            msg = f"❌ Промокод {code} деактивирован."
        elif result == "expired":
            msg = f"❌ Срок действия промокода {code} истёк."
        elif result == "limit_reached":
            msg = f"❌ Промокод {code} исчерпал лимит использований."
        elif result == "already_used":
            msg = f"❌ Вы уже использовали промокод {code}."
        elif result == "already_assigned":
            msg = f"✅ Промокод {code} уже применён к вашему аккаунту."
        else:
            msg = "⚠️ Произошла ошибка. Попробуйте позже или обратитесь к администратору."
        set_max_fsm_state(user_id, MENU)
        from shared.max_api import btn as _btn, keyboard as _keyboard
        kb = _keyboard([_btn("💳 Оплатить занятия", "menu_paid")], [_btn("← В личный кабинет", "menu_cabinet")])
        await api.send_message(user_id, msg, kb)

    elif state == PAYMENT_PROOF:
        await api.send_message(
            user_id,
            "📸 Пожалуйста, отправьте фото или PDF-файл чека об оплате.",
            keyboard([btn("← В меню", "back_to_menu")]),
        )

    else:
        await _show_menu(api, user_id, data)


async def handle_photo(
    api: MaxApiClient,
    user_id: int,
    username: str | None,
    name: str,
    photo_url: str,
    caption: str | None,
) -> None:
    state, data = get_max_fsm_state(user_id)
    if state != PAYMENT_PROOF:
        await api.send_message(
            user_id,
            "ℹ️ Чек принимается только в разделе «💳 Оплатить занятия».",
            main_menu_kb(),
        )
        return
    await _process_payment_file(api, user_id, username, name, photo_url, "photo", caption)


async def handle_file(
    api: MaxApiClient,
    user_id: int,
    username: str | None,
    name: str,
    file_url: str,
    filename: str,
    caption: str | None,
    mime_type: str = "",
) -> None:
    state, data = get_max_fsm_state(user_id)
    if state != PAYMENT_PROOF:
        await api.send_message(
            user_id,
            "ℹ️ Чек принимается только в разделе «💳 Оплатить занятия».",
            main_menu_kb(),
        )
        return
    fname = (filename or "").lower()
    mime = (mime_type or "").lower()
    if not fname.endswith(".pdf") and "pdf" not in mime:
        await api.send_message(user_id, "❓ Пожалуйста, отправьте PDF-файл чека.", keyboard([btn("← В меню", "back_to_menu")]))
        return
    await _process_payment_file(api, user_id, username, name, file_url, "pdf", caption)


async def handle_callback(
    api: MaxApiClient,
    callback_id: str,
    user_id: int,
    username: str | None,
    name: str,
    payload: str,
    message_id: str | None = None,
) -> None:
    state, data = get_max_fsm_state(user_id)
    if not state:
        state = MENU
        data = {}

    try:
        await _dispatch_callback(api, callback_id, user_id, username, name, payload, state, data, message_id)
    except Exception:
        logger.exception("callback dispatch error user=%s payload=%s", user_id, payload)
        await api.answer_callback(callback_id)


# ──────────────────────────────────────────────────────────────────────────────
# Internal handlers
# ──────────────────────────────────────────────────────────────────────────────

async def _handle_link_phone_text(
    api: MaxApiClient, user_id: int, username: str | None, text: str
) -> None:
    if not _is_valid_phone(text):
        await api.send_message(
            user_id,
            "❌ Формат номера не распознан. Попробуйте ещё раз (например: +79001234567).",
            keyboard([btn("Пропустить →", "back_to_menu")]),
        )
        return

    student = try_auto_link_max_by_phone(text, user_id, username)
    if student:
        student_name = student[1]
        set_max_fsm_state(user_id, MENU)
        await api.send_message(
            user_id,
            f"✅ Отлично! Ваш кабинет привязан.\n\nДобро пожаловать, {student_name}!",
            main_menu_kb(),
        )
        return

    set_max_fsm_state(user_id, LINK_CODE)
    await api.send_message(
        user_id,
        "🔍 Телефон не найден в базе.\n\n"
        "Если у вас уже есть кабинет в Telegram-боте школы — "
        "откройте там раздел 👤 Личный кабинет и нажмите «🔗 Подключить MAX». "
        "Вам придёт 6-значный код — введите его сюда.",
        keyboard([btn("Пропустить →", "back_to_menu")]),
    )


async def _handle_link_code(
    api: MaxApiClient, user_id: int, username: str | None, code: str
) -> None:
    if code.lower() == "/skip" or not code:
        await _show_menu(api, user_id)
        return

    telegram_id = consume_account_link_code(code)
    if not telegram_id:
        await api.send_message(
            user_id,
            "❌ Код недействителен или устарел. Запросите новый код в Telegram-боте.",
            keyboard([btn("Пропустить →", "back_to_menu")]),
        )
        return

    # Link all students of this TG user to this MAX account
    students = find_students_by_telegram_id(telegram_id)
    if students:
        for student in students:
            link_max_to_student(student[0], user_id, username)
        student_name = students[0][1]
        set_max_fsm_state(user_id, MENU)
        await api.send_message(
            user_id,
            f"✅ Аккаунты связаны! Добро пожаловать, {student_name}!",
            main_menu_kb(),
        )
    else:
        set_max_fsm_state(user_id, MENU)
        await api.send_message(
            user_id,
            "✅ Код принят. Ваш Telegram-аккаунт связан с MAX.\n"
            "(Карточка ученика будет привязана администратором.)",
            main_menu_kb(),
        )


async def _show_debt_payment_max(
    api: MaxApiClient, user_id: int, message_id: str | None,
    data: dict, direction_id: int, debt_lessons: int,
):
    """Show debt payment details screen for MAX."""
    data["payment_type"] = "pay_debt"
    data["payment_type_label"] = "💸 Погашение долга"
    set_max_fsm_state(user_id, PAYMENT_PROOF, data)
    price_block = ""
    if LESSON_PRICE and debt_lessons > 0:
        base = LESSON_PRICE * debt_lessons
        price_block = f"\n💵 Сумма долга: {debt_lessons} × {LESSON_PRICE}₽ = {base}₽\n"
    await _reply_payment(
        api, user_id, message_id,
        f"💰 Тип оплаты: 💸 Погашение долга"
        f"{price_block}\n"
        f"💳 РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ\n\n"
        f"🏦 Номер счёта: {PAYMENT_BANK_NUMBER}\n"
        f"🏢 Банк: {PAYMENT_BANK_NAME}\n"
        f"👤 Владелец: {PAYMENT_ACCOUNT_HOLDER}\n\n"
        "📝 В комментарии к переводу укажите имя ученика.\n\n"
        "📸 После оплаты отправьте фото или PDF-файл чека в этот чат.",
        keyboard([btn("← В меню", "back_to_menu")]),
    )


async def _process_payment_file(
    api: MaxApiClient,
    user_id: int,
    username: str | None,
    name: str,
    file_url: str,
    file_type: str,
    caption: str | None,
) -> None:
    try:
        file_bytes = await api.download_bytes(file_url)
    except Exception as exc:
        logger.error("Failed to download file from MAX: %s", exc)
        await api.send_message(user_id, "⚠️ Не удалось скачать файл. Попробуйте ещё раз.", keyboard([btn("← В меню", "back_to_menu")]))
        return

    try:
        tg_bot = TelegramBot(token=TG_BOT_TOKEN)
        ext = "pdf" if file_type == "pdf" else "jpg"
        input_file = BufferedInputFile(file_bytes, filename=f"receipt.{ext}")

        state, fsm_data = get_max_fsm_state(user_id)
        fsm_data = fsm_data or {}
        payment_type_label = fsm_data.get("payment_type_label", "")
        payment_type = fsm_data.get("payment_type", "")
        skip_promo = fsm_data.get("skip_promo", False)

        # Determine if promo was actually applied
        promo = get_active_promo_for_max_user(user_id)
        promo_applicable = fsm_data.get("promo_applicable", True)
        promo_applied = promo if (promo and not skip_promo and payment_type != "pay_debt" and promo_applicable) else None
        promo_code_id_used = promo_applied[0] if promo_applied else None

        payment_request_id = create_payment_request_max(
            max_user_id=user_id,
            max_username=username,
            max_full_name=name,
            caption_text=caption,
            file_id="pending",
            file_type=file_type,
            promo_code_id_used=promo_code_id_used,
        )

        promo_line = ""
        if promo_applied:
            _, code, dtype, dvalue, *_ = promo_applied
            unit = "%" if dtype == "percent" else "₽"
            promo_line = f"\n🎟 Промокод: {code} (-{int(dvalue)}{unit})"

        username_line = f"@{username}" if username else "не указан"

        direction_line = ""
        selected_dir_id = fsm_data.get("selected_direction_id")
        if selected_dir_id:
            dir_row = get_student_lesson_by_id(selected_dir_id)
            if dir_row:
                subject_name = dir_row[3]
                teacher_name = dir_row[7]
                direction_line = f"\n📚 Направление: {subject_name} — {teacher_name}"

        payment_caption = (
            f"💳 Оплата #{payment_request_id}\n\n"
            f"📌 Статус: ⏳ Ожидает проверки\n"
            f"📱 Платформа: MAX\n"
            f"👤 Имя (MAX): {name}\n"
            f"🔗 Username: {username_line}\n"
            f"🆔 MAX ID: {user_id}"
            + (f"\n💰 Тип оплаты: {payment_type_label}" if payment_type_label else "")
            + direction_line
            + promo_line
        )

        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        tg_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Подтвердить",
                        callback_data=f"payment_approve_{payment_request_id}",
                    ),
                    InlineKeyboardButton(
                        text="❌ Отклонить",
                        callback_data=f"payment_reject_{payment_request_id}",
                    ),
                ]
            ]
        )

        if file_type == "pdf":
            sent = await tg_bot.send_document(
                PAYMENTS_CHAT_ID,
                document=input_file,
                caption=payment_caption,
                reply_markup=tg_keyboard,
            )
        else:
            sent = await tg_bot.send_photo(
                PAYMENTS_CHAT_ID,
                photo=input_file,
                caption=payment_caption,
                reply_markup=tg_keyboard,
            )

        # Update the payment record with the real TG file_id so admin bot can re-use it
        real_file_id = (
            sent.photo[-1].file_id if sent.photo else
            (sent.document.file_id if sent.document else "uploaded")
        )
        from shared.database import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE payment_requests SET file_id = ? WHERE id = ?",
            (real_file_id, payment_request_id),
        )
        conn.commit()
        conn.close()

        await tg_bot.session.close()

    except Exception as exc:
        logger.exception("Failed to forward payment to TG: %s", exc)
        await api.send_message(
            user_id,
            "⚠️ Не удалось отправить чек. Попробуйте ещё раз или свяжитесь с администратором.",
        )
        return

    await api.send_message(
        user_id,
        "✅ Чек отправлен на проверку. После подтверждения занятия будут начислены на ваш баланс.",
        main_menu_kb(),
    )
    set_max_fsm_state(user_id, MENU)


async def _dispatch_callback(
    api: MaxApiClient,
    callback_id: str,
    user_id: int,
    username: str | None,
    name: str,
    payload: str,
    state: str,
    data: dict,
    message_id: str | None = None,
) -> None:
    await api.answer_callback(callback_id)

    if payload == "back_to_menu":
        await _show_menu(api, user_id, {k: v for k, v in data.items() if k == "user_type"}, message_id=message_id)
        return

    if payload == "back_step":
        await _handle_back(api, user_id, state, data, message_id)
        return

    if payload == "menu_signup":
        data_new = {k: v for k, v in data.items() if k == "user_type"}
        set_max_fsm_state(user_id, APP_USER_TYPE, data_new)
        await _reply(api, user_id, message_id, "Пожалуйста, укажите: вы ученик или родитель?", user_type_kb())
        return

    if payload in ("user_student", "user_parent"):
        data["user_type"] = "Ученик" if payload == "user_student" else "Родитель"
        set_max_fsm_state(user_id, APP_NAME, data)
        await _reply(api, user_id, message_id, "📝 Напишите, как к вам обращаться:", back_kb())
        return

    if payload == "menu_cabinet":
        students = find_students_by_max_id(user_id)
        if not students:
            await _reply(
                api, user_id, message_id,
                "❌ Мы пока не нашли вас в базе учеников.\n"
                "Введите номер телефона для привязки или обратитесь к администратору.",
                back_menu_kb(),
            )
            return
        student_id, student_name, telegram_id, _ = students[0]
        directions = get_student_directions(student_id)
        payments = get_recent_payment_history_by_student_id(student_id, limit=4)
        text = _build_cabinet_text(student_name, directions, payments, student_id=student_id, tg_linked=bool(telegram_id))
        await _reply(api, user_id, message_id, text, cabinet_kb(tg_linked=bool(telegram_id)))
        return

    if payload == "enter_promo":
        students = find_students_by_max_id(user_id)
        if not students:
            await _reply(api, user_id, message_id, "❌ Вы не зарегистрированы в системе. Обратитесь к администратору.", back_menu_kb())
            return
        promo = get_active_promo_for_max_user(user_id)
        if promo:
            _, code, dtype, dvalue, *_ = promo
            unit = "%" if dtype == "percent" else "₽"
            await _reply(
                api, user_id, message_id,
                f"✅ У вас уже активен промокод {code} (скидка {int(dvalue)}{unit}).\n\n"
                "Для замены обратитесь к администратору.",
                back_menu_kb(),
            )
            return
        set_max_fsm_state(user_id, ENTER_PROMO, data)
        await _reply(api, user_id, message_id, "🎟 Введите промокод:\n\nНапишите код в следующем сообщении.", back_menu_kb())
        return

    if payload == "link_tg":
        students = find_students_by_max_id(user_id)
        if students and students[0][2]:  # telegram_id already set
            await _reply(
                api, user_id, message_id,
                "✅ Ваш Telegram-аккаунт уже подключён.\n"
                "Если нужно изменить — обратитесь к администратору.",
                back_menu_kb(),
            )
            return
        tg_bot_username = TG_BOT_USERNAME
        set_max_fsm_state(user_id, LINK_CODE, data)
        await _reply(
            api, user_id, message_id,
            "🔗 Связка с Telegram\n\n"
            "1. Откройте Telegram-бот школы\n"
            f"2. Перейдите в 👤 Личный кабинет\n"
            "3. Нажмите «🔗 Подключить MAX»\n"
            "4. Скопируйте 6-значный код и введите его здесь\n\n"
            f"👉 Бот в Telegram: @{tg_bot_username}",
            back_menu_kb(),
        )
        return

    if payload == "menu_paid":
        students = find_students_by_max_id(user_id)
        if not students:
            await _reply(api, user_id, message_id, "❌ Вы не зарегистрированы в системе.", back_menu_kb())
            return
        student_id = students[0][0]
        directions = get_student_directions(student_id)
        if not directions:
            await _reply(api, user_id, message_id, "❌ У вас нет направлений для оплаты.", back_menu_kb())
            return
        debt_directions = [d for d in directions if d[3] < 0]
        if debt_directions:
            # Go to debt payment directly
            if len(debt_directions) == 1:
                d = debt_directions[0]
                data["selected_direction_id"] = d[0]
                data["skip_promo"] = False
                await _show_debt_payment_max(api, user_id, message_id, data, d[0], abs(d[3]))
            else:
                data["selected_direction_id"] = None
                data["skip_promo"] = False
                set_max_fsm_state(user_id, DEBT_DIRECTION_CHOICE, data)
                rows = []
                for d in debt_directions:
                    direction_id, teacher_name, subject_name, lesson_balance, _ = d
                    debt_count = abs(lesson_balance)
                    suffix = 'ие' if debt_count == 1 else 'ия' if 2 <= debt_count <= 4 else 'ий'
                    rows.append([btn(f"📚 {subject_name} — {teacher_name} (долг {debt_count} занят{suffix})", f"debt_dir_{direction_id}")])
                rows.append([btn("← В меню", "back_to_menu")])
                await _reply(api, user_id, message_id, "💸 По какому направлению погасить долг?", keyboard(*rows))
            return
        # No debt — direction picker if multiple
        if len(directions) > 1:
            data["selected_direction_id"] = None
            data["skip_promo"] = False
            set_max_fsm_state(user_id, DIRECTION_CHOICE, data)
            rows = []
            for d in directions:
                direction_id, teacher_name, subject_name, lesson_balance, _ = d
                bal_str = f" (долг {abs(lesson_balance)} зан.)" if lesson_balance < 0 else f" ({lesson_balance} зан.)"
                rows.append([btn(f"📚 {subject_name} — {teacher_name}{bal_str}", f"dir_{direction_id}")])
            rows.append([btn("← В меню", "back_to_menu")])
            await _reply(api, user_id, message_id, "📚 Выберите направление для оплаты:", keyboard(*rows))
            return
        # Single direction
        data["selected_direction_id"] = directions[0][0]
        data["skip_promo"] = False
        set_max_fsm_state(user_id, PAYMENT_TYPE_CHOICE, data)
        promo = get_active_promo_for_max_user(user_id)
        promo_hint = ""
        if promo:
            _, code, dtype, dvalue, atp, *_ = promo
            unit = "%" if dtype == "percent" else "₽"
            scope_map = {0: "разовые занятия", 1: "пакеты занятий", 2: "разовые и пакеты"}
            promo_hint = f"\n🎟 Промокод {code} (-{int(float(dvalue))}{unit}) — {scope_map.get(int(atp or 0), 'занятия')}"
        await _reply(api, user_id, message_id, f"Выберите вариант оплаты:{promo_hint}", keyboard(
            [btn("✨ Разовая оплата", "pay_single")],
            [btn("📦 Выбрать пакет", "pay_package")],
            [btn("← В меню", "back_to_menu")],
        ))
        return

    if payload in ("pay_debt", "pay_single"):
        students = find_students_by_max_id(user_id)
        if not students:
            await _reply(api, user_id, message_id, "❌ Вы не зарегистрированы в системе.", back_menu_kb())
            return
        student_id = students[0][0]
        selected_direction_id = data.get("selected_direction_id")
        skip_promo = data.get("skip_promo", False)
        type_labels = {
            "pay_debt": "💸 Погашение долга",
            "pay_single": "✨ Разовое занятие",
        }
        payment_type_label = type_labels[payload]
        promo = None if skip_promo else get_active_promo_for_max_user(user_id)

        price_block = ""
        promo_not_applicable_note = ""
        if LESSON_PRICE:
            if payload == "pay_single":
                dtype_p = dvalue_p = None
                if promo:
                    _, promo_code_str, dtype_p, dvalue_p, atp, *_ = promo
                    dvalue_p = float(dvalue_p)
                    applies_to_pkg = int(atp or 0) in (1, 2)
                    if applies_to_pkg:
                        promo = None  # package-only promo, don't apply to single
                        dtype_p = None
                        promo_not_applicable_note = f"\n🎟 Промокод {promo_code_str} применяется только к пакетам занятий\n"
                base = LESSON_PRICE
                if dtype_p == "fixed_rub":
                    after = max(0, base - int(dvalue_p))
                    price_block = f"\n💵 Стоимость: {base}₽\n🎟 Скидка: -{int(dvalue_p)}₽\n✅ К оплате: {after}₽\n"
                elif dtype_p == "percent":
                    after = int(base * (1 - dvalue_p / 100))
                    price_block = f"\n💵 Стоимость: {base}₽\n🎟 Скидка: -{int(dvalue_p)}%\n✅ К оплате: {after}₽\n"
                else:
                    price_block = f"\n💵 Стоимость: {base}₽\n"
            elif payload == "pay_debt":
                directions = get_student_directions(student_id)
                if selected_direction_id:
                    debt_dir = next((d for d in directions if d[0] == selected_direction_id and d[3] < 0), None)
                    debt_lessons = abs(debt_dir[3]) if debt_dir else 0
                else:
                    debt_lessons = sum(abs(d[3]) for d in directions if d[3] < 0)
                if debt_lessons > 0:
                    base = LESSON_PRICE * debt_lessons
                    price_block = f"\n💵 Сумма долга: {debt_lessons} × {LESSON_PRICE}₽ = {base}₽\n"

        promo_block = ""
        if promo and payload != "pay_debt":
            _, code, dtype, dvalue, *_ = promo
            unit = "%" if dtype == "percent" else "₽"
            promo_block = f"\n🎟 Применён промокод {code} ({int(float(dvalue))}{unit})\n"

        active_promo = get_active_promo_for_max_user(user_id)
        # promo may have been set to None if it doesn't apply to this payment type
        has_promo = bool(promo) and payload != "pay_debt"
        if has_promo and not skip_promo:
            payment_kb = keyboard(
                [btn("🚫 Оплатить без промокода", "pay_skip_promo")],
                [btn("← В меню", "back_to_menu")],
            )
        elif not active_promo and not skip_promo and payload != "pay_debt":
            payment_kb = keyboard(
                [btn("🎟 Ввести промокод", "enter_promo")],
                [btn("← В меню", "back_to_menu")],
            )
        else:
            payment_kb = keyboard([btn("← В меню", "back_to_menu")])

        data["payment_type"] = payload
        data["payment_type_label"] = payment_type_label
        set_max_fsm_state(user_id, PAYMENT_PROOF, data)
        await _reply_payment(
            api, user_id, message_id,
            f"💰 Тип оплаты: {payment_type_label}"
            f"{price_block}\n"
            f"💳 РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ\n\n"
            f"🏦 Номер счёта: {PAYMENT_BANK_NUMBER}\n"
            f"🏢 Банк: {PAYMENT_BANK_NAME}\n"
            f"👤 Владелец: {PAYMENT_ACCOUNT_HOLDER}\n\n"
            "📝 В комментарии к переводу укажите имя ученика.\n"
            f"{promo_not_applicable_note}"
            f"{promo_block}\n"
            "📸 После оплаты отправьте фото или PDF-файл чека в этот чат.",
            payment_kb,
        )
        return

    if payload.startswith("dir_") and state == DIRECTION_CHOICE:
        try:
            direction_id = int(payload.split("dir_", 1)[1])
        except (ValueError, IndexError):
            await _reply(api, user_id, message_id, "Ошибка выбора направления.", back_menu_kb())
            return
        data["selected_direction_id"] = direction_id
        data["skip_promo"] = False
        set_max_fsm_state(user_id, PAYMENT_TYPE_CHOICE, data)
        promo = get_active_promo_for_max_user(user_id)
        promo_hint = ""
        if promo:
            _, code, dtype, dvalue, atp, *_ = promo
            unit = "%" if dtype == "percent" else "₽"
            scope_map = {0: "разовые занятия", 1: "пакеты занятий", 2: "разовые и пакеты"}
            promo_hint = f"\n🎟 Промокод {code} (-{int(float(dvalue))}{unit}) — {scope_map.get(int(atp or 0), 'занятия')}"
        await _reply(api, user_id, message_id, f"Выберите вариант оплаты:{promo_hint}", keyboard(
            [btn("✨ Разовая оплата", "pay_single")],
            [btn("📦 Выбрать пакет", "pay_package")],
            [btn("← В меню", "back_to_menu")],
        ))
        return

    if payload.startswith("debt_dir_") and state == DEBT_DIRECTION_CHOICE:
        try:
            direction_id = int(payload.split("debt_dir_", 1)[1])
        except (ValueError, IndexError):
            await _reply(api, user_id, message_id, "Ошибка выбора направления.", back_menu_kb())
            return
        students = find_students_by_max_id(user_id)
        if not students:
            await _reply(api, user_id, message_id, "❌ Вы не зарегистрированы.", back_menu_kb())
            return
        directions = get_student_directions(students[0][0])
        debt_dir = next((d for d in directions if d[0] == direction_id and d[3] < 0), None)
        if not debt_dir:
            await _reply(api, user_id, message_id, "❌ Направление не найдено или долга нет.", back_menu_kb())
            return
        data["selected_direction_id"] = direction_id
        data["skip_promo"] = False
        await _show_debt_payment_max(api, user_id, message_id, data, direction_id, abs(debt_dir[3]))
        return

    if payload == "pay_skip_promo" and state == PAYMENT_PROOF:
        data["skip_promo"] = True
        payment_type = data.get("payment_type", "pay_single")
        set_max_fsm_state(user_id, PAYMENT_PROOF, data)
        # Re-dispatch to re-render payment details without promo
        await _dispatch_callback(api, callback_id, user_id, username, name, payment_type, PAYMENT_TYPE_CHOICE, data, message_id)
        return

    if payload == "pay_package":
        students = find_students_by_max_id(user_id)
        if not students:
            await _reply(api, user_id, message_id, "❌ Вы не зарегистрированы в системе.", back_menu_kb())
            return
        if not PACKAGE_PRICES:
            # No packages configured — treat as before
            data["payment_type_label"] = "📦 Пакет занятий"
            set_max_fsm_state(user_id, PAYMENT_PROOF, data)
            await _reply(
                api, user_id, message_id,
                "💰 Тип оплаты: 📦 Пакет занятий\n\n"
                f"💳 РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ\n\n"
                f"🏦 Номер счёта: {PAYMENT_BANK_NUMBER}\n"
                f"🏢 Банк: {PAYMENT_BANK_NAME}\n"
                f"👤 Владелец: {PAYMENT_ACCOUNT_HOLDER}\n\n"
                "📸 После оплаты отправьте фото или PDF-файл чека в этот чат.",
            )
            return
        promo = get_active_promo_for_max_user(user_id)
        promo_note = ""
        if promo:
            _, code, dtype, dvalue, applies_to_packages, *_ = promo
            unit = "%" if dtype == "percent" else "₽"
            applies_to_pkg = int(applies_to_packages or 0) in (1, 2)
            if applies_to_pkg:
                promo_note = f"\n🎟 Промокод {code} (-{int(float(dvalue))}{unit}) применяется к пакетам."
            else:
                promo_note = f"\n🎟 Промокод {code} не применяется к пакетам."
                promo = None  # don't apply discount in keyboard prices
        set_max_fsm_state(user_id, PACKAGE_SELECTION, data)
        tariff_photo_url = await _get_tariff_photo_url()
        package_text = f"📦 Выбор пакета{promo_note}\n\nВыберите количество занятий:"
        package_kb = package_selection_kb(PACKAGE_PRICES, promo)
        if tariff_photo_url:
            if message_id:
                try:
                    await api.delete_message(message_id)
                except Exception:
                    pass
            try:
                await api.send_photo_url(user_id, tariff_photo_url, caption=package_text, attachments=package_kb)
                return
            except Exception as exc:
                logger.warning("Tariff photo send failed: %s", exc)
        await _reply(api, user_id, message_id, package_text, package_kb)
        return

    if payload.startswith("pay_package_"):
        try:
            lessons = int(payload.split("pay_package_", 1)[1])
        except (ValueError, IndexError):
            await _reply(api, user_id, message_id, "Ошибка выбора пакета.", back_menu_kb())
            return
        price = PACKAGE_PRICES.get(lessons)
        if not price:
            await _reply(api, user_id, message_id, "Пакет не найден.", back_menu_kb())
            return
        students = find_students_by_max_id(user_id)
        if not students:
            await _reply(api, user_id, message_id, "❌ Вы не зарегистрированы в системе.", back_menu_kb())
            return
        promo = get_active_promo_for_max_user(user_id)
        payment_type_label = f"📦 Пакет {lessons} занятий"

        # Check if promo applies to packages
        promo_not_applicable_note = ""
        if promo:
            _, _, _, _, applies_to_packages, *_ = promo
            applies_to_pkg = int(applies_to_packages or 0) in (1, 2)
            if not applies_to_pkg:
                _, promo_code_str, _, _, _, *_ = promo
                promo_not_applicable_note = f"\n🎟 Промокод {promo_code_str} применяется только к разовым занятиям\n"
                promo = None

        # Price calculation
        price_block = f"\n💵 Стоимость пакета: {price}₽\n"
        promo_block = ""
        if promo:
            _, code, dtype, dvalue, *_ = promo
            dvalue_f = float(dvalue)
            unit = "%" if dtype == "percent" else "₽"
            if dtype == "fixed_rub":
                after = max(0, price - int(dvalue_f))
                price_block = f"\n💵 Стоимость пакета: {price}₽\n🎟 Скидка: -{int(dvalue_f)}₽\n✅ К оплате: {after}₽\n"
                promo_block = f"\n🎟 Применён промокод {code} ({int(dvalue_f)}{unit})\n"
            elif dtype == "percent":
                after = int(price * (1 - dvalue_f / 100))
                price_block = f"\n💵 Стоимость пакета: {price}₽\n🎟 Скидка: -{int(dvalue_f)}%\n✅ К оплате: {after}₽\n"
                promo_block = f"\n🎟 Применён промокод {code} ({int(dvalue_f)}{unit})\n"

        data["payment_type_label"] = payment_type_label
        data["payment_type"] = f"pay_package_{lessons}"
        data["promo_applicable"] = promo is not None
        set_max_fsm_state(user_id, PAYMENT_PROOF, data)
        active_promo = get_active_promo_for_max_user(user_id)
        if promo:
            package_kb = keyboard(
                [btn("🚫 Оплатить без промокода", "pay_skip_promo")],
                [btn("← Назад", "pay_back_to_type")],
            )
        elif not active_promo or promo_not_applicable_note:
            package_kb = keyboard(
                [btn("🎟 Ввести промокод", "enter_promo")],
                [btn("← Назад", "pay_back_to_type")],
            )
        else:
            package_kb = keyboard([btn("← Назад", "pay_back_to_type")])
        await _reply_payment(
            api, user_id, message_id,
            f"💰 Тип оплаты: {payment_type_label}"
            f"{price_block}\n"
            f"💳 РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ\n\n"
            f"🏦 Номер счёта: {PAYMENT_BANK_NUMBER}\n"
            f"🏢 Банк: {PAYMENT_BANK_NAME}\n"
            f"👤 Владелец: {PAYMENT_ACCOUNT_HOLDER}\n\n"
            "📝 В комментарии к переводу укажите имя ученика.\n"
            f"{promo_not_applicable_note}"
            f"{promo_block}\n"
            "📸 После оплаты отправьте фото или PDF-файл чека в этот чат.",
            package_kb,
        )
        return

    if payload == "pay_back_to_type":
        students = find_students_by_max_id(user_id)
        if not students:
            await _show_menu(api, user_id, data, message_id=message_id)
            return
        promo = get_active_promo_for_max_user(user_id)
        promo_hint = ""
        if promo:
            _, code, dtype, dvalue, *_ = promo
            unit = "%" if dtype == "percent" else "₽"
            scope = "на оплату 1 занятия" if dtype == "percent" else "на занятия и пакеты"
            promo_hint = f"\n🎟 Промокод {code} (-{int(float(dvalue))}{unit}) ({scope})"
        set_max_fsm_state(user_id, PAYMENT_TYPE_CHOICE, data)
        await _reply(
            api, user_id, message_id,
            f"Выберите вариант оплаты:{promo_hint}",
            keyboard(
                [btn("✨ Разовая оплата", "pay_single")],
                [btn("📦 Выбрать пакет", "pay_package")],
                [btn("← В меню", "back_to_menu")],
            ),
        )
        return

    if payload == "menu_teachers":
        from shared.database import get_teacher_catalog_subjects
        subjects = get_teacher_catalog_subjects()
        if not subjects:
            await _reply(api, user_id, message_id, "Список преподавателей пока пуст.")
            return
        await _reply(api, user_id, message_id, "👨‍🏫 Выберите предмет:", teacher_subjects_kb(subjects))
        return

    if payload.startswith("teacher_subject_"):
        subject = payload.split("teacher_subject_", 1)[1]
        from shared.database import get_teacher_cards_by_subject
        cards = [c for c in get_teacher_cards_by_subject(subject) if (c.get("name") or "").strip()]
        if not cards:
            await _reply(api, user_id, message_id, f"По предмету «{subject}» преподаватели не добавлены.", back_menu_kb())
            return
        data["teacher_subject"] = subject
        data["teacher_index"] = 0
        set_max_fsm_state(user_id, MENU, data)
        await _send_teacher_card(api, user_id, message_id, subject, cards, 0)
        return

    if payload in ("teacher_prev", "teacher_next"):
        subject = data.get("teacher_subject", "")
        index = int(data.get("teacher_index", 0))
        from shared.database import get_teacher_cards_by_subject
        cards = [c for c in get_teacher_cards_by_subject(subject) if (c.get("name") or "").strip()]
        if not cards:
            await _show_menu(api, user_id, data, message_id=message_id)
            return
        index = max(0, index - 1) if payload == "teacher_prev" else min(len(cards) - 1, index + 1)
        data["teacher_index"] = index
        set_max_fsm_state(user_id, MENU, data)
        await _send_teacher_card(api, user_id, message_id, subject, cards, index)
        return

    if payload == "teacher_back_to_subjects":
        from shared.database import get_teacher_catalog_subjects
        subjects = get_teacher_catalog_subjects()
        await _reply(api, user_id, message_id, "👨‍🏫 Выберите предмет:", teacher_subjects_kb(subjects))
        return

    if payload == "teacher_signup":
        subject = data.get("teacher_subject", "")
        index = int(data.get("teacher_index", 0))
        from shared.database import get_teacher_cards_by_subject
        cards = [c for c in get_teacher_cards_by_subject(subject) if (c.get("name") or "").strip()]
        teacher_name = (cards[index].get("name") or "") if 0 <= index < len(cards) else ""
        data_new = {k: v for k, v in data.items() if k not in ("teacher_subject", "teacher_index")}
        data_new["teacher_choice"] = "Выбрать конкретного"
        data_new["teacher_name"] = f"{teacher_name} - {subject}"
        if not data_new.get("user_type"):
            set_max_fsm_state(user_id, APP_USER_TYPE, data_new)
            await _reply(api, user_id, message_id, "Пожалуйста, укажите: вы ученик или родитель?", user_type_kb())
        else:
            set_max_fsm_state(user_id, APP_CONTACT_METHOD, data_new)
            await _reply(api, user_id, message_id, "📞 Как с вами связаться?", contact_method_kb())
        return

    if payload == "menu_offers":
        await _reply(api, user_id, message_id, "🎁 СПЕЦИАЛЬНЫЕ ПРЕДЛОЖЕНИЯ\n\nВыберите предложение:", offers_kb())
        return

    if payload == "offer_free_diagnosis":
        await _reply(
            api, user_id, message_id,
            "🎁 БЕСПЛАТНАЯ ДИАГНОСТИКА\n\n"
            "Первое занятие для новых учеников — бесплатно!\n\n"
            "✅ Определим уровень знаний\n"
            "✅ Подберём оптимальный план обучения\n"
            "✅ Ответим на все вопросы\n\n"
            "Оставьте заявку — мы свяжемся с вами.",
            keyboard(
                [btn("📝 Оставить заявку", "menu_signup")],
                [btn("← Назад", "menu_offers")],
            ),
        )
        return

    if payload == "offer_first_package":
        await _reply(
            api, user_id, message_id,
            "💰 СКИДКА НА ПЕРВЫЙ ПАКЕТ\n\n"
            "Выгодная скидка при оформлении первого пакета занятий для новых учеников.\n\n"
            "✅ Только первый пакет\n"
            "✅ Размер скидки уточняется при оформлении\n\n"
            "Оставьте заявку — мы свяжемся с вами.",
            keyboard(
                [btn("📝 Оставить заявку", "menu_signup")],
                [btn("← Назад", "menu_offers")],
            ),
        )
        return

    if payload == "offer_referral_program":
        await _reply(
            api, user_id, message_id,
            "🤝 РЕФЕРАЛЬНАЯ ПРОГРАММА\n\n"
            "Приглашайте друзей в школу и получайте бонусные занятия!\n\n"
            "Что получает приглашённый:\n"
            "✅ Бесплатную диагностику (1 занятие на балансе сразу при заведении).\n"
            "✅ Скидку 20% на первое платное занятие после диагностики.\n\n"
            "Что получаете вы:\n"
            "✅ +1 бесплатное занятие после того, как приглашённый оплатит "
            "своё первое занятие. Бонус автоматически списывается на ближайшем "
            "проведённом занятии.\n\n"
            "Где взять свою ссылку?\n"
            "Откройте 👤 Личный кабинет → 🎁 Реферальный код. "
            "Скопируйте ссылку и отправьте другу.\n\n"
            "Уведомление о начисленном бонусе придёт автоматически в этот чат.",
            keyboard(
                [btn("🎁 Реферальный код", "show_referral_code")],
                [btn("← Назад", "menu_offers")],
            ),
        )
        return

    if payload == "menu_faq":
        await _reply(api, user_id, message_id, "❓ ПОМОЩЬ И ЧАСТЫЕ ВОПРОСЫ", faq_kb())
        return

    if payload == "faq_pay":
        await _reply(
            api, user_id, message_id,
            f"💳 КАК ОПЛАТИТЬ ЗАНЯТИЯ\n\n"
            f"Реквизиты:\n"
            f"🏦 {PAYMENT_BANK_NUMBER}\n"
            f"🏢 {PAYMENT_BANK_NAME}\n"
            f"👤 {PAYMENT_ACCOUNT_HOLDER}\n\n"
            "Порядок:\n"
            "1. Сделайте перевод на реквизиты выше.\n"
            "2. В комментарии укажите имя ученика.\n"
            "3. Откройте 👤 Личный кабинет → 💳 Оплатить занятия и отправьте фото чека.\n"
            "4. После проверки занятия начислятся на ваш баланс.",
            faq_back_kb(),
        )
        return

    if payload == "faq_package":
        await _reply(
            api, user_id, message_id,
            "📦 ЧТО ТАКОЕ ПАКЕТ ЗАНЯТИЙ\n\n"
            "Пакет — это несколько занятий, оплаченных сразу, по сниженной цене за одно.\n"
            "Чем больше пакет, тем ниже цена за занятие.\n"
            "Занятия хранятся на балансе и не сгорают.",
            faq_back_kb(),
        )
        return

    if payload == "faq_reschedule":
        await _reply(
            api, user_id, message_id,
            "🔄 ПЕРЕНОС И ОТМЕНА ЗАНЯТИЙ\n\n"
            "Предупредите преподавателя или администратора не позже чем за 6 часов.\n"
            "Тогда занятие не списывается с баланса.\n\n"
            "Если предупредить позже или не прийти — занятие списывается.",
            faq_back_kb(),
        )
        return

    if payload == "faq_promo":
        await _reply(
            api, user_id, message_id,
            "🎟 ПРОМОКОДЫ\n\n"
            "В школе «Интеграл» действуют два вида промокодов:\n\n"
            "1. Скидка в рублях (₽)\n"
            "Фиксированная сумма, вычитаемая из стоимости.\n"
            "✅ Применяется ко всем типам оплаты: разовые занятия и пакеты.\n\n"
            "2. Процентная скидка (%)\n"
            "Снижает стоимость занятия на указанный процент.\n"
            "❗ Действует только для разовых занятий — к пакетам не применяется.\n\n"
            "⏰ Срок действия: у промокода может быть указан срок — дата и время истечения. "
            "После этого момента промокод перестаёт работать.\n\n"
            "Промокод активируется автоматически при оплате — "
            "вы увидите его статус при переходе к оплате.\n\n"
            "Если промокод не подходит к выбранному формату — бот сообщит об этом.",
            faq_back_kb(),
        )
        return

    # ── Application form callbacks ─────────────────────────────────────────
    if payload.startswith("class_"):
        data["school_class"] = payload.split("class_", 1)[1]
        set_max_fsm_state(user_id, APP_GOAL, data)
        await _reply(api, user_id, message_id, "🎯 Выберите цель обучения:", goal_kb())
        return

    if payload.startswith("goal_"):
        data["goal"] = payload.split("goal_", 1)[1]
        set_max_fsm_state(user_id, APP_LESSON_TYPE, data)
        await _reply(api, user_id, message_id, "📚 Выберите формат занятий:", lesson_type_kb())
        return

    if payload in ("lesson_individual", "lesson_group"):
        data["lesson_type"] = "Индивидуально" if payload == "lesson_individual" else "Мини-группа"
        data.setdefault("subjects", [])
        set_max_fsm_state(user_id, APP_SUBJECTS, data)
        await _reply(api, user_id, message_id, "📖 Выберите предметы (можно несколько):", subjects_kb(data["subjects"]))
        return

    if payload.startswith("subject_"):
        subj = payload.split("subject_", 1)[1]
        subjects = data.get("subjects", [])
        if subj in subjects:
            subjects.remove(subj)
        else:
            subjects.append(subj)
        data["subjects"] = subjects
        set_max_fsm_state(user_id, APP_SUBJECTS, data)
        await _reply(api, user_id, message_id, "📖 Выберите предметы (✅ — выбраны):", subjects_kb(subjects))
        return

    if payload == "subjects_done":
        if not data.get("subjects"):
            await _reply(api, user_id, message_id, "❓ Выберите хотя бы один предмет.", subjects_kb([]))
            return
        set_max_fsm_state(user_id, APP_TEACHER_CHOICE, data)
        await _reply(api, user_id, message_id, "👨‍🏫 Как выбрать преподавателя?", teacher_choice_kb())
        return

    if payload == "teacher_pick":
        data["teacher_choice"] = "Подобрать преподавателя"
        set_max_fsm_state(user_id, APP_CONTACT_METHOD, data)
        await _reply(api, user_id, message_id, "📞 Как с вами связаться?", contact_method_kb())
        return

    if payload == "teacher_specific":
        data["teacher_choice"] = "Выбрать конкретного"
        set_max_fsm_state(user_id, APP_TEACHER_NAME, data)
        await _reply(api, user_id, message_id, "👨‍🏫 Выберите преподавателя:", teachers_list_kb())
        return

    if payload.startswith("pick_teacher_"):
        idx = int(payload.split("pick_teacher_", 1)[1])
        pairs = get_teacher_catalog_name_subject_pairs() or []
        seen: list[str] = []
        for pname, psubj in pairs:
            label = f"{pname} - {psubj}"
            if label not in seen:
                seen.append(label)
        if 0 <= idx < len(seen):
            data["teacher_name"] = seen[idx]
        set_max_fsm_state(user_id, APP_CONTACT_METHOD, data)
        await _reply(api, user_id, message_id, "📞 Как с вами связаться?", contact_method_kb())
        return

    if payload.startswith("contact_"):
        method = payload.split("contact_", 1)[1]
        data["contact_method"] = method
        if method == "MAX":
            if username:
                data["contact_value"] = f"@{username}"
                set_max_fsm_state(user_id, APP_COMMENT, data)
                await _reply(api, user_id, message_id, "💬 Оставьте комментарий или напишите «-» чтобы пропустить:")
            else:
                set_max_fsm_state(user_id, APP_CONTACT_VALUE, data)
                await _reply(api, user_id, message_id, "📞 У вас нет username в MAX. Введите номер телефона для связи (например: +79001234567):", back_kb())
        elif method == "Telegram":
            set_max_fsm_state(user_id, APP_CONTACT_VALUE, data)
            await _reply(api, user_id, message_id, "📱 Введите ваш Telegram @username (например: @username):", back_kb())
        else:  # Звонок
            set_max_fsm_state(user_id, APP_CONTACT_VALUE, data)
            await _reply(api, user_id, message_id, "📞 Введите номер телефона для звонка (например: +79001234567):", back_kb())
        return

    if payload == "menu_reviews":
        cards = [c for c in get_active_review_cards(limit=200) if (c.get("description") or "").strip()]
        if not cards:
            await _reply(api, user_id, message_id, "Отзывы пока не добавлены.", back_menu_kb())
            return
        data["review_index"] = 0
        set_max_fsm_state(user_id, MENU, data)
        await _send_review_card(api, user_id, message_id, cards, 0)
        return

    if payload in ("review_prev", "review_next"):
        cards = [c for c in get_active_review_cards(limit=200) if (c.get("description") or "").strip()]
        if not cards:
            await _show_menu(api, user_id, data, message_id=message_id)
            return
        index = int(data.get("review_index", 0))
        if payload == "review_prev":
            index = max(0, index - 1)
        else:
            index = min(len(cards) - 1, index + 1)
        data["review_index"] = index
        set_max_fsm_state(user_id, MENU, data)
        await _send_review_card(api, user_id, message_id, cards, index)
        return

    if payload == "faq_referral":
        await _reply(
            api, user_id, message_id,
            "🎁 РЕФЕРАЛЬНАЯ ПРОГРАММА — КРАТКО\n\n"
            "Вы приглашаете друга по своей ссылке.\n\n"
            "Друг получает:\n"
            "✅ бесплатное диагностическое занятие;\n"
            "✅ скидку 20% на своё первое платное занятие.\n\n"
            "Вы получаете:\n"
            "✅ +1 бесплатное занятие на свой баланс — после того, как друг оплатит первое занятие.\n\n"
            "Свою реферальную ссылку возьмите в 👤 Личный кабинет → 🎁 Мой реферальный код.",
            faq_back_kb(),
        )
        return

    if payload == "faq_link":
        await _reply(
            api, user_id, message_id,
            "👤 ПРИВЯЗКА АККАУНТА\n\n"
            "Когда администратор заводит вашу карточку, он использует ваш номер телефона "
            "или Telegram @username для автоматической привязки.\n\n"
            "Если в Личном кабинете написано «Вы не найдены в базе» — "
            "значит ваш аккаунт ещё не привязан к карточке ученика. "
            "Напишите администратору, и он привяжет вас вручную.\n\n"
            "Также можно связать MAX-аккаунт с Telegram: "
            "откройте 👤 Личный кабинет → 🔗 Связать с Telegram.",
            faq_back_kb(),
        )
        return

    if payload == "show_referral_code":
        students = find_students_by_max_id(user_id)
        if not students:
            await _reply(api, user_id, message_id, "❌ Вы не зарегистрированы в системе. Обратитесь к администратору.", back_menu_kb())
            return
        telegram_id = students[0][2]
        from shared.max_api import btn_url
        if telegram_id:
            tg_bot_username = TG_BOT_USERNAME or "integral_school_ru_bot"
            referral_link = f"https://t.me/{tg_bot_username}?start=ref_{telegram_id}"
            share_text = "Привет! Я занимаюсь в школе Интеграл и советую попробовать 🎓 Переходи по моей ссылке — получишь скидку 20% на первое занятие!"
            share_url = f"https://t.me/share/url?url={quote(referral_link, safe='')}&text={quote(share_text, safe='')}"
            ref_text = (
                f"🎁 Ваша реферальная ссылка\n\n"
                f"{referral_link}\n\n"
                "Поделитесь ссылкой с другом — и оба получите бонус:\n\n"
                "👤 Друг — скидка 20% на первую оплату\n"
                "🎓 Вы — +1 занятие в подарок\n\n"
                "Схема простая:\n"
                "Друг переходит → бесплатная диагностика → оплачивает занятие со скидкой → вы получаете бонус"
            )
            await _reply(
                api, user_id, message_id,
                ref_text,
                keyboard(
                    [btn_url("📤 Поделиться ссылкой", share_url)],
                    [btn("← В меню", "back_to_menu")],
                ),
            )
        else:
            # MAX-only user: generate MAX bot referral link
            max_referral_link = ""
            if MAX_BOT_USERNAME:
                max_referral_link = f"https://max.ru/{MAX_BOT_USERNAME}?start=ref_{user_id}"
            ref_text = (
                "🎁 Ваша реферальная ссылка\n\n"
                + (f"{max_referral_link}\n\n" if max_referral_link else "")
                + "Поделитесь ссылкой с другом — и оба получите бонус:\n\n"
                "👤 Друг — скидка 20% на первую оплату\n"
                "🎓 Вы — +1 занятие в подарок\n\n"
                "Схема простая:\n"
                "Друг переходит → бесплатная диагностика → оплачивает занятие со скидкой → вы получаете бонус\n\n"
                "💡 Чтобы также получить ссылку для Telegram, привяжите Telegram-аккаунт в Личном кабинете."
            )
            kb_rows = []
            if max_referral_link:
                share_text = "Привет! Я занимаюсь в школе Интеграл и советую попробовать 🎓 Переходи по моей ссылке — получишь скидку 20% на первое занятие!"
                share_url = f"https://vk.com/share.php?url={quote(max_referral_link, safe='')}&title={quote(share_text, safe='')}"
                kb_rows.append([btn_url("📤 Поделиться ссылкой", share_url)])
            kb_rows.append([btn("🔗 Связать с Telegram", "link_tg")])
            kb_rows.append([btn("← В меню", "back_to_menu")])
            await _reply(api, user_id, message_id, ref_text, keyboard(*kb_rows))
        return

    if payload == "noop":
        return

    # Unhandled — go to menu
    logger.debug("Unhandled callback payload=%s state=%s", payload, state)
    await _show_menu(api, user_id, data, message_id=message_id)


async def _handle_contact_value(
    api: MaxApiClient, user_id: int, data: dict, text: str
) -> None:
    method = data.get("contact_method", "")
    if method == "Telegram":
        if not _is_valid_tg_username(text):
            await api.send_message(
                user_id,
                "❓ Введите Telegram @username — минимум 5 символов, начиная с @.",
                back_kb(),
            )
            return
    else:
        if not _is_valid_phone(text):
            await api.send_message(
                user_id,
                "❓ Формат номера не распознан. Попробуйте ещё раз (например: +79001234567).",
                back_kb(),
            )
            return
    data["contact_value"] = text
    set_max_fsm_state(user_id, APP_COMMENT, data)
    await api.send_message(
        user_id,
        "💬 Оставьте комментарий к заявке или напишите «-» чтобы пропустить:",
        back_kb(),
    )


async def _handle_back(
    api: MaxApiClient, user_id: int, state: str, data: dict, message_id: str | None = None
) -> None:
    back_map = {
        APP_NAME:           (APP_USER_TYPE, "Пожалуйста, укажите: вы ученик или родитель?", user_type_kb()),
        APP_CLASS:          (APP_NAME,      "📝 Напишите, как к вам обращаться:", back_kb()),
        APP_GOAL:           (APP_CLASS,     "🏫 Выберите класс:", class_kb()),
        APP_LESSON_TYPE:    (APP_GOAL,      "🎯 Выберите цель обучения:", goal_kb()),
        APP_SUBJECTS:       (APP_LESSON_TYPE, "📚 Выберите формат занятий:", lesson_type_kb()),
        APP_TEACHER_CHOICE: (APP_SUBJECTS,  "📖 Выберите предметы:", subjects_kb(data.get("subjects", []))),
        APP_TEACHER_NAME:   (APP_TEACHER_CHOICE, "👨‍🏫 Как выбрать преподавателя?", teacher_choice_kb()),
        APP_CONTACT_METHOD: (APP_TEACHER_CHOICE, "👨‍🏫 Как выбрать преподавателя?", teacher_choice_kb()),
        APP_CONTACT_VALUE:  (APP_CONTACT_METHOD, "📞 Как с вами связаться?", contact_method_kb()),
        APP_COMMENT:        (APP_CONTACT_VALUE, "Введите контактные данные:", back_kb()),
    }
    if state in back_map:
        new_state, prompt, kb = back_map[state]
        set_max_fsm_state(user_id, new_state, data)
        await _reply(api, user_id, message_id, prompt, kb)
    else:
        await _show_menu(api, user_id, data, message_id=message_id)


async def _submit_application(api: MaxApiClient, user_id: int, data: dict) -> None:
    text = _build_application_text(data)
    try:
        tg_bot = TelegramBot(token=TG_BOT_TOKEN)
        await tg_bot.send_message(APPLICATIONS_CHAT_ID, text)
        await tg_bot.session.close()
    except Exception as exc:
        logger.error("Failed to forward application to TG: %s", exc)
        await api.send_message(
            user_id,
            "⚠️ Не удалось отправить заявку. Попробуйте ещё раз или свяжитесь с администратором.",
            back_menu_kb(),
        )
        return

    set_max_fsm_state(user_id, MENU, {k: v for k, v in data.items() if k == "user_type"})
    await api.send_message(
        user_id,
        "✅ Заявка отправлена! Мы свяжемся с вами в ближайшее время.",
        main_menu_kb(),
    )
