from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_superadmin_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пользователи", callback_data="superadmin_section_users")],
            [InlineKeyboardButton(text="Учебный процесс", callback_data="superadmin_section_school")],
            [InlineKeyboardButton(text="Отчеты и журнал", callback_data="superadmin_section_reports")],
        ]
    )


def get_superadmin_users_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Добавить администратора", callback_data="superadmin_add_admin")],
            [InlineKeyboardButton(text="Добавить преподавателя", callback_data="superadmin_add_teacher")],
            [InlineKeyboardButton(text="Редактировать карточку преподавателя", callback_data="superadmin_edit_teacher")],
            [InlineKeyboardButton(text="Изменить роль пользователя", callback_data="superadmin_change_role")],
            [InlineKeyboardButton(text="Удалить пользователя", callback_data="admin_delete_user")],
            [InlineKeyboardButton(text="Список администраторов", callback_data="superadmin_list_admins")],
            [InlineKeyboardButton(text="Список преподавателей", callback_data="superadmin_list_teachers")],
            [InlineKeyboardButton(text="Назад", callback_data="superadmin_back_main")],
        ]
    )


def get_superadmin_school_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Добавить ученика", callback_data="admin_add_student")],
            [InlineKeyboardButton(text="Публикация ученикам", callback_data="admin_publication_new")],
            [InlineKeyboardButton(text="Сообщение в чат оплат", callback_data="admin_payment_chat_message")],
            [InlineKeyboardButton(text="Добавить карточку отзыва", callback_data="admin_review_new")],
            [InlineKeyboardButton(text="📋 Список отзывов", callback_data="admin_review_list")],
            [InlineKeyboardButton(text="Назначить предмет/преподавателя", callback_data="admin_assign_lesson")],
            [InlineKeyboardButton(text="Привязать Telegram преподавателя", callback_data="admin_bind_teacher_telegram")],
            [InlineKeyboardButton(text="Найти ученика", callback_data="admin_find_student")],
            [InlineKeyboardButton(text="Корректировка баланса", callback_data="admin_add_balance")],
            [InlineKeyboardButton(text="Посещаемость", callback_data="admin_attendance")],
            [InlineKeyboardButton(text="История баланса", callback_data="admin_balance_history")],
            [InlineKeyboardButton(text="Назад", callback_data="superadmin_back_main")],
        ]
    )


def get_superadmin_reports_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отчет по долгам", callback_data="admin_debt_report")],
            [InlineKeyboardButton(text="Занятия преподавателей", callback_data="admin_teacher_lessons_report")],
            [InlineKeyboardButton(text="Журнал действий", callback_data="admin_actions_recent")],
            [InlineKeyboardButton(text="Назад", callback_data="superadmin_back_main")],
        ]
    )


def get_admin_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Добавить ученика", callback_data="admin_add_student")],
            [InlineKeyboardButton(text="Публикация ученикам", callback_data="admin_publication_new")],
            [InlineKeyboardButton(text="Сообщение в чат оплат", callback_data="admin_payment_chat_message")],
            [InlineKeyboardButton(text="Добавить карточку отзыва", callback_data="admin_review_new")],
            [InlineKeyboardButton(text="📋 Список отзывов", callback_data="admin_review_list")],
            [InlineKeyboardButton(text="Назначить предмет/преподавателя", callback_data="admin_assign_lesson")],
            [InlineKeyboardButton(text="Привязать Telegram преподавателя", callback_data="admin_bind_teacher_telegram")],
            [InlineKeyboardButton(text="Удалить преподавателя/ученика", callback_data="admin_delete_user")],
            [InlineKeyboardButton(text="Найти ученика", callback_data="admin_find_student")],
            [InlineKeyboardButton(text="Корректировка баланса", callback_data="admin_add_balance")],
            [InlineKeyboardButton(text="Посещаемость", callback_data="admin_attendance")],
            [InlineKeyboardButton(text="История баланса", callback_data="admin_balance_history")],
            [InlineKeyboardButton(text="Отчет по долгам", callback_data="admin_debt_report")],
            [InlineKeyboardButton(text="Журнал действий", callback_data="admin_actions_recent")],
        ]
    )


