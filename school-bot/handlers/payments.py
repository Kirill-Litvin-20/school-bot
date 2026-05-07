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
    create_payment_request,
    finalize_payment_with_topup,
    find_students_by_telegram_id,
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

    # Отправляем фото с ценами
    if PAYMENT_PHOTO_FILE_ID:
        await callback.message.answer_photo(
            photo=PAYMENT_PHOTO_FILE_ID
        )

    payment_text = (
        "💳 <b>РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ</b>\n\n"
        f"🏦 <b>Номер счёта:</b> <code>{PAYMENT_BANK_NUMBER}</code>\n"
        f"🏢 <b>Банк:</b> {PAYMENT_BANK_NAME}\n"
        f"👤 <b>Владелец:</b> {PAYMENT_ACCOUNT_HOLDER}\n\n"
        "<b>📝 В комментарии к переводу укажите:</b>\n"
        "<code>[ИМЯ УЧЕНИКА]</code>\n\n"
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

    payment_text = build_payment_caption(
        payment_request_id=payment_request_id,
        full_name=message.from_user.full_name,
        username=username,
        telegram_user_id=message.from_user.id,
        caption_text=caption_text,
        status_text="⏳ Ожидает проверки",
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

    _, _, _, subject_name, lesson_balance, _, _student_name, teacher_name = lesson
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

    caption = build_payment_caption(
        payment_request_id=payment_request_id,
        full_name=telegram_full_name,
        username=telegram_username,
        telegram_user_id=telegram_user_id,
        caption_text=caption_text,
        status_text="🔄 В обработке",
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


@router.callback_query(lambda c: c.data.startswith("payadd_"))
async def add_lessons_after_payment(callback: CallbackQuery):
    if not _can_manage_payments(callback):
        await callback.answer("🚫 Недостаточно прав", show_alert=True)
        return

    _, payment_request_id_raw, direction_id_raw, lessons_to_add_raw = callback.data.split("_")
    payment_request_id = int(payment_request_id_raw)
    direction_id = int(direction_id_raw)
    lessons_to_add = int(lessons_to_add_raw)

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
        _status,
        _approved_by,
        _rejected_by,
        _created_at,
        _updated_at,
    ) = payment

    lesson = get_student_lesson_by_id(direction_id)
    if not lesson:
        await callback.answer("❌ Направление не найдено", show_alert=True)
        return

    _, _, _, subject_name, lesson_balance_before, _, student_name, teacher_name = lesson

    finalized = finalize_payment_with_topup(
        payment_request_id=payment_request_id,
        direction_id=direction_id,
        lessons_count=lessons_to_add,
        admin_id=callback.from_user.id,
        comment=f"Начисление после подтверждения оплаты #{payment_request_id}",
    )
    if not finalized:
        payment_latest = get_payment_request_by_id(payment_request_id)
        latest_status = payment_latest[7] if payment_latest else "unknown"
        log_admin_action(
            admin_telegram_id=callback.from_user.id,
            action_type="payment_topup_failed",
            target_type="payment_request",
            target_id=payment_request_id,
            details=f"status={latest_status}",
            status="error",
        )
        await callback.answer(
            f"⚠️ Начисление не выполнено: оплата уже обработана (статус: {latest_status})",
            show_alert=True,
        )
        return

    log_admin_action(
        admin_telegram_id=callback.from_user.id,
        action_type="payment_topup_success",
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

    try:
        await callback.message.edit_caption(caption=approved_caption, parse_mode="HTML")
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    updated_lesson = get_student_lesson_by_id(direction_id)
    _, _, _, _, lesson_balance_after, _, _, _ = updated_lesson

    await callback.message.answer(
        f"✅ <b>Оплата #{payment_request_id} подтверждена</b>\n\n"
        f"👤 <b>Ученик:</b> {student_name}\n"
        f"📚 <b>Предмет:</b> {subject_name}\n"
        f"👨‍🏫 <b>Преподаватель:</b> {teacher_name}\n"
        f"📊 <b>Баланс был:</b> {lesson_balance_before}\n"
        f"➕ <b>Начислено:</b> {lessons_to_add}\n"
        f"📊 <b>Баланс стал:</b> {lesson_balance_after}",
        parse_mode="HTML"
    )

    if telegram_user_id:
        try:
            await callback.bot.send_message(
                telegram_user_id,
                f"✅ <b>Ваша оплата подтверждена!</b>\n\n"
                f"На баланс начислено {lessons_to_add} занятий.\n\n"
                f"📚 <b>Предмет:</b> {subject_name}\n"
                f"👨‍🏫 <b>Преподаватель:</b> {teacher_name}",
                parse_mode="HTML"
            )
        except Exception:
            pass

    await callback.answer("✅ Занятия начислены")


@router.message(lambda m: _is_private_chat(m) and _is_payment_moderator(m.from_user.id) and m.from_user.id in PENDING_MANUAL_TOPUPS)
@router.message(ApplicationForm.payment_manual_amount)
async def process_manual_payment_amount(message: Message, state: FSMContext):
    if not _is_payment_moderator(message.from_user.id):
        return

    if not message.text:
        return

    text = message.text.strip()
    if not text.isdigit():
        await message.answer("❓ Введите количество занятий числом.")
        return

    lessons_to_add = int(text)
    if lessons_to_add <= 0:
        await message.answer("❓ Количество занятий должно быть больше нуля.")
        return

    data = await state.get_data()
    payment_request_id = data.get("manual_payment_request_id")
    direction_id = data.get("manual_direction_id")
    if not payment_request_id or not direction_id:
        pending = PENDING_MANUAL_TOPUPS.get(message.from_user.id)
        if pending:
            payment_request_id, direction_id = pending

    if not payment_request_id or not direction_id:
        await message.answer("❌ Нет активного ручного начисления. Нажмите «Указать вручную» у нужной оплаты.")
        await state.clear()
        return

    payment = get_payment_request_by_id(payment_request_id)
    if not payment:
        await message.answer("❌ Запрос оплаты не найден.")
        await state.clear()
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
        await message.answer("❌ Направление не найдено.")
        await state.clear()
        return

    _, _, _, subject_name, lesson_balance_before, _, student_name, teacher_name = lesson

    finalized = finalize_payment_with_topup(
        payment_request_id=payment_request_id,
        direction_id=direction_id,
        lessons_count=lessons_to_add,
        admin_id=message.from_user.id,
        comment=f"Начисление после подтверждения оплаты #{payment_request_id} (вручную)",
    )
    if not finalized:
        payment_latest = get_payment_request_by_id(payment_request_id)
        latest_status = payment_latest[7] if payment_latest else "unknown"
        log_admin_action(
            admin_telegram_id=message.from_user.id,
            action_type="payment_manual_topup_failed",
            target_type="payment_request",
            target_id=payment_request_id,
            details=f"status={latest_status}",
            status="error",
        )
        await message.answer(
            f"⚠️ Не удалось завершить начисление: оплата уже обработана (статус: {latest_status})."
        )
        PENDING_MANUAL_TOPUPS.pop(message.from_user.id, None)
        await state.clear()
        return

    log_admin_action(
        admin_telegram_id=message.from_user.id,
        action_type="payment_manual_topup_success",
        target_type="payment_request",
        target_id=payment_request_id,
        details=f"direction={direction_id};lessons={lessons_to_add}",
        status="success",
    )

    updated_lesson = get_student_lesson_by_id(direction_id)
    _, _, _, _, lesson_balance_after, _, _, _ = updated_lesson

    await message.answer(
        f"✅ <b>Оплата #{payment_request_id} подтверждена</b>\n\n"
        f"👤 <b>Ученик:</b> {student_name}\n"
        f"📚 <b>Предмет:</b> {subject_name}\n"
        f"👨‍🏫 <b>Преподаватель:</b> {teacher_name}\n"
        f"📊 <b>Баланс был:</b> {lesson_balance_before}\n"
        f"➕ <b>Начислено:</b> {lessons_to_add}\n"
        f"📊 <b>Баланс стал:</b> {lesson_balance_after}",
        parse_mode="HTML"
    )

    if telegram_user_id:
        try:
            await message.bot.send_message(
                telegram_user_id,
                f"✅ <b>Ваша оплата подтверждена!</b>\n\n"
                f"На баланс начислено {lessons_to_add} занятий.\n\n"
                f"📚 <b>Предмет:</b> {subject_name}\n"
                f"👨‍🏫 <b>Преподаватель:</b> {teacher_name}",
                parse_mode="HTML"
            )
        except Exception:
            pass

    PENDING_MANUAL_TOPUPS.pop(message.from_user.id, None)
    await state.clear()
