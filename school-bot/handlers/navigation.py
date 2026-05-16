import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)

from keyboards import (
    get_main_menu_keyboard,
    get_teacher_subject_keyboard,
    get_offers_menu_keyboard,
    get_offer_application_keyboard,
    get_cabinet_keyboard,
    get_faq_menu_keyboard,
    get_faq_back_keyboard,
    get_user_type_keyboard,
)
from shared.database import get_teacher_catalog_subjects
from shared.database import (
    add_user,
    apply_promo_code_for_student,
    bind_student_telegram_by_id,
    bind_teacher_telegram_by_id,
    capture_referral,
    create_account_link_code,
    find_students_by_max_id,
    find_students_by_telegram_id,
    get_active_promo_for_user,
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
            "После проверки оплаты мы сообщим результат.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="← В меню", callback_data="back_to_menu")],
            ]),
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
    await state.set_state(ApplicationForm.name)
    try:
        await callback.message.edit_text(
            "👤 Как к вам обращаться? Напишите имя.",
            reply_markup=None,
        )
    except Exception:
        await callback.message.answer("👤 Как к вам обращаться? Напишите имя.")
    await callback.answer()


@router.callback_query(lambda c: c.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_type = data.get("user_type")
    await state.clear()
    if user_type:
        await state.update_data(user_type=user_type)
    await state.set_state(ApplicationForm.menu)
    try:
        await callback.message.edit_text(
            "📋 Пожалуйста, выберите нужный раздел:",
            reply_markup=get_main_menu_keyboard(),
        )
    except Exception:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(
            "📋 Пожалуйста, выберите нужный раздел:",
            reply_markup=get_main_menu_keyboard(),
        )
    await callback.answer()


@router.callback_query(lambda c: c.data == "no_teachers_available")
async def no_teachers_available(callback: CallbackQuery):
    await callback.answer("Преподаватели пока не добавлены.", show_alert=True)


@router.callback_query(ApplicationForm.menu, lambda c: c.data == "menu_teachers")
async def menu_teachers(callback: CallbackQuery, state: FSMContext):
    subjects = get_teacher_catalog_subjects()
    if not subjects:
        await callback.answer("Список преподавателей пока пуст.", show_alert=True)
        return
    try:
        await callback.message.edit_text(
            "Пожалуйста, выберите предмет:",
            reply_markup=get_teacher_subject_keyboard(subjects),
        )
    except Exception:
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

    try:
        await callback.message.delete()
    except Exception:
        pass
    await send_review_card(callback.message, 0, state)
    await state.set_state(ApplicationForm.review_card)
    await callback.answer()


@router.callback_query(ApplicationForm.menu, lambda c: c.data == "menu_cabinet")
async def menu_cabinet(callback: CallbackQuery, state: FSMContext):
    students = find_students_by_telegram_id(callback.from_user.id)

    if not students:
        await callback.answer(
            "❌ Вы не найдены в базе учеников. Обратитесь к администратору.",
            show_alert=True,
        )
        return

    student_id, student_name, _, _ = students[0]
    try:
        directions = get_student_directions(student_id)
        recent_payments = get_recent_payment_history_by_telegram_user(
            callback.from_user.id,
            limit=4,
        )
        has_debt = any(d[3] < 0 for d in directions)
        text = build_cabinet_text(student_name, directions, recent_payments, student_id=student_id)
        text += build_multi_students_warning(len(students))
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_cabinet_keyboard(has_debt))
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=get_cabinet_keyboard(has_debt))
    except Exception:
        logger.exception("menu_cabinet failed for user %s", callback.from_user.id)
        await callback.answer(
            "⚠️ Не удалось загрузить личный кабинет. Попробуйте ещё раз.",
            show_alert=True,
        )
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

    try:
        await callback.message.delete()
    except Exception:
        pass
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


