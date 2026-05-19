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
from config import MAX_ADMIN_USERNAME


def _admin_btn_rows() -> list[list[dict]]:
    if MAX_ADMIN_USERNAME:
        return [[btn_url("✉️ Написать администратору", f"https://max.ru/{MAX_ADMIN_USERNAME}")]]
    return []

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


def cabinet_kb(tg_linked: bool = False) -> list[dict]:
    rows = [
        [btn("💳 Оплатить занятия", "menu_paid")],
        [btn("🎟 Ввести промокод", "enter_promo")],
        [btn_url("✉️ Написать администратору", "https://t.me/integral_school_ru")],
    ]
    if tg_linked:
        rows.append([btn("✅ Telegram подключён", "noop")])
    else:
        rows.append([btn("🔗 Связать с Telegram", "link_tg")])
    rows.append([btn("← В меню", "back_to_menu")])
    return keyboard(*rows)


def faq_kb() -> list[dict]:
    rows = [
        [btn("💳 Как оплатить", "faq_pay")],
        [btn("📦 Что такое пакет занятий", "faq_package")],
        [btn("🔄 Перенос и отмена занятий", "faq_reschedule")],
        [btn("🎟 Промокоды", "faq_promo")],
        *_admin_btn_rows(),
        [btn("← В меню", "back_to_menu")],
    ]
    return keyboard(*rows)


def faq_back_kb() -> list[dict]:
    rows = [
        [btn("← К вопросам", "menu_faq")],
        *_admin_btn_rows(),
        [btn("← В меню", "back_to_menu")],
    ]
    return keyboard(*rows)


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


def _strike(s: str) -> str:
    return "".join(c + "̶" for c in s)


def _fmt_price(p: int) -> str:
    s = str(p)
    return s[:-3] + " " + s[-3:] if len(s) > 3 else s


def package_selection_kb(packages: dict, promo=None) -> list[dict]:
    """packages: {lessons: price}, promo: tuple or None"""
    promo_dtype = promo_dvalue = None
    if promo:
        _, _, promo_dtype, promo_dvalue, _ = promo
        promo_dvalue = float(promo_dvalue)

    rows = []
    for lessons, price in sorted(packages.items()):
        if promo_dtype == "fixed_rub":
            discounted = max(0, price - int(promo_dvalue))
            label = f"{lessons} зан. — {_strike(_fmt_price(price) + '₽')} → {_fmt_price(discounted)}₽"
        elif promo_dtype == "percent":
            discounted = int(price * (1 - promo_dvalue / 100))
            label = f"{lessons} зан. — {_strike(_fmt_price(price) + '₽')} → {_fmt_price(discounted)}₽"
        else:
            label = f"{lessons} зан. — {_fmt_price(price)}₽"
        rows.append([btn(label, f"pay_package_{lessons}")])

    rows.append([btn("← Назад", "pay_back_to_type")])
    return keyboard(*rows)


def teacher_subjects_kb(subjects: list[str]) -> list[dict]:
    rows = [[btn(s, f"teacher_subject_{s}")] for s in subjects]
    rows.append([btn("← В меню", "back_to_menu")])
    return keyboard(*rows)


def review_card_kb(index: int, total: int) -> list[dict]:
    nav_row = []
    if index > 0:
        nav_row.append(btn("◀ Пред.", "review_prev"))
    nav_row.append(btn(f"{index + 1} / {total}", "noop"))
    if index < total - 1:
        nav_row.append(btn("След. ▶", "review_next"))
    rows = []
    if len(nav_row) > 1:
        rows.append(nav_row)
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
