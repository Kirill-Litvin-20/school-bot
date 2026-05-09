from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from shared.database import (
    get_teacher_catalog_name_subject_pairs,
    get_teacher_catalog_names,
    get_teacher_catalog_subjects,
)

SUBJECTS = [
    "Математика",
    "Русский язык",
    "Информатика",
    "Физика",
    "Обществознание",
    "Литература",
]


def get_all_teacher_names() -> list[str]:
    names: list[str] = []

    pairs = get_teacher_catalog_name_subject_pairs()
    if pairs:
        for full_name, subject_name in pairs:
            label = f"{full_name} - {subject_name}"
            if label not in names:
                names.append(label)
        return names

    for name in get_teacher_catalog_names():
        if name and name not in names:
            names.append(name)

    return names


def get_all_subject_names() -> list[str]:
    subjects: list[str] = []

    for subject in get_teacher_catalog_subjects():
        if subject and subject not in subjects:
            subjects.append(subject)

    for subject in SUBJECTS:
        if subject not in subjects:
            subjects.append(subject)

    return subjects


def get_back_button():
    return [InlineKeyboardButton(text="Назад", callback_data="back_step")]


def get_user_type_keyboard():
    buttons = [
        [InlineKeyboardButton(text="Ученик", callback_data="user_student")],
        [InlineKeyboardButton(text="Родитель", callback_data="user_parent")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_main_menu_keyboard():
    buttons = [
        [InlineKeyboardButton(text="📝 Оставить заявку", callback_data="menu_signup")],
        [InlineKeyboardButton(text="💳 Оплатить занятия", callback_data="menu_paid")],
        [InlineKeyboardButton(text="👨‍🏫 Преподаватели", callback_data="menu_teachers")],
        [InlineKeyboardButton(text="🎁 Акции и предложения", callback_data="menu_offers")],
        [InlineKeyboardButton(text="⭐ Отзывы", callback_data="menu_reviews")],
        [InlineKeyboardButton(text="👤 Личный кабинет", callback_data="menu_cabinet")],
        [InlineKeyboardButton(text="❓ Помощь и FAQ", callback_data="menu_faq")],
        [InlineKeyboardButton(text="📢 Канал школы", url="https://t.me/school_integral_ru")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_faq_menu_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Как оплатить", callback_data="faq_pay")],
            [InlineKeyboardButton(text="📦 Что такое пакет занятий", callback_data="faq_package")],
            [InlineKeyboardButton(text="🔄 Перенос и отмена занятий", callback_data="faq_reschedule")],
            [InlineKeyboardButton(text="🎁 Реферальная программа", callback_data="faq_referral")],
            [InlineKeyboardButton(text="👤 Привязка аккаунта", callback_data="faq_link")],
            [InlineKeyboardButton(text="← В меню", callback_data="back_to_menu")],
        ]
    )


def get_faq_back_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="← К списку вопросов", callback_data="menu_faq")],
            [InlineKeyboardButton(text="← В меню", callback_data="back_to_menu")],
        ]
    )