@router.callback_query(ApplicationForm.teacher_card, lambda c: c.data == "teacher_back_to_subjects")
async def teacher_back_to_subjects(callback: CallbackQuery, state: FSMContext):
    subjects = get_teacher_catalog_subjects()
    try:
        await callback.message.edit_text(
            "Пожалуйста, выберите предмет:",
            reply_markup=get_teacher_subject_keyboard(subjects),
        )
    except Exception:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(
            "Пожалуйста, выберите предмет:",
            reply_markup=get_teacher_subject_keyboard(subjects),
        )
    await state.set_state(ApplicationForm.teacher_subject)
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
    share_text = "Занимайся с лучшими репетиторами! Запишись через мою ссылку и получи скидку на первое занятие 🎓"
    share_url = f"https://t.me/share/url?url={referral_link}&text={share_text}"
    text = (
        "🎁 <b>ВАШ РЕФЕРАЛЬНЫЙ КОД</b>\n\n"
        f"Ваша персональная ссылка:\n{referral_link}\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "📌 <b>Как это работает:</b>\n"
        "1️⃣ Отправьте ссылку другу\n"
        "2️⃣ Друг регистрируется и проходит бесплатную диагностику\n"
        "3️⃣ При первой оплате друг получает <b>скидку 20%</b>\n"
        "4️⃣ Вам начисляется <b>+1 занятие</b> в подарок\n\n"
        "Количество рефералов не ограничено — приглашайте всех! 🚀"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Поделиться ссылкой", url=share_url)],
        [InlineKeyboardButton(text="← В меню", callback_data="back_to_menu")],
    ])
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


def _offers_back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Оставить заявку", callback_data="menu_signup")],
        [InlineKeyboardButton(text="← Назад", callback_data="menu_offers")],
    ])


