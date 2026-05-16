from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
import os

from config import ADMIN_ID, LESSON_PRICE, PACKAGE_PRICES, PAYMENTS_CHAT_ID, PAYMENT_BANK_NUMBER, PAYMENT_BANK_NAME, PAYMENT_ACCOUNT_HOLDER, PAYMENT_PHOTO_FILE_ID
from keyboards import (
    get_package_selection_keyboard,
    get_payment_check_keyboard,
    get_payment_direction_keyboard,
    get_payment_topup_keyboard,
)
from shared.database import (
    attach_first_payment,
    award_referral_bonus_to_inviter,
    consume_promo_for_student,
    create_payment_request,
    finalize_payment_with_topup,
    find_students_by_max_id,
    find_students_by_telegram_id,
    get_active_invitee_discount_percent,
    get_active_promo_for_user,
    get_payment_platform_info,
    get_payment_request_by_id,
    get_user_by_telegram_id,
    get_student_directions,
    get_student_lesson_by_id,
    log_admin_action,
    try_transition_payment_request_status,
)
from shared.max_api import MaxApiClient

_MAX_BOT_TOKEN = os.getenv("SCHOOL_MAX_BOT_TOKEN")
_max_client: MaxApiClient | None = MaxApiClient(_MAX_BOT_TOKEN) if _MAX_BOT_TOKEN else None
from states import ApplicationForm

from .common import build_payment_caption, show_main_menu

router = Router()

# admin telegram_id -> (payment_request_id, direction_id)
# tracks which manual top-up an admin has just clicked, so a follow-up numeric
# message in the payments chat is interpreted as the lesson count for that
# specific payment.
PENDING_MANUAL_TOPUPS: dict[int, tuple[int, int]] = {}


def _is_private_chat(message: Message) -> bool:
    return bool(message.chat and message.chat.type == "private")


def _is_payments_chat(message: Message) -> bool:
    return bool(message.chat and message.chat.id == PAYMENTS_CHAT_ID)


