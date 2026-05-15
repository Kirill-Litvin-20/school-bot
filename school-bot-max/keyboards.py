"""MAX keyboard builders.

MAX buttons format:
  {"type": "callback", "text": "...", "payload": "..."}
  {"type": "link", "text": "...", "url": "..."}

keyboard() returns a list[dict] — the `attachments` field in API calls.
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from shared.max_api import btn, btn_url, keyboard
from shared.database import get_teacher_catalog_subjects, get_teacher_catalog_name_subject_pairs

SUBJECTS = [
    "Математика",
    "Русский язык",
    "Информатика",
    "Физика",
    "Обществознание",
    "Литература",
]


def get_all_subject_names() -> list[str]:
    subjects: list[str] = []
    from shared.database import get_teacher_catalog_subjects
    for s in get_teacher_catalog_subjects():
        if s and s not in subjects:
            subjects.append(s)
    for s in SUBJECTS:
        if s not in subjects:
            subjects.append(s)
    return subjects


def main_menu_kb() -> list[dict]:
    return keyboard(
        [btn("📝 Оставить заявку", "menu_signup")],
        [btn("👨‍🏫 Преподаватели", "menu_teachers")],
        [btn("🎁 Акции и предложения", "menu_offers")],
        [btn("👤 Личный кабинет", "menu_cabinet")],
        [btn("❓ Помощь и FAQ", "menu_faq")],
        [btn_url("📢 Канал школы", "https://t.me/school_integral_ru")],
    )


def cabinet_kb() -> list[dict]:
    return keyboard(
        [btn("💳 Оплатить занятия", "menu_paid")],
        [btn_url("✉️ Написать администратору", "https://t.me/integral_school_ru")],
        [btn("← В меню", "back_to_menu")],
    )


def faq_kb() -> list[dict]:
    return keyboard(
        [btn("💳 Как оплатить", "faq_pay")],
        [btn("📦 Что такое пакет занятий", "faq_package")],
        [btn("🔄 Перенос и отмена занятий", "faq_reschedule")],
        [btn("← В меню", "back_to_menu")],
    )


def faq_back_kb() -> list[dict]:
    return keyboard(
        [btn("← К вопросам", "menu_faq")],
        [btn("← В меню", "back_to_menu")],
    )


def back_kb() -> list[dict]:
    return keyboard([btn("← Назад", "back_step")])


def back_menu_kb() -> list[dict]:
    return keyboard([btn("← В меню", "back_to_menu")])


def user_type_kb() -> list[dict]:
    return keyboard(
        [btn("Ученик", "user_student"), btn("Родитель", "user_parent")],
        [btn("← В меню", "back_to_menu")],
    )


def class_kb() -> list[dict]:
    return keyboard(
        [btn("5", "class_5"), btn("6", "class_6"), btn("7", "class_7")],
        [btn("8", "class_8"), btn("9", "class_9"), btn("10", "class_10")],
        [btn("11", "class_11")],
        [btn("← Назад", "back_step")],
    )


def goal_kb() -> list[dict]:
    return keyboard(
        [btn("ОГЭ", "goal_ОГЭ")],
        [btn("ЕГЭ", "goal_ЕГЭ")],
        [btn("Успеваемость", "goal_Успеваемость")],
        [btn("← Назад", "back_step")],
    )


def lesson_type_kb() -> list[dict]:
    return keyboard(
        [btn("Индивидуально", "lesson_individual")],
        [btn("Мини-группа", "lesson_group")],
        [btn("← Назад", "back_step")],
    )


def subjects_kb(selected: list[str]) -> list[dict]:
    rows = []
    for subject in get_all_subject_names():
        prefix = "✅ " if subject in selected else ""
        rows.append([btn(f"{prefix}{subject}", f"subject_{subject}")])
    rows.append([btn("Готово ✓", "subjects_done")])
    rows.append([btn("← Назад", "back_step")])
    return keyboard(*rows)


def teacher_choice_kb() -> list[dict]:
    return keyboard(
        [btn("Подобрать преподавателя", "teacher_pick")],
        [btn("Выбрать конкретного", "teacher_specific")],
        [btn("← Назад", "back_step")],
    )


def teachers_list_kb() -> list[dict]:
    pairs = get_teacher_catalog_name_subject_pairs()
    rows = []
    seen = []
    for name, subj in (pairs or []):
        label = f"{name} - {subj}"
        if label not in seen:
            seen.append(label)
            rows.append([btn(label, f"pick_teacher_{len(seen) - 1}")])
    if not rows:
        rows.append([btn("Преподаватели не добавлены", "noop")])
    rows.append([btn("← Назад", "back_step")])
    return keyboard(*rows)


def contact_method_kb() -> list[dict]:
    return keyboard(
        [btn("MAX (этот мессенджер)", "contact_MAX")],
        [btn("Telegram", "contact_Telegram")],
        [btn("Звонок", "contact_Звонок")],
        [btn("← Назад", "back_step")],
    )


def offers_kb() -> list[dict]:
    return keyboard(
        [btn("🎁 Бесплатная диагностика", "offer_free_diagnosis")],
        [btn("💰 Скидка на первый пакет", "offer_first_package")],
        [btn("🤝 Реферальная программа", "offer_referral_program")],
        [btn("← В меню", "back_to_menu")],
    )


def teacher_subjects_kb(subjects: list[str]) -> list[dict]:
    rows = [[btn(s, f"teacher_subject_{s}")] for s in subjects]
    rows.append([btn("← В меню", "back_to_menu")])
    return keyboard(*rows)


def teacher_card_kb(index: int, total: int) -> list[dict]:
    nav_row = []
    if index > 0:
        nav_row.append(btn("◀ Пред.", "teacher_prev"))
    nav_row.append(btn(f"{index + 1} / {total}", "noop"))
    if index < total - 1:
        nav_row.append(btn("След. ▶", "teacher_next"))
    rows = []
    if len(nav_row) > 1:
        rows.append(nav_row)
    rows.append([btn("📝 Записаться к этому преподавателю", "teacher_signup")])
    rows.append([btn("← К предметам", "teacher_back_to_subjects")])
    rows.append([btn("← В меню", "back_to_menu")])
    return keyboard(*rows)