@router.callback_query(lambda c: c.data == "menu_offers")
async def show_offers(callback: CallbackQuery, state: FSMContext):
    text = "🎁 <b>СПЕЦИАЛЬНЫЕ ПРЕДЛОЖЕНИЯ</b>\n\nВыберите интересующее предложение:"
    try:
        await callback.message.edit_text(text, reply_markup=get_offers_menu_keyboard(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=get_offers_menu_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(lambda c: c.data == "offer_free_diagnosis")
async def show_free_diagnosis(callback: CallbackQuery):
    text = (
        "🎁 <b>БЕСПЛАТНАЯ ДИАГНОСТИКА</b>\n\n"
        "Первое занятие для новых учеников — <b>бесплатно</b>!\n\n"
        "✅ Определим уровень знаний\n"
        "✅ Подберём оптимальный план обучения\n"
        "✅ Ответим на все вопросы\n\n"
        "Оставьте заявку — мы свяжемся с вами."
    )
    try:
        await callback.message.edit_text(text, reply_markup=_offers_back_kb(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=_offers_back_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(lambda c: c.data == "offer_first_package")
async def show_first_package(callback: CallbackQuery):
    text = (
        "💰 <b>СКИДКА НА ПЕРВЫЙ ПАКЕТ</b>\n\n"
        "Выгодная скидка при оформлении первого пакета занятий для новых учеников.\n\n"
        "✅ Только первый пакет\n"
        "✅ Размер скидки уточняется при оформлении\n\n"
        "Оставьте заявку — мы свяжемся с вами."
    )
    try:
        await callback.message.edit_text(text, reply_markup=_offers_back_kb(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=_offers_back_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("apply_offer_"))
async def apply_offer(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ApplicationForm.user_type)
    try:
        await callback.message.edit_text(
            "👤 <b>Кто оставляет заявку?</b>\n\nВыберите вариант:",
            parse_mode="HTML",
            reply_markup=get_user_type_keyboard(),
        )
    except Exception:
        await callback.message.answer(
            "👤 <b>Кто оставляет заявку?</b>\n\nВыберите вариант:",
            parse_mode="HTML",
            reply_markup=get_user_type_keyboard(),
        )
    await callback.answer()


@router.callback_query(lambda c: c.data == "menu_faq")
async def show_faq_menu(callback: CallbackQuery):
    text = (
        "❓ <b>ПОМОЩЬ И ЧАСТЫЕ ВОПРОСЫ</b>\n\n"
        "Выберите интересующий вопрос — ниже короткие ответы. "
        "Если ответа нет, нажмите «← В меню» → «👤 Личный кабинет» → "
        "«✉️ Написать администратору»."
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_faq_menu_keyboard(),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            text,
            reply_markup=get_faq_menu_keyboard(),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(lambda c: c.data == "faq_pay")
async def faq_pay(callback: CallbackQuery):
    from config import (
        PAYMENT_ACCOUNT_HOLDER,
        PAYMENT_BANK_NAME,
        PAYMENT_BANK_NUMBER,
    )

    text = (
        "💳 <b>КАК ОПЛАТИТЬ ЗАНЯТИЯ</b>\n\n"
        "<b>Реквизиты:</b>\n"
        f"🏦 Номер счёта: <code>{PAYMENT_BANK_NUMBER}</code>\n"
        f"🏢 Банк: {PAYMENT_BANK_NAME}\n"
        f"👤 Получатель: {PAYMENT_ACCOUNT_HOLDER}\n\n"
        "<b>Порядок:</b>\n"
        "1. Сделайте перевод на указанные реквизиты.\n"
        "2. В комментарии к переводу укажите имя ученика.\n"
        "3. Откройте раздел оплаты в боте (👤 Личный кабинет → 💳 Оплата) "
        "и пришлите фото или PDF-чека.\n"
        "4. После проверки администратором занятия начислятся на ваш баланс, "
        "вам придёт уведомление.\n\n"
        "💡 Если у вас активна реферальная скидка 20%, она будет учтена. "
        "Заплатите на 20% меньше указанной в прайсе суммы."
    )
    await callback.message.edit_text(text, reply_markup=get_faq_back_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(lambda c: c.data == "faq_package")
async def faq_package(callback: CallbackQuery):
    text = (
        "📦 <b>ЧТО ТАКОЕ ПАКЕТ ЗАНЯТИЙ</b>\n\n"
        "Пакет — это сразу несколько занятий, оплаченных одним платежом, "
        "по более выгодной цене за одно занятие.\n\n"
        "Чем больше пакет, тем ниже цена за каждое занятие. "
        "Конкретные размеры пакетов и цены можно увидеть в разделе оплаты "
        "(👤 Личный кабинет → 💳 Оплата) — там приходит фото с актуальным "
        "прайсом.\n\n"
        "💡 Все занятия из пакета хранятся на вашем балансе и расходуются по мере "
        "проведения. Срок годности занятий не сгорает."
    )
    await callback.message.edit_text(text, reply_markup=get_faq_back_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(lambda c: c.data == "faq_reschedule")
async def faq_reschedule(callback: CallbackQuery):
    text = (
        "🔄 <b>ПЕРЕНОС И ОТМЕНА ЗАНЯТИЙ</b>\n\n"
        "Перенести или отменить занятие можно <b>не позже чем за 6 часов</b> "
        "до его начала. В этом случае занятие не списывается с баланса.\n\n"
        "Если предупредить менее чем за 6 часов (или вообще не прийти), "
        "<b>занятие списывается с баланса</b> как проведённое.\n\n"
        "<b>Куда сообщать:</b>\n"
        "• своему преподавателю напрямую,\n"
        "• или администратору школы (👤 Личный кабинет → ✉️ Написать "
        "администратору).\n\n"
        "💡 Чем раньше вы предупредите — тем проще найти удобный новый слот."
    )
    await callback.message.edit_text(text, reply_markup=get_faq_back_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(lambda c: c.data == "faq_promo")
async def faq_promo(callback: CallbackQuery):
    text = (
        "🎟 <b>ПРОМОКОДЫ</b>\n\n"
        "В школе «Интеграл» действуют два вида промокодов:\n\n"
        "<b>1. Скидка в рублях (₽)</b>\n"
        "Фиксированная сумма, вычитаемая из стоимости.\n"
        "✅ Применяется <b>ко всем типам оплаты</b>: разовые занятия и пакеты.\n\n"
        "<b>2. Процентная скидка (%)</b>\n"
        "Снижает стоимость занятия на указанный процент.\n"
        "❗ Действует <b>только для разовых занятий</b> — к пакетам не применяется.\n\n"
        "⏰ <b>Срок действия:</b> у промокода может быть указан срок — дата и время истечения. "
        "После этого момента промокод перестаёт работать.\n\n"
        "Промокод активируется автоматически при оплате — "
        "вы увидите его статус при переходе к оплате.\n\n"
        "💡 Если промокод не подходит к выбранному формату — бот сообщит об этом."
    )
    await callback.message.edit_text(text, reply_markup=get_faq_back_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(lambda c: c.data == "enter_promo")
async def enter_promo_start(callback: CallbackQuery, state: FSMContext):
    students = find_students_by_telegram_id(callback.from_user.id)
    if not students:
        await callback.answer("❌ Вы не найдены в базе учеников.", show_alert=True)
        return
    promo = get_active_promo_for_user(callback.from_user.id)
    if promo:
        _, code, dtype, dvalue, _ = promo
        unit = "%" if dtype == "percent" else "₽"
        try:
            await callback.message.edit_text(
                f"✅ У вас уже активен промокод <b>{code}</b> (скидка {int(float(dvalue))}{unit}).\n\n"
                "Для замены промокода обратитесь к администратору.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="← В меню", callback_data="back_to_menu")
                ]]),
            )
        except Exception:
            await callback.message.answer(
                f"✅ У вас уже активен промокод <b>{code}</b> (скидка {int(float(dvalue))}{unit}).\n\n"
                "Для замены промокода обратитесь к администратору.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="← В меню", callback_data="back_to_menu")
                ]]),
            )
        await callback.answer()
        return
    await state.set_state(ApplicationForm.entering_promo_code)
    promo_prompt_text = "🎟 <b>Введите промокод</b>\n\nНапишите код в следующем сообщении:"
    promo_prompt_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="← В меню", callback_data="back_to_menu")
    ]])
    try:
        await callback.message.edit_text(promo_prompt_text, parse_mode="HTML", reply_markup=promo_prompt_kb)
    except Exception:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(promo_prompt_text, parse_mode="HTML", reply_markup=promo_prompt_kb)
    await callback.answer()