def _is_payment_moderator(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True

    raw_superadmins = os.getenv("SCHOOL_ADMIN_SUPERADMINS", "")
    if raw_superadmins:
        for part in raw_superadmins.split(","):
            part = part.strip()
            if part.isdigit() and int(part) == user_id:
                return True

    user = get_user_by_telegram_id(user_id)
    if not user:
        return False

    _, _telegram_id, _full_name, role, is_active = user
    return bool(is_active) and role in {"admin", "superadmin"}


def _can_manage_payments(callback: CallbackQuery) -> bool:
    if not callback.message:
        return False
    return _is_payments_chat(callback.message) and _is_payment_moderator(callback.from_user.id)


def _calc_price_block(payment_type: str, promo, debt_lessons: int = 0) -> str:
    """Return a text block with price calculation, or empty string if unknown."""
    dtype = dvalue = None
    if promo:
        _, _, dtype, dvalue, _ = promo
        dvalue = float(dvalue)

    if payment_type == "single" and LESSON_PRICE:
        base = LESSON_PRICE
        if dtype == "fixed_rub":
            after = max(0, base - int(dvalue))
            return f"\n💵 Стоимость: {base}₽\n🎟 Скидка по промокоду: -{int(dvalue)}₽\n✅ <b>К оплате: {after}₽</b>\n"
        elif dtype == "percent":
            after = int(base * (1 - dvalue / 100))
            return f"\n💵 Стоимость: {base}₽\n🎟 Скидка по промокоду: -{int(dvalue)}%\n✅ <b>К оплате: {after}₽</b>\n"
        else:
            return f"\n💵 <b>Стоимость: {base}₽</b>\n"

    if payment_type == "package" and debt_lessons > 0:
        base = debt_lessons  # debt_lessons reused as package_price for packages
        if dtype == "fixed_rub":
            after = max(0, base - int(dvalue))
            return f"\n💵 Стоимость пакета: {base}₽\n🎟 Скидка по промокоду: -{int(dvalue)}₽\n✅ <b>К оплате: {after}₽</b>\n"
        elif dtype == "percent":
            after = int(base * (1 - dvalue / 100))
            return f"\n💵 Стоимость пакета: {base}₽\n🎟 Скидка по промокоду: -{int(dvalue)}%\n✅ <b>К оплате: {after}₽</b>\n"
        else:
            return f"\n💵 <b>Стоимость пакета: {base}₽</b>\n"

    if payment_type == "debt" and LESSON_PRICE and debt_lessons > 0:
        base = LESSON_PRICE * debt_lessons
        return f"\n💵 <b>Сумма долга: {debt_lessons} × {LESSON_PRICE}₽ = {base}₽</b>\n"

    return ""


def _build_payment_details_text(
    discount_block: str,
    promo_block: str,
    price_block: str,
    payment_type_label: str,
) -> str:
    return (
        f"💰 <b>Тип оплаты:</b> {payment_type_label}\n"
        f"{price_block}\n"
        "💳 <b>РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ</b>\n\n"
        f"🏦 <b>Номер счёта:</b> <code>{PAYMENT_BANK_NUMBER}</code>\n"
        f"🏢 <b>Банк:</b> {PAYMENT_BANK_NAME}\n"
        f"👤 <b>Владелец:</b> {PAYMENT_ACCOUNT_HOLDER}\n\n"
        "<b>📝 В комментарии к переводу укажите:</b>\n"
        "<code>[ИМЯ УЧЕНИКА]</code>\n"
        f"{discount_block}{promo_block}\n"
        "📸 <b>После оплаты отправьте фото, скриншот или PDF-файл чека.</b>"
    )


def _build_payment_type_kb(has_debt: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_debt:
        rows.append([InlineKeyboardButton(text="💸 Погасить долг", callback_data="pay_debt")])
    else:
        rows.append([InlineKeyboardButton(text="✨ Оплатить одно занятие", callback_data="pay_single")])
        rows.append([InlineKeyboardButton(text="📦 Выбрать пакет", callback_data="pay_package")])
    rows.append([InlineKeyboardButton(text="← В меню", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(ApplicationForm.menu, lambda c: c.data == "menu_paid")
async def menu_paid(callback: CallbackQuery, state: FSMContext):
    if not _is_private_chat(callback.message):
        await callback.answer("⚠️ Оплату отправляйте в личном чате с ботом.", show_alert=True)
        return

    students = find_students_by_telegram_id(callback.from_user.id)
    if not students:
        await callback.answer(
            "❌ Вы не зарегистрированы в системе.\n"
            "Пожалуйста, обратитесь к администратору.",
            show_alert=True
        )
        return

    student_id = students[0][0]
    directions = get_student_directions(student_id)
    debt_directions = [(d[1], d[2], d[3]) for d in directions if d[3] < 0]

    promo = get_active_promo_for_user(callback.from_user.id)
    promo_hint = ""
    if promo and not debt_directions:
        _, code, dtype, dvalue, _ = promo
        unit = "%" if dtype == "percent" else "₽"
        scope = "на оплату 1 занятия" if dtype == "percent" else "на занятия и пакеты" if dtype == "fixed_rub" else ""
        promo_hint = f"\n\nАктивная скидка: промокод <b>{code}</b> (-{int(float(dvalue))}{unit})"
        if scope:
            promo_hint += f" ({scope})"

    if debt_directions:
        debt_lines = "\n".join(
            f"• {subj} — {teacher}: долг {abs(bal)} занят{'ие' if abs(bal) == 1 else 'ия' if 2 <= abs(bal) <= 4 else 'ий'}"
            for teacher, subj, bal in debt_directions
        )
        text = (
            "⚠️ <b>У вас есть задолженность по занятиям:</b>\n\n"
            f"{debt_lines}\n\n"
            "Для погашения долга переведите нужную сумму и отправьте чек."
        )
    else:
        text = f"Выберите вариант оплаты:{promo_hint}"

    await state.set_state(ApplicationForm.payment_type_choice)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=_build_payment_type_kb(bool(debt_directions)))
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=_build_payment_type_kb(bool(debt_directions)))
    await callback.answer()


async def _show_payment_details(
    callback: CallbackQuery, state: FSMContext,
    payment_type: str, payment_type_label: str,
    debt_lessons: int = 0, package_price: int = 0,
):
    students = find_students_by_telegram_id(callback.from_user.id)
    if not students:
        await callback.answer("❌ Вы не зарегистрированы в системе.", show_alert=True)
        return

    student_id = students[0][0]
    discount_percent = get_active_invitee_discount_percent(student_id)
    promo = get_active_promo_for_user(callback.from_user.id)

    discount_block = ""
    if discount_percent:
        discount_block = (
            f"\n🎁 <b>У вас активна реферальная скидка {discount_percent}%.</b>\n"
            f"Заплатите на {discount_percent}% меньше указанной суммы — "
            "скидка сгорает после первой подтверждённой оплаты.\n"
        )

    promo_block = ""
    if promo and payment_type != "debt":
        _, code, dtype, dvalue, applies_to_packages = promo
        unit = "%" if dtype == "percent" else "₽"
        promo_only_packages = int(applies_to_packages or 0) == 1
        if promo_only_packages and payment_type == "single":
            promo_block = f"\n🎟 Промокод <b>{code}</b> действует только на пакеты занятий.\n"
            promo = None  # не применяем скидку к разовому
        elif payment_type == "single" and LESSON_PRICE:
            promo_block = f"\n🎟 Применён промокод <b>{code}</b> ({int(float(dvalue))}{unit})\n"
        elif payment_type == "package":
            promo_block = f"\n🎟 Применён промокод <b>{code}</b>: скидка {int(float(dvalue))}{unit}\n"
        else:
            promo_block = f"\n🎟 <b>Промокод {code}: скидка {int(float(dvalue))}{unit}</b> применится к этому платежу.\n"

    # For packages, pass package_price via debt_lessons slot
    price_block = _calc_price_block(payment_type, promo, package_price if payment_type == "package" else debt_lessons)

    payment_text = _build_payment_details_text(discount_block, promo_block, price_block, payment_type_label)

    detail_buttons = []
    if not promo and payment_type != "debt":
        detail_buttons.append([InlineKeyboardButton(text="🎟 Ввести промокод", callback_data="enter_promo")])
    detail_buttons.append([InlineKeyboardButton(text="← В меню", callback_data="back_to_menu")])
    detail_kb = InlineKeyboardMarkup(inline_keyboard=detail_buttons)

    await state.update_data(payment_type=payment_type, payment_type_label=payment_type_label)
    await state.set_state(ApplicationForm.payment_proof)
    await callback.answer()

    if PAYMENT_PHOTO_FILE_ID:
        try:
            await callback.message.delete()
        except Exception:
            pass
        try:
            await callback.message.answer_photo(
                photo=PAYMENT_PHOTO_FILE_ID,
                caption=payment_text,
                parse_mode="HTML",
                reply_markup=detail_kb,
            )
        except Exception:
            # Caption too long — send photo then text separately
            await callback.message.answer_photo(photo=PAYMENT_PHOTO_FILE_ID)
            await callback.message.answer(payment_text, parse_mode="HTML", reply_markup=detail_kb)
    else:
        try:
            await callback.message.edit_text(payment_text, parse_mode="HTML", reply_markup=detail_kb)
        except Exception:
            await callback.message.answer(payment_text, parse_mode="HTML", reply_markup=detail_kb)


@router.callback_query(lambda c: c.data == "debt_notify_pay")
async def debt_notify_pay(callback: CallbackQuery, state: FSMContext):
    """Called from debt reminder notification button — no specific FSM state required."""
    students = find_students_by_telegram_id(callback.from_user.id)
    debt_lessons = 0
    if students:
        directions = get_student_directions(students[0][0])
        debt_lessons = sum(abs(d[3]) for d in directions if d[3] < 0)
    await state.set_state(ApplicationForm.payment_type_choice)
    await _show_payment_details(callback, state, "debt", "💸 Погашение долга", debt_lessons=debt_lessons)


@router.callback_query(ApplicationForm.payment_type_choice, lambda c: c.data == "pay_debt")
async def pay_debt(callback: CallbackQuery, state: FSMContext):
    students = find_students_by_telegram_id(callback.from_user.id)
    debt_lessons = 0
    if students:
        directions = get_student_directions(students[0][0])
        debt_lessons = sum(abs(d[3]) for d in directions if d[3] < 0)
    await _show_payment_details(callback, state, "debt", "💸 Погашение долга", debt_lessons=debt_lessons)


@router.callback_query(ApplicationForm.payment_type_choice, lambda c: c.data == "pay_single")
async def pay_single(callback: CallbackQuery, state: FSMContext):
    await _show_payment_details(callback, state, "single", "✨ Разовое занятие")


@router.callback_query(ApplicationForm.payment_type_choice, lambda c: c.data == "pay_package")
async def pay_package(callback: CallbackQuery, state: FSMContext):
    if not PACKAGE_PRICES:
        await _show_payment_details(callback, state, "package", "📦 Пакет занятий")
        return

    promo = get_active_promo_for_user(callback.from_user.id)
    promo_note = ""
    if promo:
        _, code, dtype, dvalue, _ = promo
        unit = "%" if dtype == "percent" else "₽"
        promo_note = f"\n🎟 Промокод <b>{code}</b> (-{int(float(dvalue))}{unit}) применяется к пакетам."
    else:
        promo_note = ""

    text = f"📦 <b>Выбор пакета</b>{promo_note}\n\nВыберите количество занятий:"
    kb = get_package_selection_keyboard(PACKAGE_PRICES, promo)
    await state.set_state(ApplicationForm.package_selection)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(ApplicationForm.package_selection, lambda c: c.data.startswith("pay_package_"))
async def select_package(callback: CallbackQuery, state: FSMContext):
    try:
        lessons = int(callback.data.split("pay_package_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка выбора пакета.", show_alert=True)
        return
    price = PACKAGE_PRICES.get(lessons)
    if not price:
        await callback.answer("Пакет не найден.", show_alert=True)
        return
    label = f"📦 Пакет {lessons} занятий"
    await _show_payment_details(callback, state, "package", label, package_price=price)


@router.callback_query(ApplicationForm.package_selection, lambda c: c.data == "pay_back_to_type")
async def package_back_to_type(callback: CallbackQuery, state: FSMContext):
    promo = get_active_promo_for_user(callback.from_user.id)
    promo_hint = ""
    if promo:
        _, code, dtype, dvalue, _ = promo
        unit = "%" if dtype == "percent" else "₽"
        scope = "на оплату 1 занятия" if dtype == "percent" else "на занятия и пакеты"
        promo_hint = f"\n\nАктивная скидка: промокод <b>{code}</b> (-{int(float(dvalue))}{unit}) ({scope})"
    text = f"Выберите вариант оплаты:{promo_hint}"
    await state.set_state(ApplicationForm.payment_type_choice)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=_build_payment_type_kb(False))
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=_build_payment_type_kb(False))
    await callback.answer()


@router.message(ApplicationForm.payment_proof)
async def get_payment_proof(message: Message, state: FSMContext):
    if not _is_private_chat(message):
        return

    username = f"@{message.from_user.username}" if message.from_user.username else None
    caption_text = message.caption.strip() if message.caption else None
    file_id = None
    file_type = None

    if message.photo:
        file_id = message.photo[-1].file_id
        file_type = "photo"
    elif message.document:
        doc = message.document
        file_name = (doc.file_name or "").lower()
        mime_type = (doc.mime_type or "").lower()
        if mime_type != "application/pdf" and not file_name.endswith(".pdf"):
            await message.answer("❓ Пожалуйста, отправьте PDF-файл чека.")
            return
        file_id = doc.file_id
        file_type = "pdf"
    else:
        await message.answer("❓ Пожалуйста, отправьте фото или PDF-файл чека.")
        return

    fsm_data = await state.get_data()
    payment_type_label = fsm_data.get("payment_type_label")
    payment_type = fsm_data.get("payment_type")

    payment_request_id = create_payment_request(
        telegram_user_id=message.from_user.id,
        telegram_username=username,
        telegram_full_name=message.from_user.full_name,
        caption_text=caption_text,
        file_id=file_id,
        file_type=file_type,
    )

    students_for_discount = find_students_by_telegram_id(message.from_user.id)
    discount_percent = None
    if students_for_discount:
        discount_percent = get_active_invitee_discount_percent(students_for_discount[0][0])

    promo = get_active_promo_for_user(message.from_user.id)
    promo_label = None
    if promo and payment_type != "debt":
        _, code, dtype, dvalue, _ = promo
        unit = "%" if dtype == "percent" else "₽"
        promo_label = f"{code} (-{int(dvalue)}{unit})"

    payment_text = build_payment_caption(
        payment_request_id=payment_request_id,
        full_name=message.from_user.full_name,
        username=username,
        telegram_user_id=message.from_user.id,
        caption_text=caption_text,
        status_text="⏳ Ожидает проверки",
        referral_discount_percent=discount_percent,
        payment_type_label=payment_type_label,
        promo_label=promo_label,
    )

    if file_type == "pdf":
        await message.bot.send_document(
            PAYMENTS_CHAT_ID,
            document=file_id,
            caption=payment_text,
            parse_mode="HTML",
            reply_markup=get_payment_check_keyboard(payment_request_id),
        )
    else:
        await message.bot.send_photo(
            PAYMENTS_CHAT_ID,
            photo=file_id,
            caption=payment_text,
            parse_mode="HTML",
            reply_markup=get_payment_check_keyboard(payment_request_id),
        )

    await message.answer("✅ Спасибо, чек отправлен. После проверки мы сообщим результат.")
    await show_main_menu(message, state)


@router.callback_query(lambda c: c.data.startswith("payment_reject_"))
async def reject_payment_request(callback: CallbackQuery):
    if not _can_manage_payments(callback):
        await callback.answer("🚫 Недостаточно прав", show_alert=True)
        return

    payment_request_id = int(callback.data.split("_")[2])
    payment = get_payment_request_by_id(payment_request_id)
    if not payment:
        await callback.answer("❌ Запрос оплаты не найден", show_alert=True)
        return

    (
        _,
        telegram_user_id,
        telegram_username,
        telegram_full_name,
        caption_text,
        _file_id,
        _file_type,
        status,
        _approved_by,
        _rejected_by,
        _created_at,
        _updated_at,
    ) = payment

    if status == "approved":
        await callback.answer("✅ Эта оплата уже подтверждена", show_alert=True)
        return

    if status == "rejected":
        await callback.answer("❌ Эта оплата уже отклонена", show_alert=True)
        return

    transitioned = try_transition_payment_request_status(
        payment_request_id=payment_request_id,
        allowed_from_statuses=["pending", "processing"],
        new_status="rejected",
        admin_id=callback.from_user.id,
    )
    if not transitioned:
        payment_latest = get_payment_request_by_id(payment_request_id)
        latest_status = payment_latest[7] if payment_latest else "unknown"
        await callback.answer(
            f"Эту оплату уже обработали (статус: {latest_status})",
            show_alert=True,
        )
        return

    log_admin_action(
        admin_telegram_id=callback.from_user.id,
        action_type="payment_rejected",
        target_type="payment_request",
        target_id=payment_request_id,
        details=f"telegram_user_id={telegram_user_id}",
        status="success",
    )

    rejected_caption = build_payment_caption(
        payment_request_id=payment_request_id,
        full_name=telegram_full_name,
        username=telegram_username,
        telegram_user_id=telegram_user_id,
        caption_text=caption_text,
        status_text="❌ Отклонено",
    )

    try:
        await callback.message.edit_caption(caption=rejected_caption, parse_mode="HTML")
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    if telegram_user_id:
        try:
            await callback.bot.send_message(
                telegram_user_id,
                "❌ <b>Ваша оплата отклонена</b>\n\n"
                "Проверьте чек или свяжитесь с администратором.",
                parse_mode="HTML"
            )
        except Exception:
            pass
    else:
        source_platform, max_user_id = get_payment_platform_info(payment_request_id)
        if source_platform == "max" and max_user_id and _max_client:
            try:
                await _max_client.send_message(
                    max_user_id,
                    "❌ Ваша оплата отклонена.\n\nПроверьте чек или свяжитесь с администратором.",
                )
            except Exception:
                pass

    await callback.answer("❌ Оплата отклонена")


@router.callback_query(lambda c: c.data.startswith("payment_approve_"))
async def approve_payment_request(callback: CallbackQuery):
    if not _can_manage_payments(callback):
        await callback.answer("🚫 Недостаточно прав", show_alert=True)
        return

    payment_request_id = int(callback.data.split("_")[2])
    payment = get_payment_request_by_id(payment_request_id)
    if not payment:
        await callback.answer("❌ Запрос оплаты не найден", show_alert=True)
        return

    (
        _,
        telegram_user_id,
        telegram_username,
        telegram_full_name,
        caption_text,
        _file_id,
        _file_type,
        status,
        _approved_by,
        _rejected_by,
        _created_at,
        _updated_at,
    ) = payment

    if status == "approved":
        await callback.answer("✅ Эта оплата уже подтверждена", show_alert=True)
        return
    if status == "rejected":
        await callback.answer("❌ Эта оплата уже отклонена", show_alert=True)
        return

    students = find_students_by_telegram_id(telegram_user_id) if telegram_user_id else []
    if not students:
        source_platform, max_uid = get_payment_platform_info(payment_request_id)
        if source_platform == "max" and max_uid:
            students = find_students_by_max_id(max_uid)
    if not students and not telegram_user_id:
        await callback.answer("❌ Ученик не найден (нет TG ID и MAX ID)", show_alert=True)
        return
    if not students:
        await callback.answer("❌ Ученик не найден по Telegram ID", show_alert=True)
        return
    if len(students) > 1:
        await callback.answer("⚠️ Найдено несколько учеников с этим Telegram ID", show_alert=True)
        return

    student_id, student_name, _, _ = students[0]
    directions = get_student_directions(student_id)
    if not directions:
        await callback.answer("❌ У ученика нет направлений для начисления", show_alert=True)
        return

    discount_percent = get_active_invitee_discount_percent(student_id)

    transitioned = try_transition_payment_request_status(
        payment_request_id=payment_request_id,
        allowed_from_statuses=["pending"],
        new_status="processing",
        admin_id=callback.from_user.id,
    )
    if not transitioned:
        payment_latest = get_payment_request_by_id(payment_request_id)
        latest_status = payment_latest[7] if payment_latest else "unknown"
        await callback.answer(
            f"⚠️ Эта оплата уже обрабатывается или обработана (статус: {latest_status})",
            show_alert=True,
        )
        return

    log_admin_action(
        admin_telegram_id=callback.from_user.id,
        action_type="payment_processing_started",
        target_type="payment_request",
        target_id=payment_request_id,
        details=f"student={student_name}",
        status="success",
    )

    caption = build_payment_caption(
        payment_request_id=payment_request_id,
        full_name=telegram_full_name,
        username=telegram_username,
        telegram_user_id=telegram_user_id,
        caption_text=caption_text,
        status_text="🔄 В обработке",
        referral_discount_percent=discount_percent,
    )

    if len(directions) == 1:
        direction_id, teacher_name, subject_name, lesson_balance, _ = directions[0]
        caption += (
            "\n\n"
            f"👤 <b>Ученик:</b> {student_name}\n"
            f"📚 <b>Направление:</b> {subject_name} — {teacher_name}\n"
            f"📊 <b>Текущий остаток:</b> {lesson_balance}\n\n"
            "⬇️ <b>Выберите, сколько занятий начислить:</b>"
        )
        try:
            await callback.message.edit_caption(caption=caption, parse_mode="HTML")
            await callback.message.edit_reply_markup(
                reply_markup=get_payment_topup_keyboard(payment_request_id, direction_id)
            )
        except Exception:
            pass
        await callback.answer("✅ Направление выбрано автоматически")
        return

    caption += (
        "\n\n"
        f"👤 <b>Ученик:</b> {student_name}\n"
        "⬇️ <b>Выберите направление для начисления:</b>"
    )
    try:
        await callback.message.edit_caption(caption=caption, parse_mode="HTML")
        await callback.message.edit_reply_markup(
            reply_markup=get_payment_direction_keyboard(payment_request_id, directions)
        )
    except Exception:
        pass
    await callback.answer("👆 Выберите направление")


@router.callback_query(lambda c: c.data.startswith("paydir_"))
async def choose_payment_direction(callback: CallbackQuery):
    if not _can_manage_payments(callback):
        await callback.answer("🚫 Недостаточно прав", show_alert=True)
        return

    _, payment_request_id_raw, direction_id_raw = callback.data.split("_")
    payment_request_id = int(payment_request_id_raw)
    direction_id = int(direction_id_raw)

    payment = get_payment_request_by_id(payment_request_id)
    if not payment:
        await callback.answer("❌ Запрос оплаты не найден", show_alert=True)
        return

    lesson = get_student_lesson_by_id(direction_id)
    if not lesson:
        await callback.answer("❌ Направление не найдено", show_alert=True)
        return

    _, student_id_owner, _, subject_name, lesson_balance, _, _student_name, teacher_name = lesson
    (
        _,
        telegram_user_id,
        telegram_username,
        telegram_full_name,
        caption_text,
        _file_id,
        _file_type,
        _status,
        _approved_by,
        _rejected_by,
        _created_at,
        _updated_at,
    ) = payment

    discount_percent = get_active_invitee_discount_percent(student_id_owner)

    caption = build_payment_caption(
        payment_request_id=payment_request_id,
        full_name=telegram_full_name,
        username=telegram_username,
        telegram_user_id=telegram_user_id,
        caption_text=caption_text,
        status_text="🔄 В обработке",
        referral_discount_percent=discount_percent,
    )
    caption += (
        "\n\n"
        f"📚 <b>Выбрано направление:</b> {subject_name} — {teacher_name}\n"
        f"📊 <b>Текущий остаток:</b> {lesson_balance}\n\n"
        "⬇️ <b>Выберите, сколько занятий начислить:</b>"
    )

    try:
        await callback.message.edit_caption(caption=caption, parse_mode="HTML")
        await callback.message.edit_reply_markup(
            reply_markup=get_payment_topup_keyboard(payment_request_id, direction_id)
        )
    except Exception:
        pass

    await callback.answer()


async def _apply_topup(
    bot,
    *,
    payment_request_id: int,
    direction_id: int,
    lessons_to_add: int,
    admin_id: int,
    source_message: Message,
    edit_target: Message | None,
    is_manual: bool,
):
    """Shared finalization for both quick-button and manual top-ups.

    Updates the original payment-chat message caption, posts a confirmation
    line, and DM's the student. `edit_target` is the message that holds the
    original receipt (its caption/markup should be cleared); for manual entry
    it's None because the admin replied with a separate text message.
    """
    payment = get_payment_request_by_id(payment_request_id)
    if not payment:
        await source_message.answer("❌ Запрос оплаты не найден.")
        return

    (
        _,
        telegram_user_id,
        telegram_username,
        telegram_full_name,
        caption_text,
        _file_id,
        _file_type,
        _status,
        _approved_by,
        _rejected_by,
        _created_at,
        _updated_at,
    ) = payment

    lesson = get_student_lesson_by_id(direction_id)
    if not lesson:
        await source_message.answer("❌ Направление не найдено.")
        return

    _, student_id, _, subject_name, lesson_balance_before, _, student_name, teacher_name = lesson

    comment_suffix = " (вручную)" if is_manual else ""
    finalized = finalize_payment_with_topup(
        payment_request_id=payment_request_id,
        direction_id=direction_id,
        lessons_count=lessons_to_add,
        admin_id=admin_id,
        comment=f"Начисление после подтверждения оплаты #{payment_request_id}{comment_suffix}",
    )
    if not finalized:
        payment_latest = get_payment_request_by_id(payment_request_id)
        latest_status = payment_latest[7] if payment_latest else "unknown"
        log_admin_action(
            admin_telegram_id=admin_id,
            action_type=(
                "payment_manual_topup_failed" if is_manual else "payment_topup_failed"
            ),
            target_type="payment_request",
            target_id=payment_request_id,
            details=f"status={latest_status}",
            status="error",
        )
        await source_message.answer(
            f"⚠️ Начисление не выполнено: оплата уже обработана (статус: {latest_status})."
        )
        return

    log_admin_action(
        admin_telegram_id=admin_id,
        action_type=(
            "payment_manual_topup_success" if is_manual else "payment_topup_success"
        ),
        target_type="payment_request",
        target_id=payment_request_id,
        details=f"direction={direction_id};lessons={lessons_to_add}",
        status="success",
    )

    approved_caption = build_payment_caption(
        payment_request_id=payment_request_id,
        full_name=telegram_full_name,
        username=telegram_username,
        telegram_user_id=telegram_user_id,
        caption_text=caption_text,
        status_text=f"✅ Подтверждено, начислено {lessons_to_add} занятий",
    )

    if edit_target is not None:
        try:
            await edit_target.edit_caption(caption=approved_caption, parse_mode="HTML")
            await edit_target.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    consume_promo_for_student(student_id)

    updated_lesson = get_student_lesson_by_id(direction_id)
    _, _, _, _, lesson_balance_after, _, _, _ = updated_lesson

    await source_message.answer(
        f"✅ <b>Оплата #{payment_request_id} подтверждена</b>\n\n"
        f"👤 <b>Ученик:</b> {student_name}\n"
        f"📚 <b>Предмет:</b> {subject_name}\n"
        f"👨‍🏫 <b>Преподаватель:</b> {teacher_name}\n"
        f"📊 <b>Баланс был:</b> {lesson_balance_before}\n"
        f"➕ <b>Начислено:</b> {lessons_to_add}\n"
        f"📊 <b>Баланс стал:</b> {lesson_balance_after}",
        parse_mode="HTML",
    )

    if telegram_user_id:
        try:
            await bot.send_message(
                telegram_user_id,
                f"✅ <b>Ваша оплата подтверждена!</b>\n\n"
                f"На баланс начислено {lessons_to_add} занятий.\n\n"
                f"📚 <b>Предмет:</b> {subject_name}\n"
                f"👨‍🏫 <b>Преподаватель:</b> {teacher_name}",
                parse_mode="HTML",
            )
        except Exception:
            pass
    else:
        source_platform, max_user_id = get_payment_platform_info(payment_request_id)
        if source_platform == "max" and max_user_id and _max_client:
            try:
                await _max_client.send_message(
                    max_user_id,
                    f"✅ Ваша оплата подтверждена!\n\n"
                    f"На баланс начислено {lessons_to_add} занятий.\n"
                    f"📚 Предмет: {subject_name}\n"
                    f"👨‍🏫 Преподаватель: {teacher_name}",
                )
            except Exception:
                pass

    # Referral post-processing: only fires once per student because
    # attach_first_payment is idempotent (returns False if first_paid_at is
    # already set), and award_referral_bonus_to_inviter only credits referrals
    # that are still in 'student_linked' state.
    is_first_paid = attach_first_payment(student_id, payment_request_id)
    if is_first_paid:
        bonus = award_referral_bonus_to_inviter(
            invitee_student_id=student_id,
            admin_id=admin_id,
        )
        if bonus:
            inviter_tg = bonus["inviter_telegram_id"]
            try:
                await bot.send_message(
                    inviter_tg,
                    "🎁 <b>Реферальный бонус!</b>\n\n"
                    f"Ваш приглашённый ученик <b>{student_name}</b> "
                    "оплатил первое занятие.\n"
                    f"Вам начислено <b>+{bonus['lessons_added']} бонусное занятие</b> "
                    f"в направлении «{bonus['subject_name']} — {bonus['teacher_name']}».\n\n"
                    "Оно будет списано на ближайшем проведённом занятии.",
                    parse_mode="HTML",
                )
            except Exception:
                pass

            if telegram_user_id:
                try:
                    await bot.send_message(
                        telegram_user_id,
                        "🎁 К этой оплате применена реферальная скидка 20%. "
                        "Спасибо, что пришли по приглашению!",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

            try:
                await source_message.answer(
                    "🎁 Реферальный бонус начислен пригласившему "
                    f"(tg_id={inviter_tg}, +{bonus['lessons_added']} в направлении "
                    f"«{bonus['subject_name']} — {bonus['teacher_name']}»).",
                )
            except Exception:
                pass


@router.callback_query(lambda c: c.data.startswith("payadd_"))
async def add_lessons_after_payment(callback: CallbackQuery):
    if not _can_manage_payments(callback):
        await callback.answer("🚫 Недостаточно прав", show_alert=True)
        return

    _, payment_request_id_raw, direction_id_raw, lessons_to_add_raw = callback.data.split("_")
    payment_request_id = int(payment_request_id_raw)
    direction_id = int(direction_id_raw)
    lessons_to_add = int(lessons_to_add_raw)

    await _apply_topup(
        callback.bot,
        payment_request_id=payment_request_id,
        direction_id=direction_id,
        lessons_to_add=lessons_to_add,
        admin_id=callback.from_user.id,
        source_message=callback.message,
        edit_target=callback.message,
        is_manual=False,
    )
    await callback.answer("✅ Занятия начислены")


@router.callback_query(lambda c: c.data.startswith("paymanual_"))
async def request_manual_topup(callback: CallbackQuery):
    if not _can_manage_payments(callback):
        await callback.answer("🚫 Недостаточно прав", show_alert=True)
        return

    parts = callback.data.split("_")
    if len(parts) != 3:
        await callback.answer("Некорректные данные кнопки", show_alert=True)
        return

    try:
        payment_request_id = int(parts[1])
        direction_id = int(parts[2])
    except ValueError:
        await callback.answer("Некорректные данные кнопки", show_alert=True)
        return

    payment = get_payment_request_by_id(payment_request_id)
    if not payment:
        await callback.answer("❌ Запрос оплаты не найден", show_alert=True)
        return

    PENDING_MANUAL_TOPUPS[callback.from_user.id] = (payment_request_id, direction_id)
    await callback.message.answer(
        f"📝 Введите числом, сколько занятий начислить по оплате #{payment_request_id}.\n"
        "Ответьте сообщением в этот же чат.",
    )
    await callback.answer()


@router.message(
    lambda m: _is_payments_chat(m)
    and _is_payment_moderator(m.from_user.id)
    and m.from_user.id in PENDING_MANUAL_TOPUPS
    and bool(m.text)
    and m.text.strip().isdigit()
)
async def process_manual_payment_amount(message: Message):
    pending = PENDING_MANUAL_TOPUPS.get(message.from_user.id)
    if not pending:
        return
    payment_request_id, direction_id = pending

    lessons_to_add = int(message.text.strip())
    if lessons_to_add <= 0:
        await message.answer("❓ Количество занятий должно быть больше нуля.")
        return

    PENDING_MANUAL_TOPUPS.pop(message.from_user.id, None)

    await _apply_topup(
        message.bot,
        payment_request_id=payment_request_id,
        direction_id=direction_id,
        lessons_to_add=lessons_to_add,
        admin_id=message.from_user.id,
        source_message=message,
        edit_target=None,
        is_manual=True,
    )
