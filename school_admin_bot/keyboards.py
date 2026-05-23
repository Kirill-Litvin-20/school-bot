import os

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def _sheets_url() -> str | None:
    sid = os.getenv("SHEETS_SPREADSHEET_ID", "").strip()
    return f"https://docs.google.com/spreadsheets/d/{sid}" if sid else None


# ─────────────────────────────────────────────────────────────
#  SUPERADMIN MENUS
# ─────────────────────────────────────────────────────────────

def get_superadmin_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            # Быстрый доступ
            [
                InlineKeyboardButton(text="🔍 Найти ученика", callback_data="admin_find_student"),
                InlineKeyboardButton(text="✅ Посещаемость",  callback_data="admin_attendance"),
            ],
            [
                InlineKeyboardButton(text="📊 Дашборд",       callback_data="admin_dashboard"),
                InlineKeyboardButton(text="💳 Баланс",        callback_data="admin_add_balance"),
            ],
            # Разделы
            [InlineKeyboardButton(text="👥 Персонал",              callback_data="superadmin_section_users")],
            [InlineKeyboardButton(text="📚 Учебный процесс",       callback_data="superadmin_section_school")],
            [InlineKeyboardButton(text="💰 Финансы и промокоды",   callback_data="superadmin_section_finance")],
            [InlineKeyboardButton(text="📊 Отчёты и таблица",      callback_data="superadmin_section_reports")],
        ]
    )


def get_superadmin_users_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👤 Добавить адм.",     callback_data="superadmin_add_admin"),
                InlineKeyboardButton(text="👨‍🏫 Добавить препода", callback_data="superadmin_add_teacher"),
            ],
            [
                InlineKeyboardButton(text="✏️ Редактировать карточку", callback_data="superadmin_edit_teacher"),
            ],
            [
                InlineKeyboardButton(text="🔄 Изменить роль",    callback_data="superadmin_change_role"),
                InlineKeyboardButton(text="🗑️ Удалить",          callback_data="admin_delete_user"),
            ],
            [
                InlineKeyboardButton(text="📋 Список адм.",       callback_data="superadmin_list_admins"),
                InlineKeyboardButton(text="📋 Список преподов",   callback_data="superadmin_list_teachers"),
            ],
            [InlineKeyboardButton(text="← Назад", callback_data="superadmin_back_main")],
        ]
    )


def get_superadmin_school_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            # Ученики
            [
                InlineKeyboardButton(text="👶 Добавить ученика",  callback_data="admin_add_student"),
                InlineKeyboardButton(text="🔍 Найти ученика",     callback_data="admin_find_student"),
            ],
            # Обучение
            [
                InlineKeyboardButton(text="✏️ Назначить предмет", callback_data="admin_assign_lesson"),
                InlineKeyboardButton(text="✅ Посещаемость",      callback_data="admin_attendance"),
            ],
            [
                InlineKeyboardButton(text="📱 Привязать Telegram препода", callback_data="admin_bind_teacher_telegram"),
            ],
            # Контент
            [
                InlineKeyboardButton(text="📝 Добавить отзыв",   callback_data="admin_review_new"),
                InlineKeyboardButton(text="📋 Список отзывов",   callback_data="admin_review_list"),
            ],
            [
                InlineKeyboardButton(text="📢 Публикация",       callback_data="admin_publication_new"),
                InlineKeyboardButton(text="💬 Чат оплат",        callback_data="admin_payment_chat_message"),
            ],
            [InlineKeyboardButton(text="← Назад", callback_data="superadmin_back_main")],
        ]
    )


def get_superadmin_finance_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💳 Корректировка баланса", callback_data="admin_add_balance"),
                InlineKeyboardButton(text="📜 История баланса",       callback_data="admin_balance_history"),
            ],
            [InlineKeyboardButton(text="🎟 Промокоды",               callback_data="admin_promo_list")],
            [InlineKeyboardButton(text="← Назад", callback_data="superadmin_back_main")],
        ]
    )