@router.message(ApplicationForm.entering_promo_code)
async def enter_promo_code_input(message: Message, state: FSMContext):
    students = find_students_by_telegram_id(message.from_user.id)
    if not students:
        await state.set_state(ApplicationForm.menu)
        await message.answer("❌ Вы не найдены в базе учеников.")
        return
    student_id = students[0][0]
    code = (message.text or "").strip().upper()
    if not code:
        await message.answer("Введите текст промокода.")
        return
    ok, result = apply_promo_code_for_student(student_id, code)
    if ok:
        dtype, dvalue = result.split(":", 1)
        unit = "%" if dtype == "percent" else "₽"
        text = (
            f"✅ Промокод <b>{code}</b> активирован!\n"
            f"Скидка {int(float(dvalue))}{unit} будет применена при следующей оплате."
        )
    elif result == "not_found":
        text = f"❌ Промокод <b>{code}</b> не найден. Проверьте правильность написания."
    elif result == "inactive":
        text = f"❌ Промокод <b>{code}</b> деактивирован."
    elif result == "expired":
        text = f"❌ Срок действия промокода <b>{code}</b> истёк."
    elif result == "limit_reached":
        text = f"❌ Промокод <b>{code}</b> исчерпал лимит использований."
    elif result == "already_assigned":
        text = f"✅ Промокод <b>{code}</b> уже применён к вашему аккаунту."
    else:
        text = "⚠️ Произошла ошибка. Попробуйте позже или обратитесь к администратору."
    await state.set_state(ApplicationForm.menu)
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить занятия", callback_data="menu_paid")],
            [InlineKeyboardButton(text="← В меню", callback_data="back_to_menu")],
        ]),
    )