def get_teacher_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Мои ученики", callback_data="teacher_students")],
            [InlineKeyboardButton(text="Отметить посещение", callback_data="teacher_attendance")],
        ]
    )


def get_student_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Мой профиль", callback_data="student_profile")],
            [InlineKeyboardButton(text="Мои направления", callback_data="student_directions")],
            [InlineKeyboardButton(text="История оплат", callback_data="student_payment_history")],
        ]
    )


def get_subject_selection_keyboard(subjects: list[str]):
    buttons = []
    for index, subject in enumerate(subjects[:20]):
        buttons.append(
            [InlineKeyboardButton(text=subject[:64], callback_data=f"assign_subject_pick_{index}")]
        )
    buttons.append([InlineKeyboardButton(text="➕ Добавить новый предмет", callback_data="assign_subject_add_new")])
    buttons.append([InlineKeyboardButton(text="Главное меню", callback_data="menu_home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_assign_subject_rename_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оставить как есть", callback_data="assign_subject_keep")],
            [InlineKeyboardButton(text="Переименовать для ученика", callback_data="assign_subject_rename")],
            [InlineKeyboardButton(text="Главное меню", callback_data="menu_home")],
        ]
    )


def get_teacher_subject_picker_keyboard(subjects: list[str]):
    buttons = []
    for index, subject in enumerate(subjects[:20]):
        buttons.append(
            [InlineKeyboardButton(text=subject[:64], callback_data=f"new_teacher_subject_pick_{index}")]
        )
    buttons.append([InlineKeyboardButton(text="Добавить новый предмет", callback_data="new_teacher_subject_add_new")])
    buttons.append([InlineKeyboardButton(text="Главное меню", callback_data="menu_home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_edit_teacher_subject_picker_keyboard(subjects: list[str]):
    buttons = []
    for index, subject in enumerate(subjects[:20]):
        buttons.append(
            [InlineKeyboardButton(text=subject[:64], callback_data=f"edit_teacher_subject_pick_{index}")]
        )
    buttons.append([InlineKeyboardButton(text="Добавить новый предмет", callback_data="edit_teacher_subject_add_new")])
    buttons.append([InlineKeyboardButton(text="Главное меню", callback_data="menu_home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_tariff_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Разовое", callback_data="tariff_single")],
            [InlineKeyboardButton(text="Пакет", callback_data="tariff_package")],
        ]
    )


def get_attendance_direction_keyboard(directions):
    buttons = []

    for direction in directions:
        direction_id, teacher_name, subject_name, lesson_balance, _tariff_type = direction
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{subject_name} — {teacher_name} (остаток: {lesson_balance})",
                    callback_data=f"attendance_direction_{direction_id}",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_teacher_attendance_students_keyboard(students):
    buttons = []
    for student in students:
        student_id, full_name = student[0], student[1]
        buttons.append(
            [
                InlineKeyboardButton(
                    text=full_name[:64],
                    callback_data=f"teacher_attendance_student_{student_id}",
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text="Главное меню", callback_data="menu_home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_attendance_mark_keyboard(direction_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Был", callback_data=f"attendance_present_{direction_id}")],
            [InlineKeyboardButton(text="Не был", callback_data=f"attendance_absent_{direction_id}")],
        ]
    )


def get_balance_direction_keyboard(directions):
    buttons = []

    for direction in directions:
        direction_id, teacher_name, subject_name, lesson_balance, _tariff_type = direction
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{subject_name} — {teacher_name} (остаток: {lesson_balance})",
                    callback_data=f"balance_direction_{direction_id}",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_balance_add_keyboard(direction_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="+1", callback_data=f"balance_add_{direction_id}_1")],
            [InlineKeyboardButton(text="+4", callback_data=f"balance_add_{direction_id}_4")],
            [InlineKeyboardButton(text="+8", callback_data=f"balance_add_{direction_id}_8")],
            [InlineKeyboardButton(text="+12", callback_data=f"balance_add_{direction_id}_12")],
            [InlineKeyboardButton(text="-1", callback_data=f"balance_add_{direction_id}_-1")],
            [InlineKeyboardButton(text="-4", callback_data=f"balance_add_{direction_id}_-4")],
            [InlineKeyboardButton(text="-8", callback_data=f"balance_add_{direction_id}_-8")],
            [InlineKeyboardButton(text="-12", callback_data=f"balance_add_{direction_id}_-12")],
        ]
    )