def get_superadmin_reports_menu():
    sheets_url = _sheets_url()
    sheets_row = [InlineKeyboardButton(text="🔄 Обновить всё", callback_data="sheets_refresh_all")]
    if sheets_url:
        sheets_row.append(InlineKeyboardButton(text="📂 Открыть таблицу", url=sheets_url))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Отчёт по долгам",      callback_data="admin_debt_report"),
                InlineKeyboardButton(text="👨‍🏫 Занятия преподов",    callback_data="admin_teacher_lessons_report"),
            ],
            [InlineKeyboardButton(text="📋 Журнал действий",          callback_data="admin_actions_recent")],
            sheets_row,
            [
                InlineKeyboardButton(text="💰 Балансы",     callback_data="sheets_refresh_balances"),
                InlineKeyboardButton(text="📆 Выплаты",     callback_data="sheets_refresh_payouts"),
                InlineKeyboardButton(text="📈 Статистика",  callback_data="sheets_refresh_stats"),
            ],
            [
                InlineKeyboardButton(text="💵 Выручка",     callback_data="sheets_refresh_revenue"),
                InlineKeyboardButton(text="💳 Пополнения",  callback_data="sheets_refresh_topups"),
                InlineKeyboardButton(text="🎟 Промокоды",   callback_data="sheets_refresh_discounts"),
            ],
            [InlineKeyboardButton(text="← Назад", callback_data="superadmin_back_main")],
        ]
    )


# ─────────────────────────────────────────────────────────────
#  ADMIN MENUS
# ─────────────────────────────────────────────────────────────

def get_admin_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            # Быстрый доступ
            [
                InlineKeyboardButton(text="🔍 Найти ученика", callback_data="admin_find_student"),
                InlineKeyboardButton(text="✅ Посещаемость",  callback_data="admin_attendance"),
            ],
            [InlineKeyboardButton(text="📊 Дашборд",          callback_data="admin_dashboard")],
            # Разделы
            [
                InlineKeyboardButton(text="👥 Управление",    callback_data="admin_section_management"),
                InlineKeyboardButton(text="📚 Учёба",         callback_data="admin_section_education"),
            ],
            [
                InlineKeyboardButton(text="💰 Финансы",       callback_data="admin_section_finance"),
                InlineKeyboardButton(text="📊 Отчёты",        callback_data="admin_section_reports"),
            ],
        ]
    )


def get_admin_management_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👶 Добавить ученика", callback_data="admin_add_student"),
                InlineKeyboardButton(text="🔍 Найти ученика",    callback_data="admin_find_student"),
            ],
            [InlineKeyboardButton(text="🗑️ Удалить пользователя",      callback_data="admin_delete_user")],
            [InlineKeyboardButton(text="📱 Привязать Telegram препода", callback_data="admin_bind_teacher_telegram")],
            [InlineKeyboardButton(text="← Назад", callback_data="admin_back_main")],
        ]
    )


def get_admin_education_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Назначить предмет/преподавателя", callback_data="admin_assign_lesson")],
            [InlineKeyboardButton(text="✅ Посещаемость",                    callback_data="admin_attendance")],
            [
                InlineKeyboardButton(text="📝 Добавить отзыв",  callback_data="admin_review_new"),
                InlineKeyboardButton(text="📋 Список отзывов",  callback_data="admin_review_list"),
            ],
            [InlineKeyboardButton(text="← Назад", callback_data="admin_back_main")],
        ]
    )


def get_admin_finance_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💳 Корректировка баланса", callback_data="admin_add_balance"),
                InlineKeyboardButton(text="📜 История баланса",       callback_data="admin_balance_history"),
            ],
            [
                InlineKeyboardButton(text="💬 Чат оплат",   callback_data="admin_payment_chat_message"),
                InlineKeyboardButton(text="🎟 Промокоды",   callback_data="admin_promo_list"),
            ],
            [InlineKeyboardButton(text="← Назад", callback_data="admin_back_main")],
        ]
    )


def get_admin_reports_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Отчёт по долгам",  callback_data="admin_debt_report"),
                InlineKeyboardButton(text="📋 Журнал действий",  callback_data="admin_actions_recent"),
            ],
            [InlineKeyboardButton(text="📢 Публикация ученикам", callback_data="admin_publication_new")],
            [InlineKeyboardButton(text="← Назад", callback_data="admin_back_main")],
        ]
    )


# ─────────────────────────────────────────────────────────────
#  TEACHER / STUDENT MENUS
# ─────────────────────────────────────────────────────────────