def get_teacher_subject_keyboard(subjects: list[str] | None = None):
    subject_list = subjects or get_all_subject_names()
    buttons = []
    if not subject_list:
        buttons.append(
            [InlineKeyboardButton(text="Преподаватели пока не добавлены", callback_data="no_teachers_available")]
        )
        buttons.append([InlineKeyboardButton(text="Р’ РјРµРЅСЋ", callback_data="back_to_menu")])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    for subject in subject_list:
        buttons.append(
            [InlineKeyboardButton(text=subject, callback_data=f"teacher_subject_{subject}")]
        )

    buttons.append([InlineKeyboardButton(text="В меню", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_teacher_card_keyboard(index: int, total: int):
    nav_buttons = []

    if index > 0:
        nav_buttons.append(InlineKeyboardButton(text="<", callback_data="teacher_prev"))
    if index < total - 1:
        nav_buttons.append(InlineKeyboardButton(text=">", callback_data="teacher_next"))

    buttons = []
    if nav_buttons:
        buttons.append(nav_buttons)

    buttons.append([InlineKeyboardButton(text="Оставить заявку", callback_data="teacher_signup")])
    buttons.append([InlineKeyboardButton(text="В меню", callback_data="back_to_menu")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_review_card_keyboard(index: int, total: int):
    nav_buttons = []

    if index > 0:
        nav_buttons.append(InlineKeyboardButton(text="<", callback_data="review_prev"))
    if index < total - 1:
        nav_buttons.append(InlineKeyboardButton(text=">", callback_data="review_next"))

    buttons = []
    if nav_buttons:
        buttons.append(nav_buttons)

    buttons.append([InlineKeyboardButton(text="В меню", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_class_keyboard():
    buttons = [
        [
            InlineKeyboardButton(text="5", callback_data="class_5"),
            InlineKeyboardButton(text="6", callback_data="class_6"),
            InlineKeyboardButton(text="7", callback_data="class_7"),
        ],
        [
            InlineKeyboardButton(text="8", callback_data="class_8"),
            InlineKeyboardButton(text="9", callback_data="class_9"),
            InlineKeyboardButton(text="10", callback_data="class_10"),
        ],
        [InlineKeyboardButton(text="11", callback_data="class_11")],
        get_back_button(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_goal_keyboard():
    buttons = [
        [InlineKeyboardButton(text="ОГЭ", callback_data="goal_ОГЭ")],
        [InlineKeyboardButton(text="ЕГЭ", callback_data="goal_ЕГЭ")],
        [InlineKeyboardButton(text="Успеваемость", callback_data="goal_Успеваемость")],
        get_back_button(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_lesson_type_keyboard():
    buttons = [
        [InlineKeyboardButton(text="Индивидуально", callback_data="lesson_individual")],
        [InlineKeyboardButton(text="Мини-группа", callback_data="lesson_group")],
        get_back_button(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_subjects_keyboard(selected_subjects: list[str]):
    buttons = []

    for subject in get_all_subject_names():
        prefix = "✅ " if subject in selected_subjects else ""
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{prefix}{subject}",
                    callback_data=f"subject_{subject}",
                )
            ]
        )

    buttons.append([InlineKeyboardButton(text="Готово", callback_data="subjects_done")])
    buttons.append(get_back_button())
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_teacher_choice_keyboard():
    buttons = [
        [InlineKeyboardButton(text="Подобрать преподавателя", callback_data="teacher_pick")],
        [InlineKeyboardButton(text="Выбрать конкретного", callback_data="teacher_specific")],
        get_back_button(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_teachers_keyboard():
    buttons = []
    teachers = get_all_teacher_names()
    if not teachers:
        buttons.append(
            [InlineKeyboardButton(text="Преподаватели пока не добавлены", callback_data="no_teachers_available")]
        )
        buttons.append(get_back_button())
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    for teacher in teachers:
        buttons.append(
            [InlineKeyboardButton(text=teacher, callback_data=f"teacher_{teacher}")]
        )

    buttons.append(get_back_button())
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_contact_method_keyboard():
    buttons = [
        [InlineKeyboardButton(text="Telegram", callback_data="contact_Telegram")],
        [InlineKeyboardButton(text="MAX", callback_data="contact_MAX")],
        [InlineKeyboardButton(text="Звонок", callback_data="contact_Звонок")],
        get_back_button(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_payment_check_keyboard(payment_request_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить",
                    callback_data=f"payment_approve_{payment_request_id}",
                ),
                InlineKeyboardButton(
                    text="Отклонить",
                    callback_data=f"payment_reject_{payment_request_id}",
                ),
            ]
        ]
    )


def get_payment_direction_keyboard(payment_request_id: int, directions):
    buttons = []

    for direction in directions:
        direction_id, teacher_name, subject_name, lesson_balance, _tariff_type = direction
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{subject_name} — {teacher_name} (остаток: {lesson_balance})",
                    callback_data=f"paydir_{payment_request_id}_{direction_id}",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_payment_topup_keyboard(payment_request_id: int, direction_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✨ +1",
                    callback_data=f"payadd_{payment_request_id}_{direction_id}_1",
                ),
                InlineKeyboardButton(
                    text="🎁 +4 (сэкономь)",
                    callback_data=f"payadd_{payment_request_id}_{direction_id}_4",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💎 +8 (выгодно)",
                    callback_data=f"payadd_{payment_request_id}_{direction_id}_8",
                ),
                InlineKeyboardButton(
                    text="🏆 +12 (максимум)",
                    callback_data=f"payadd_{payment_request_id}_{direction_id}_12",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📝 Указать вручную",
                    callback_data=f"paymanual_{payment_request_id}_{direction_id}",
                )
            ],
        ]
    )


def get_offers_menu_keyboard():
    """Меню акций и предложений"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Бесплатная диагностика", callback_data="offer_free_diagnosis")],
            [InlineKeyboardButton(text="💰 Скидка на первый пакет", callback_data="offer_first_package")],
            [InlineKeyboardButton(text="🤝 Реферальная программа", callback_data="offer_referral_program")],
            [InlineKeyboardButton(text="← Назад в меню", callback_data="back_to_menu")],
        ]
    )


def get_offer_application_keyboard(offer_type: str):
    """Клавиатура для оставления заявки по акции"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 Оставить заявку", callback_data=f"apply_offer_{offer_type}")],
            [InlineKeyboardButton(text="← Назад", callback_data="menu_offers")],
        ]
    )


def get_cabinet_keyboard():
    """Клавиатура для личного кабинета ученика"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✉️ Написать администратору", url="https://t.me/integral_school_ru")],
            [InlineKeyboardButton(text="🎁 Мой реферальный код", callback_data="show_referral_code")],
            [InlineKeyboardButton(text="← В меню", callback_data="back_to_menu")],
        ]
    )