def get_teacher_bind_keyboard(teacher_names: list[str]):
    buttons = []

    for idx, teacher_name in enumerate(teacher_names):
        buttons.append(
            [InlineKeyboardButton(text=teacher_name, callback_data=f"bind_teacher_choose_{idx}")]
        )

    buttons.append([InlineKeyboardButton(text="Отмена", callback_data="admin_bind_teacher_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_role_change_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Сделать администратором", callback_data="role_set_admin")],
            [InlineKeyboardButton(text="Сделать преподавателем", callback_data="role_set_teacher")],
            [InlineKeyboardButton(text="Сделать учеником", callback_data="role_set_student")],
            [InlineKeyboardButton(text="Отключить доступ", callback_data="role_set_disabled")],
            [InlineKeyboardButton(text="Отмена", callback_data="role_set_cancel")],
        ]
    )


def get_main_menu_shortcut_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Главное меню", callback_data="menu_home")],
        ]
    )


def get_publication_schedule_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отправить сейчас", callback_data="publication_send_now")],
            [InlineKeyboardButton(text="Запланировать по времени", callback_data="publication_schedule_pick_time")],
            [InlineKeyboardButton(text="Главное меню", callback_data="menu_home")],
        ]
    )


def get_publication_audience_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Только ученикам", callback_data="publication_audience_students")],
            [InlineKeyboardButton(text="Ученикам + мне", callback_data="publication_audience_students_plus_me")],
            [InlineKeyboardButton(text="Только мне (тест)", callback_data="publication_audience_me_only")],
            [InlineKeyboardButton(text="Главное меню", callback_data="menu_home")],
        ]
    )


def get_user_selection_keyboard(users: list[tuple[int, str, str, str, str | None]], action_prefix: str):
    buttons = []
    for user_id, full_name, role, username, telegram_id in users[:20]:
        role_title = {
            "superadmin": "Суперадмин",
            "admin": "Админ",
            "teacher": "Преподаватель",
            "student": "Ученик",
        }.get(role, role)
        uname = f"@{username}" if username else "без username"
        text = f"{full_name} | {role_title} | {uname}"
        buttons.append(
            [InlineKeyboardButton(text=text[:64], callback_data=f"{action_prefix}_{user_id}")]
        )
    buttons.append([InlineKeyboardButton(text="Главное меню", callback_data="menu_home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_teacher_selection_keyboard(
    teachers: list[tuple[int, str, str, str | None]],
    action_prefix: str = "edit_teacher_pick",
):
    buttons = []
    for teacher_id, full_name, subject_name, username in teachers:
        subject = subject_name or "без предмета"
        uname = f"@{username}" if username else "без username"
        text = f"{full_name} | {subject} | {uname}"
        buttons.append(
            [InlineKeyboardButton(text=text[:64], callback_data=f"{action_prefix}_{teacher_id}")]
        )
    buttons.append([InlineKeyboardButton(text="Главное меню", callback_data="menu_home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_student_disambiguation_keyboard(
    students: list[tuple[int, str, int | None, str | None, str | None]],
    action_prefix: str,
):
    buttons = []
    for student_id, full_name, telegram_id, _phone, username in students[:30]:
        uname = f"@{username}" if username else "без username"
        tg_suffix = f"tg:{telegram_id}" if telegram_id else "tg:-"
        text = f"{full_name} | {uname} | {tg_suffix}"
        buttons.append(
            [InlineKeyboardButton(text=text[:64], callback_data=f"{action_prefix}_{student_id}")]
        )
    buttons.append([InlineKeyboardButton(text="Главное меню", callback_data="menu_home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