def get_teacher_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Мои ученики",         callback_data="teacher_students")],
            [InlineKeyboardButton(text="✅ Отметить посещение",  callback_data="teacher_attendance")],
            [InlineKeyboardButton(text="📊 Мой отчёт за неделю", callback_data="teacher_weekly_report")],
        ]
    )


def get_student_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Мой профиль",       callback_data="student_profile")],
            [InlineKeyboardButton(text="Мои направления",   callback_data="student_directions")],
            [InlineKeyboardButton(text="История оплат",     callback_data="student_payment_history")],
        ]
    )


# ─────────────────────────────────────────────────────────────
#  PROMO KEYBOARDS
# ─────────────────────────────────────────────────────────────

def get_promo_discount_type_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="% Процентная скидка",   callback_data="promo_type_percent"),
                InlineKeyboardButton(text="₽ Фиксированная сумма", callback_data="promo_type_fixed_rub"),
            ],
            [InlineKeyboardButton(text="Отмена", callback_data="admin_promo_list")],
        ]
    )


# ─────────────────────────────────────────────────────────────
#  ASSIGNMENT / SUBJECT KEYBOARDS
# ─────────────────────────────────────────────────────────────

def get_subject_selection_keyboard(subjects: list[str]):
    buttons = []
    for index, subject in enumerate(subjects[:20]):
        buttons.append(
            [InlineKeyboardButton(text=subject[:64], callback_data=f"assign_subject_pick_{index}")]
        )
    buttons.append([InlineKeyboardButton(text="➕ Добавить новый предмет", callback_data="assign_subject_add_new")])
    buttons.append([InlineKeyboardButton(text="← Главное меню", callback_data="menu_home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_assign_subject_rename_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оставить как есть",          callback_data="assign_subject_keep")],
            [InlineKeyboardButton(text="Переименовать для ученика",   callback_data="assign_subject_rename")],
            [InlineKeyboardButton(text="← Главное меню",             callback_data="menu_home")],
        ]
    )


def get_teacher_subject_picker_keyboard(subjects: list[str]):
    buttons = []
    for index, subject in enumerate(subjects[:20]):
        buttons.append(
            [InlineKeyboardButton(text=subject[:64], callback_data=f"new_teacher_subject_pick_{index}")]
        )
    buttons.append([InlineKeyboardButton(text="Добавить новый предмет", callback_data="new_teacher_subject_add_new")])
    buttons.append([InlineKeyboardButton(text="← Главное меню",         callback_data="menu_home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_edit_teacher_subject_picker_keyboard(subjects: list[str]):
    buttons = []
    for index, subject in enumerate(subjects[:20]):
        buttons.append(
            [InlineKeyboardButton(text=subject[:64], callback_data=f"edit_teacher_subject_pick_{index}")]
        )
    buttons.append([InlineKeyboardButton(text="Добавить новый предмет", callback_data="edit_teacher_subject_add_new")])
    buttons.append([InlineKeyboardButton(text="← Главное меню",         callback_data="menu_home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_tariff_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Разовое", callback_data="tariff_single"),
                InlineKeyboardButton(text="Пакет",   callback_data="tariff_package"),
            ],
        ]
    )


# ─────────────────────────────────────────────────────────────
#  ATTENDANCE KEYBOARDS
# ─────────────────────────────────────────────────────────────

def get_attendance_direction_keyboard(directions):
    buttons = []
    for direction in directions:
        direction_id, teacher_name, subject_name, lesson_balance, _tariff_type = direction
        buttons.append([
            InlineKeyboardButton(
                text=f"{subject_name} — {teacher_name} (остаток: {lesson_balance})",
                callback_data=f"attendance_direction_{direction_id}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_teacher_attendance_students_keyboard(students):
    buttons = []
    for student in students:
        student_id, full_name = student[0], student[1]
        buttons.append([
            InlineKeyboardButton(
                text=full_name[:64],
                callback_data=f"teacher_attendance_student_{student_id}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="← Главное меню", callback_data="menu_home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_attendance_mark_keyboard(direction_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Был",    callback_data=f"attendance_present_{direction_id}"),
                InlineKeyboardButton(text="❌ Не был", callback_data=f"attendance_absent_{direction_id}"),
            ],
        ]
    )


# ─────────────────────────────────────────────────────────────
#  BALANCE KEYBOARDS
# ─────────────────────────────────────────────────────────────

def get_balance_direction_keyboard(directions):
    buttons = []
    for direction in directions:
        direction_id, teacher_name, subject_name, lesson_balance, _tariff_type = direction
        buttons.append([
            InlineKeyboardButton(
                text=f"{subject_name} — {teacher_name} (остаток: {lesson_balance})",
                callback_data=f"balance_direction_{direction_id}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_balance_add_keyboard(direction_id: int):
    d = direction_id
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="+1",  callback_data=f"balance_add_{d}_1"),
                InlineKeyboardButton(text="+4",  callback_data=f"balance_add_{d}_4"),
                InlineKeyboardButton(text="+8",  callback_data=f"balance_add_{d}_8"),
                InlineKeyboardButton(text="+12", callback_data=f"balance_add_{d}_12"),
            ],
            [
                InlineKeyboardButton(text="-1",  callback_data=f"balance_add_{d}_-1"),
                InlineKeyboardButton(text="-4",  callback_data=f"balance_add_{d}_-4"),
                InlineKeyboardButton(text="-8",  callback_data=f"balance_add_{d}_-8"),
                InlineKeyboardButton(text="-12", callback_data=f"balance_add_{d}_-12"),
            ],
        ]
    )


# ─────────────────────────────────────────────────────────────
#  USER / TEACHER SELECTION
# ─────────────────────────────────────────────────────────────

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
            [InlineKeyboardButton(text="Сделать преподавателем",  callback_data="role_set_teacher")],
            [InlineKeyboardButton(text="Сделать учеником",        callback_data="role_set_student")],
            [InlineKeyboardButton(text="Отключить доступ",        callback_data="role_set_disabled")],
            [InlineKeyboardButton(text="Отмена",                  callback_data="role_set_cancel")],
        ]
    )


def get_main_menu_shortcut_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="← Главное меню", callback_data="menu_home")],
        ]
    )


