from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from keyboards import (
    get_main_menu_keyboard,
    get_teacher_subject_keyboard,
    get_offers_menu_keyboard,
    get_offer_application_keyboard,
    get_cabinet_keyboard,
)
from shared.database import get_teacher_catalog_subjects
from shared.database import (
    add_user,
    bind_student_telegram_by_id,
    bind_teacher_telegram_by_id,
    capture_referral,
    find_students_by_telegram_id,
    get_latest_student_by_username,
    get_onboarding_invite_by_token,
    get_recent_payment_history_by_telegram_user,
    get_student_directions,
    mark_onboarding_invite_used,
    normalize_telegram_username,
    upsert_known_telegram_user,
)
from states import ApplicationForm

from .common import (
    build_admin_contacts_text,
    build_cabinet_text,
    build_multi_students_warning,
    build_recent_payments_text,
    edit_review_card,
    edit_teacher_card,
    get_review_cards,
    get_teacher_cards_for_subject,
    send_review_card,
    send_teacher_card,
    show_main_menu,
)

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


@router.message(CommandStart())
async def start_handler(message: Message, state: FSMContext):
    await state.clear()
    username = normalize_telegram_username(message.from_user.username)
    upsert_known_telegram_user(
        telegram_id=message.from_user.id,
        telegram_username=username,
        full_name=message.from_user.full_name,
    )

    if username:
        student_by_username = get_latest_student_by_username(username)
        if student_by_username:
            student_id, student_name, student_telegram_id, _phone = student_by_username
            if not student_telegram_id:
                bind_student_telegram_by_id(
                    student_id=student_id,
                    telegram_id=message.from_user.id,
                    telegram_username=username,
                )
                add_user(
                    telegram_id=message.from_user.id,
                    full_name=student_name,
                    role="student",
                    telegram_username=username,
                )

    start_parts = (message.text or "").split(maxsplit=1)
    start_payload = start_parts[1].strip() if len(start_parts) > 1 else ""

    # Обработка реферальной ссылки
    referral_code = None
    if start_payload.lower().startswith("ref_"):
        referral_code = start_payload[4:].strip()
        await state.update_data(referral_code=referral_code)
        try:
            inviter_tg = int(referral_code)
        except (TypeError, ValueError):
            inviter_tg = None
        if inviter_tg:
            captured = capture_referral(
                inviter_telegram_id=inviter_tg,
                invitee_telegram_id=message.from_user.id,
            )
            if captured:
                await message.answer(
                    "🎉 Вы пришли по приглашению друга!\n\n"
                    "После того как вы оплатите своё первое занятие "
                    "(вне бесплатной диагностики), вы получите "
                    "<b>20% скидку</b> на этот платёж, "
                    "а пригласивший — бонусное занятие.",
                    parse_mode="HTML",
                )

    if start_payload.lower().startswith("invite_"):
        token = start_payload[len("invite_"):].strip()
        invite = get_onboarding_invite_by_token(token)
        if not invite:
            await message.answer("Ссылка приглашения недействительна или уже устарела.")
            return

        (
            invite_id,
            _token,
            invite_role,
            invite_full_name,
            invite_username,
            entity_type,
            entity_id,
            used_by_telegram_id,
        ) = invite

        if used_by_telegram_id:
            await message.answer("Эта ссылка уже была использована.")
            return

        if not username or username != invite_username:
            await message.answer(
                "Эта ссылка привязана к другому @username. "
                "Пожалуйста, войдите в Telegram с нужным аккаунтом и повторите."
            )
            return

        add_user(
            telegram_id=message.from_user.id,
            full_name=invite_full_name or message.from_user.full_name,
            role=invite_role,
            telegram_username=username,
        )

        if invite_role == "student" and entity_type == "student" and entity_id:
            bind_student_telegram_by_id(
                student_id=int(entity_id),
                telegram_id=message.from_user.id,
                telegram_username=username,
            )
        if invite_role == "teacher" and entity_type == "teacher" and entity_id:
            bind_teacher_telegram_by_id(
                teacher_id=int(entity_id),
                telegram_id=message.from_user.id,
            )

        mark_onboarding_invite_used(invite_id=int(invite_id), telegram_id=message.from_user.id)
        await message.answer("Профиль успешно привязан. Доступ обновлен, используйте /start еще раз.")
        return

    if start_payload.lower() == "pay":
        await message.answer(
            "Здравствуйте!\n\n"
            "Пожалуйста, отправьте фото или скриншот чека об оплате.\n\n"
            "После проверки оплаты мы сообщим результат."
        )
        await state.set_state(ApplicationForm.payment_proof)
        return
    await message.answer(
        "Здравствуйте! Пожалуйста, укажите, кто будет оставлять заявку.",
        reply_markup=get_main_menu_keyboard(),
    )
    await state.set_state(ApplicationForm.menu)