@router.callback_query(lambda c: c.data == "faq_referral")
async def faq_referral(callback: CallbackQuery):
    text = (
        "🎁 <b>РЕФЕРАЛЬНАЯ ПРОГРАММА — КРАТКО</b>\n\n"
        "<b>Вы приглашаете друга</b> по своей ссылке.\n\n"
        "Друг получает:\n"
        "✅ бесплатное диагностическое занятие;\n"
        "✅ скидку <b>20%</b> на своё первое платное занятие.\n\n"
        "Вы получаете:\n"
        "✅ <b>+1 бесплатное занятие</b> на свой баланс — после того, как друг "
        "оплатит первое занятие. Бонус автоматически списывается на ближайшем "
        "проведённом занятии.\n\n"
        "Свою ссылку возьмите в «👤 Личный кабинет» → «🎁 Мой реферальный код»."
    )
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🎁 Открыть мой реферальный код", callback_data="show_referral_code")],
                [InlineKeyboardButton(text="← К списку вопросов", callback_data="menu_faq")],
                [InlineKeyboardButton(text="← В меню", callback_data="back_to_menu")],
            ]
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "faq_link")
async def faq_link(callback: CallbackQuery):
    text = (
        "👤 <b>ПРИВЯЗКА АККАУНТА</b>\n\n"
        "Когда администратор заводит вашу карточку, он использует ваш Telegram "
        "@username или ссылку для автоматической привязки.\n\n"
        "Если в Личном кабинете написано «Мы пока не нашли вас в базе» — "
        "значит ваш Telegram-аккаунт ещё не привязан к карточке ученика. "
        "Напишите администратору (✉️ Написать администратору), и он привяжет "
        "вас вручную или пришлёт ссылку для автоматической привязки.\n\n"
        "💡 После смены @username в Telegram карточку нужно перепривязать — "
        "тоже через администратора."
    )
    await callback.message.edit_text(text, reply_markup=get_faq_back_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(lambda c: c.data == "link_max_start")
async def link_max_start(callback: CallbackQuery):
    telegram_id = callback.from_user.id

    # Check if already linked to a MAX account via any student card
    students = find_students_by_telegram_id(telegram_id)
    already_linked = any(s[0] and _student_has_max(s[0]) for s in students)
    if already_linked:
        await callback.message.answer(
            "✅ Ваш MAX-аккаунт уже подключён к этому кабинету.\n"
            "Если нужно привязать другой — обратитесь к администратору."
        )
        await callback.answer()
        return

    import os
    code = create_account_link_code(telegram_id)
    max_bot_username = os.getenv("SCHOOL_MAX_BOT_USERNAME", "")
    max_bot_hint = f"\nБот в MAX: @{max_bot_username}" if max_bot_username else ""
    await callback.message.answer(
        "🔗 <b>Подключение MAX</b>\n\n"
        "Откройте бот школы в MAX-мессенджере и введите команду:\n\n"
        f"<code>/link {code}</code>\n\n"
        f"Код действует <b>15 минут</b>.{max_bot_hint}",
        parse_mode="HTML",
    )
    await callback.answer()


def _student_has_max(student_id: int) -> bool:
    from shared.database import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT max_id FROM students WHERE id = ?", (student_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row and row[0])


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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Мой реферальный код", callback_data="show_referral_code")],
        [InlineKeyboardButton(text="← Назад", callback_data="menu_offers")],
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()