def get_user_selection_keyboard(users: list[tuple[int, str, str, str, str | None]], action_prefix: str):
    buttons = []
    for user_id, full_name, role, username, telegram_id in users[:20]:
        role_title = {
            "superadmin": "Суперадмин",
            "admin": "Админ",
            "teacher": "Препод",
            "student": "Ученик",
        }.get(role, role)
        uname = f"@{username}" if username else "—"
        text = f"{full_name} · {role_title} · {uname}"
        buttons.append(
            [InlineKeyboardButton(text=text[:64], callback_data=f"{action_prefix}_{user_id}")]
        )
    buttons.append([InlineKeyboardButton(text="← Главное меню", callback_data="menu_home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_teacher_selection_keyboard(
    teachers: list[tuple[int, str, str, str | None]],
    action_prefix: str = "edit_teacher_pick",
):
    buttons = []
    for teacher_id, full_name, subject_name, username in teachers:
        subject = subject_name or "без предмета"
        uname = f"@{username}" if username else "—"
        text = f"{full_name} · {subject} · {uname}"
        buttons.append(
            [InlineKeyboardButton(text=text[:64], callback_data=f"{action_prefix}_{teacher_id}")]
        )
    buttons.append([InlineKeyboardButton(text="← Главное меню", callback_data="menu_home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_student_disambiguation_keyboard(
    students: list[tuple[int, str, int | None, str | None, str | None]],
    action_prefix: str,
):
    buttons = []
    for student_id, full_name, telegram_id, _phone, username in students[:30]:
        uname = f"@{username}" if username else "—"
        tg = f"tg:{telegram_id}" if telegram_id else "tg:—"
        text = f"{full_name} · {uname} · {tg}"
        buttons.append(
            [InlineKeyboardButton(text=text[:64], callback_data=f"{action_prefix}_{student_id}")]
        )
    buttons.append([InlineKeyboardButton(text="← Главное меню", callback_data="menu_home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ─────────────────────────────────────────────────────────────
#  PUBLICATION
# ─────────────────────────────────────────────────────────────

def get_publication_schedule_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Отправить сейчас",         callback_data="publication_send_now")],
            [InlineKeyboardButton(text="🕐 Запланировать по времени",  callback_data="publication_schedule_pick_time")],
            [InlineKeyboardButton(text="← Главное меню",              callback_data="menu_home")],
        ]
    )


def get_publication_audience_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Только ученикам",         callback_data="publication_audience_students")],
            [InlineKeyboardButton(text="👥➕ Ученикам + мне",        callback_data="publication_audience_students_plus_me")],
            [InlineKeyboardButton(text="🔬 Только мне (тест)",       callback_data="publication_audience_me_only")],
            [InlineKeyboardButton(text="← Главное меню",             callback_data="menu_home")],
        ]
    )


# ─────────────────────────────────────────────────────────────
#  SCHEDULE KEYBOARDS
# ─────────────────────────────────────────────────────────────

_WEEKDAYS_FULL  = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
_WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def get_schedule_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Моё расписание",  callback_data="schedule_view")],
        [
            InlineKeyboardButton(text="➕ Добавить урок",  callback_data="schedule_add"),
            InlineKeyboardButton(text="🗑 Удалить урок",   callback_data="schedule_delete_menu"),
        ],
        [InlineKeyboardButton(text="← Назад",             callback_data="menu_home")],
    ])


def get_schedule_slot_type_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔁 Еженедельно",   callback_data="schedule_type_recurring"),
            InlineKeyboardButton(text="📅 Разовый урок",  callback_data="schedule_type_one_time"),
        ],
        [InlineKeyboardButton(text="← Назад",             callback_data="teacher_schedule")],
    ])