@router.message(Command("menu"))
async def menu_command_handler(message: Message, state: FSMContext):
    await show_main_menu(message, state)


@router.callback_query(
    ApplicationForm.user_type, lambda c: c.data in ["user_student", "user_parent"]
)
async def choose_user_type(callback: CallbackQuery, state: FSMContext):
    user_type_map = {"user_student": "Ученик", "user_parent": "Родитель"}

    await state.update_data(user_type=user_type_map[callback.data])

    await callback.message.answer(
        "Благодарим. Теперь, пожалуйста, выберите раздел:",
        reply_markup=get_main_menu_keyboard(),
    )
    await state.set_state(ApplicationForm.menu)
    await callback.answer()


@router.callback_query(lambda c: c.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    await show_main_menu(callback.message, state)
    await callback.answer()


@router.callback_query(lambda c: c.data == "no_teachers_available")
async def no_teachers_available(callback: CallbackQuery):
    await callback.answer("Преподаватели пока не добавлены.", show_alert=True)


@router.callback_query(ApplicationForm.menu, lambda c: c.data == "menu_teachers")
async def menu_teachers(callback: CallbackQuery, state: FSMContext):
    subjects = get_teacher_catalog_subjects()
    if not subjects:
        await callback.message.answer(
            "Список преподавателей пока пуст. Пожалуйста, обратитесь к администратору."
        )
        await callback.answer()
        return
    await callback.message.answer(
        "Пожалуйста, выберите предмет:",
        reply_markup=get_teacher_subject_keyboard(subjects),
    )
    await state.set_state(ApplicationForm.teacher_subject)
    await callback.answer()


@router.callback_query(ApplicationForm.menu, lambda c: c.data == "menu_reviews")
async def menu_reviews(callback: CallbackQuery, state: FSMContext):
    reviews = get_review_cards()

    if not reviews:
        await callback.message.answer("Отзывы пока не добавлены.")
        await callback.answer()
        return

    await send_review_card(callback.message, 0, state)
    await state.set_state(ApplicationForm.review_card)
    await callback.answer()


@router.callback_query(ApplicationForm.menu, lambda c: c.data == "menu_cabinet")
async def menu_cabinet(callback: CallbackQuery, state: FSMContext):
    students = find_students_by_telegram_id(callback.from_user.id)

    if not students:
        await callback.message.answer(
            "❌ Мы пока не нашли Вас в базе учеников.\n"
            "Пожалуйста, обратитесь к администратору, чтобы привязать Telegram ID к Вашей карточке."
        )
        await callback.answer()
        return

    student_id, student_name, _, _ = students[0]
    directions = get_student_directions(student_id)
    recent_payments = get_recent_payment_history_by_telegram_user(
        callback.from_user.id,
        limit=4,
    )

    text = build_cabinet_text(student_name, directions, recent_payments, student_id=student_id)
    text += build_multi_students_warning(len(students))
    await callback.message.answer(text, parse_mode="HTML", reply_markup=get_cabinet_keyboard())
    await callback.answer()


@router.callback_query(
    ApplicationForm.review_card, lambda c: c.data in ["review_prev", "review_next"]
)
async def navigate_reviews(callback: CallbackQuery, state: FSMContext):
    reviews = get_review_cards()
    data = await state.get_data()
    index = data["selected_review_index"]

    if callback.data == "review_prev" and index > 0:
        index -= 1
    elif callback.data == "review_next" and index < len(reviews) - 1:
        index += 1

    await edit_review_card(callback, index, state)
    await callback.answer()


@router.callback_query(
    ApplicationForm.teacher_subject, lambda c: c.data.startswith("teacher_subject_")
)
async def choose_teacher_subject(callback: CallbackQuery, state: FSMContext):
    subject = callback.data.split("teacher_subject_", 1)[1]
    teachers = get_teacher_cards_for_subject(subject)
    if not teachers:
        await callback.answer(
            "По этому предмету преподаватели пока не добавлены.",
            show_alert=True,
        )
        return

    await send_teacher_card(callback.message, subject, 0, state)
    await state.set_state(ApplicationForm.teacher_card)
    await callback.answer()


@router.callback_query(
    ApplicationForm.teacher_card, lambda c: c.data in ["teacher_prev", "teacher_next"]
)
async def navigate_teacher_cards(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    subject = data["selected_teacher_subject"]
    index = data["selected_teacher_index"]
    teachers = get_teacher_cards_for_subject(subject)

    if callback.data == "teacher_prev" and index > 0:
        index -= 1
    elif callback.data == "teacher_next" and index < len(teachers) - 1:
        index += 1

    try:
        await edit_teacher_card(callback, subject, index, state)
    except Exception:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await send_teacher_card(callback.message, subject, index, state)
    await callback.answer()


@router.callback_query(ApplicationForm.teacher_card, lambda c: c.data == "teacher_signup")
async def signup_from_teacher_card(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    subject = data["selected_teacher_subject"]
    index = data["selected_teacher_index"]
    teachers = get_teacher_cards_for_subject(subject)
    if index < 0 or index >= len(teachers):
        await callback.answer("Карточка преподавателя не найдена.", show_alert=True)
        return
    teacher = teachers[index]
    user_type = data.get("user_type")

    await state.clear()

    if user_type:
        await state.update_data(user_type=user_type)

    await state.update_data(
        from_teacher_card=True,
        subjects=[subject],
        teacher_choice="Выбрать конкретного",
        teacher_name=teacher["name"],
    )

    await callback.message.answer("Пожалуйста, напишите, как к Вам обращаться.")
    await state.set_state(ApplicationForm.name)
    await callback.answer()


@router.message(Command("photo_id"))
async def get_photo_id(message: Message):
    """Команда для получения file_id фото для оплаты"""
    await message.answer(
        "📸 <b>Для получения file_id фото:</b>\n\n"
        "1. Загрузи фото сюда (в этот чат)\n"
        "2. Я выведу file_id которое нужно скопировать"
    )
    await message.answer(
        "⏳ <i>Жду фото...</i>",
        parse_mode="HTML"
    )


@router.callback_query(lambda c: c.data == "show_referral_code")
async def show_referral_code(callback: CallbackQuery):
    telegram_id = callback.from_user.id
    referral_link = f"https://t.me/integral_school_ru_bot?start=ref_{telegram_id}"

    await callback.message.answer(
        "🎁 <b>ВАШ РЕФЕРАЛЬНЫЙ КОД</b>\n\n"
        "Отправьте эту ссылку друзьям:\n\n"
        f"<code>{referral_link}</code>\n\n"
        "<b>Как это работает:</b>\n"
        "1️⃣ Друг переходит по вашей ссылке и оставляет заявку.\n"
        "2️⃣ Проходит бесплатную диагностику.\n"
        "3️⃣ Оплачивает первое платное занятие со <b>скидкой 20%</b>.\n"
        "4️⃣ После подтверждения этой оплаты вам автоматически "
        "начисляется <b>+1 бесплатное занятие</b>.\n\n"
        "Бонус будет списан с ближайшего проведённого занятия.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "menu_offers")
async def show_offers(callback: CallbackQuery):
    """Показать меню с акциями"""
    text = (
        "🎁 <b>СПЕЦИАЛЬНЫЕ ПРЕДЛОЖЕНИЯ</b>\n\n"
        "Мы дарим отличные возможности для новых учеников:\n"
    )
    await callback.message.edit_text(text, reply_markup=get_offers_menu_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(lambda c: c.data == "offer_free_diagnosis")
async def show_free_diagnosis(callback: CallbackQuery):
    """Показать информацию о бесплатной диагностике"""
    text = (
        "🎁 <b>БЕСПЛАТНАЯ ДИАГНОСТИКА</b>\n\n"
        "Мы предлагаем новым ученикам совершенно <b>бесплатное первое занятие</b> - диагностику!\n\n"
        "<b>Что это такое?</b>\n"
        "✅ Определим ваш уровень знаний\n"
        "✅ Поймём ваши цели и задачи\n"
        "✅ Подберём оптимальный план обучения\n"
        "✅ Ответим на все вопросы\n\n"
        "<b>Как это работает?</b>\n"
        "1. Вы оставляете заявку\n"
        "2. Мы свяжемся с вами\n"
        "3. Проводим диагностическое занятие\n"
        "4. Обсуждаем результаты и план\n\n"
        "<i>Скидка доступна только один раз для новых учеников!</i>"
    )
    await callback.message.edit_text(text, reply_markup=get_offer_application_keyboard("free_diagnosis"), parse_mode="HTML")
    await callback.answer()


@router.callback_query(lambda c: c.data == "offer_first_package")
async def show_first_package(callback: CallbackQuery):
    """Показать информацию о скидке на первый пакет"""
    text = (
        "💰 <b>СКИДКА НА ПЕРВЫЙ ПАКЕТ</b>\n\n"
        "Получите выгодную скидку при оформлении <b>первого пакета занятий</b>!\n\n"
        "Мы верим в качество и хотим, чтобы вы попробовали обучение с лучшей стороны.\n\n"
        "<b>Условия:</b>\n"
        "✅ Скидка применяется только на первый пакет\n"
        "✅ Действует для всех новых учеников\n"
        "✅ Размер скидки уточняется при оформлении\n\n"
        "<i>Не пропустите эту выгодную предложение!</i>"
    )
    await callback.message.edit_text(text, reply_markup=get_offer_application_keyboard("first_package"), parse_mode="HTML")
    await callback.answer()


@router.callback_query(lambda c: c.data == "offer_referral_program")
async def show_referral_program(callback: CallbackQuery):
    """Показать информацию о реферальной программе"""
    text = (
        "🤝 <b>РЕФЕРАЛЬНАЯ ПРОГРАММА</b>\n\n"
        "Приглашайте друзей в школу и получайте бонусные занятия!\n\n"
        "<b>Что получает приглашённый:</b>\n"
        "✅ Бесплатную диагностику (1 занятие на балансе сразу при заведении).\n"
        "✅ <b>Скидку 20%</b> на первое платное занятие после диагностики.\n\n"
        "<b>Что получаете вы:</b>\n"
        "✅ <b>+1 бесплатное занятие</b> после того, как приглашённый оплатит "
        "своё первое занятие. Бонус автоматически списывается на ближайшем "
        "проведённом занятии.\n\n"
        "<b>Где взять свою ссылку?</b>\n"
        "Откройте «👤 Личный кабинет» → «🎁 Мой реферальный код». "
        "Скопируйте ссылку и отправьте другу.\n\n"
        "Уведомление о начисленном бонусе придёт автоматически в этот чат."
    )
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🎁 Открыть мой реферальный код", callback_data="show_referral_code")],
                [InlineKeyboardButton(text="← Назад", callback_data="menu_offers")],
            ]
        ),
        parse_mode="HTML",
    )
    await callback.answer()
