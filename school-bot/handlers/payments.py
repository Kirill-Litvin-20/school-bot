from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
import os

from config import ADMIN_ID, PAYMENTS_CHAT_ID, PAYMENT_BANK_NUMBER, PAYMENT_BANK_NAME, PAYMENT_ACCOUNT_HOLDER, PAYMENT_PHOTO_FILE_ID
from keyboards import (
    get_payment_check_keyboard,
    get_payment_direction_keyboard,
    get_payment_topup_keyboard,
)
from shared.database import (
    attach_first_payment,
    award_referral_bonus_to_inviter,
    create_payment_request,
    finalize_payment_with_topup,
    find_students_by_telegram_id,
    get_active_invitee_discount_percent,
    get_payment_request_by_id,
    get_user_by_telegram_id,
    get_student_directions,
    get_student_lesson_by_id,
    log_admin_action,
    try_transition_payment_request_status,
)
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


@router.callback_query(ApplicationForm.menu, lambda c: c.data == "menu_paid")
async def menu_paid(callback: CallbackQuery, state: FSMContext):
    if not _is_private_chat(callback.message):
        await callback.answer("⚠️ Оплату отправляйте в личном чате с ботом.", show_alert=True)
        return

    # Проверяем, найден ли ученик в системе
    students = find_students_by_telegram_id(callback.from_user.id)
    if not students:
        await callback.answer(
            "❌ Вы не зарегистрированы в системе.\n"
            "Пожалуйста, обратитесь к администратору.",
            show_alert=True
        )
        return

    # Отправляем фото с ценами
    if PAYMENT_PHOTO_FILE_ID:
        await callback.message.answer_photo(
            photo=PAYMENT_PHOTO_FILE_ID
        )

    student_id = students[0][0]
    discount_percent = get_active_invitee_discount_percent(student_id)

    discount_block = ""
    if discount_percent:
        discount_block = (
            f"\n🎁 <b>У вас активна реферальная скидка {discount_percent}% "
            "на этот платёж.</b>\n"
            f"Заплатите на {discount_percent}% меньше указанной в прайсе суммы — "
            "скидка сгорает после первой подтверждённой оплаты.\n"
        )

    payment_text = (
        "💳 <b>РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ</b>\n\n"
        f"🏦 <b>Номер счёта:</b> <code>{PAYMENT_BANK_NUMBER}</code>\n"
        f"🏢 <b>Банк:</b> {PAYMENT_BANK_NAME}\n"
        f"👤 <b>Владелец:</b> {PAYMENT_ACCOUNT_HOLDER}\n\n"
        "<b>📝 В комментарии к переводу укажите:</b>\n"
        "<code>[ИМЯ УЧЕНИКА]</code>\n"
        f"{discount_block}\n"
        "📸 <b>После оплаты отправьте фото, скриншот или PDF-файл чека об оплате.</b>\n\n"
        "🔙 Если нужно вернуться в меню, нажмите /menu"
    )

    await callback.message.answer(payment_text, parse_mode="HTML")
    await state.set_state(ApplicationForm.payment_proof)
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

    payment_text = build_payment_caption(
        payment_request_id=payment_request_id,
        full_name=message.from_user.full_name,
        username=username,
        telegram_user_id=message.from_user.id,
        caption_text=caption_text,
        status_text="⏳ Ожидает проверки",
        referral_discount_percent=discount_percent,
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
    if not telegram_user_id:
        await callback.answer("❌ У оплаты нет Telegram ID", show_alert=True)
        return

    students = find_students_by_telegram_id(telegram_user_id)
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