def get_schedule_direction_keyboard(directions: list):
    rows = [
        [InlineKeyboardButton(
            text=f"{d['student_name']} — {d['subject_name']}"[:64],
            callback_data=f"schedule_direction_{d['id']}",
        )]
        for d in directions
    ]
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="teacher_schedule")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_schedule_day_of_week_keyboard():
    # 4+3 layout
    rows = [
        [InlineKeyboardButton(text=day, callback_data=f"schedule_dow_{i}")
         for i, day in enumerate(_WEEKDAYS_SHORT[:4])],
        [InlineKeyboardButton(text=day, callback_data=f"schedule_dow_{i}")
         for i, day in enumerate(_WEEKDAYS_SHORT[4:], start=4)],
        [InlineKeyboardButton(text="← Назад", callback_data="teacher_schedule")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_schedule_delete_keyboard(slots: list):
    rows = []
    for s in slots:
        if s.get("schedule_type") == "recurring":
            when = f"каждый {_WEEKDAYS_FULL[s['day_of_week']]} в {s['lesson_time']}"
        else:
            when = f"{s.get('specific_date', '?')} в {s['lesson_time']}"
        text = f"{s['student_name']} — {s['subject_name']} | {when}"
        rows.append([InlineKeyboardButton(text=text[:64], callback_data=f"schedule_del_{s['id']}")])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="teacher_schedule")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ─────────────────────────────────────────────────────────────
#  REPORTS
# ─────────────────────────────────────────────────────────────

def get_lessons_report_period_keyboard(back_callback: str = "superadmin_section_reports"):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сегодня",     callback_data="lreport_period_today"),
                InlineKeyboardButton(text="7 дней",      callback_data="lreport_period_week"),
            ],
            [
                InlineKeyboardButton(text="30 дней",     callback_data="lreport_period_month"),
                InlineKeyboardButton(text="Этот месяц",  callback_data="lreport_period_curmonth"),
            ],
            [InlineKeyboardButton(text="Произвольный период", callback_data="lreport_period_custom")],
            [InlineKeyboardButton(text="← Назад", callback_data=back_callback)],
        ]
    )


def get_lessons_report_teacher_filter_keyboard(
    teachers: list[tuple[int, str]],
    period_key: str,
    back_callback: str = "admin_teacher_lessons_report",
):
    """teachers: list of (teacher_id, full_name)."""
    buttons = [
        [InlineKeyboardButton(text="Все преподаватели", callback_data=f"lreport_teacher_all_{period_key}")]
    ]
    for teacher_id, full_name in teachers[:20]:
        buttons.append([
            InlineKeyboardButton(
                text=full_name[:60],
                callback_data=f"lreport_teacher_{teacher_id}_{period_key}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="← Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
