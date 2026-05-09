"""Utility helpers shared by the admin bot's handlers.

Pulled out of `handlers.py` to keep that file focused on the actual aiogram
callback/message handlers. No behavioural changes — every function below is
the original copy from `handlers.py`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import SCHOOL_BOT_TOKEN, SCHOOL_BOT_USERNAME, SUPERADMINS
from keyboards import (
    get_admin_menu,
    get_student_menu,
    get_superadmin_menu,
    get_teacher_menu,
)
from shared.database import get_user_by_telegram_id


logger = logging.getLogger(__name__)


# Resolve project paths the same way handlers.py used to. BOT_DIR points to the
# repo root (e.g. /opt/school-system/), PROJECT_ROOT also points there because
# both _utils.py and handlers.py live in school_admin_bot/.
BOT_DIR = Path(__file__).resolve().parent.parent.parent
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEACHER_UPLOADS_DIR = PROJECT_ROOT / "assets" / "teachers_uploaded"


try:
    MSK_TZ = ZoneInfo("Europe/Moscow")
except Exception:
    MSK_TZ = timezone(timedelta(hours=3))


def msk_now_naive() -> datetime:
    return datetime.now(MSK_TZ).replace(tzinfo=None)


def resolve_local_path(path_value: str) -> str:
    """Convert relative path to absolute path based on BOT_DIR."""
    path = Path(path_value)
    if path.is_absolute():
        return str(path)
    return str((BOT_DIR / path).resolve())


async def update_flow_message(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = None,
) -> None:
    try:
        await callback.message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except Exception:
        await callback.message.answer(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )


async def save_teacher_photo(message: Message) -> str:
    """Save uploaded teacher photo locally so it can be shown by the school bot."""
    file_info = await message.bot.get_file(message.photo[-1].file_id)
    file_ext = Path(file_info.file_path or "").suffix or ".jpg"
    unique_id = message.photo[-1].file_unique_id or uuid4().hex
    filename = f"teacher_{unique_id}{file_ext}"

    TEACHER_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    destination = TEACHER_UPLOADS_DIR / filename
    await message.bot.download_file(file_info.file_path, destination=destination)

    return f"assets/teachers_uploaded/{filename}".replace("\\", "/")


async def send_student_notification(
    callback: CallbackQuery,
    student_telegram_id: int | None,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if not student_telegram_id:
        return

    if SCHOOL_BOT_TOKEN:
        try:
            async with Bot(token=SCHOOL_BOT_TOKEN) as school_bot:
                await school_bot.send_message(
                    student_telegram_id,
                    text,
                    reply_markup=reply_markup,
                )
            return
        except Exception as exc:
            logger.warning(
                "Failed to send via school bot token to user %s: %s",
                student_telegram_id,
                exc,
            )

    try:
        await callback.bot.send_message(
            student_telegram_id,
            text,
            reply_markup=reply_markup,
        )
    except Exception as exc:
        logger.warning(
            "Failed to send via admin bot token to user %s: %s",
            student_telegram_id,
            exc,
        )


def build_payment_prompt_keyboard() -> InlineKeyboardMarkup | None:
    if not SCHOOL_BOT_USERNAME:
        return None

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Перейти к оплате",
                    url=f"https://t.me/{SCHOOL_BOT_USERNAME}?start=pay",
                )
            ]
        ]
    )


async def notify_student_about_attendance(
    callback: CallbackQuery,
    *,
    student_telegram_id: int | None,
    student_name: str,
    subject_name: str,
    teacher_name: str,
    tariff_type: str,
    status: str,
    lesson_balance_before: int,
    lesson_balance_after: int,
) -> None:
    if not student_telegram_id:
        return

    if status != "present":
        text = (
            "Здравствуйте!\n\n"
            "По Вашему направлению обновлена отметка посещаемости.\n\n"
            f"Ученик: {student_name}\n"
            f"Предмет: {subject_name}\n"
            f"Преподаватель: {teacher_name}\n"
            "Статус занятия: не был"
        )
        await send_student_notification(callback, student_telegram_id, text)
        return

    lines = [
        "Здравствуйте!",
        "",
        "Занятие отмечено как проведённое.",
        "",
        f"Ученик: {student_name}",
        f"Предмет: {subject_name}",
        f"Преподаватель: {teacher_name}",
        "Списано занятий: 1",
        f"Баланс был: {lesson_balance_before}",
        f"Баланс стал: {lesson_balance_after}",
    ]

    need_payment_prompt = tariff_type == "single" or lesson_balance_after < 0
    reply_markup = None

    if lesson_balance_after < 0:
        lines.extend(
            [
                "",
                "❗❗❗🔴 ВНИМАНИЕ! ОБРАЗОВАЛАСЬ ЗАДОЛЖЕННОСТЬ! 🔴❗❗❗",
                f"Размер задолженности: {abs(lesson_balance_after)} занят.",
                "❗❗❗ Пожалуйста, внесите оплату. ❗❗❗",
            ]
        )
        reply_markup = build_payment_prompt_keyboard()
    elif lesson_balance_after == 0:
        lines.extend(
            [
                "",
                "На балансе больше не осталось оплаченных занятий.",
            ]
        )

    if tariff_type == "single":
        lines.extend(
            [
                "",
                "У Вас разовый тариф. Пожалуйста, направьте чек об оплате следующего занятия.",
            ]
        )
        if reply_markup is None:
            reply_markup = build_payment_prompt_keyboard()

    if reply_markup is None and need_payment_prompt:
        reply_markup = build_payment_prompt_keyboard()

    await send_student_notification(
        callback,
        student_telegram_id,
        "\n".join(lines),
        reply_markup=reply_markup,
    )


async def notify_teacher_about_attendance(
    callback: CallbackQuery,
    *,
    teacher_telegram_id: int | None,
    student_name: str,
    subject_name: str,
    status: str,
    lesson_balance_after: int,
) -> None:
    if not teacher_telegram_id:
        return

    status_text = "был" if status == "present" else "не был"
    text = (
        "Здравствуйте!\n\n"
        "По Вашему ученику обновлена посещаемость.\n\n"
        f"Ученик: {student_name}\n"
        f"Предмет: {subject_name}\n"
        f"Статус занятия: {status_text}\n"
        f"Текущий баланс ученика: {lesson_balance_after}"
    )
    try:
        await callback.bot.send_message(teacher_telegram_id, text)
    except Exception:
        pass


def get_role_by_user_id(user_id: int):
    user = get_user_by_telegram_id(user_id)
    if not user:
        return None

    _, _telegram_id, _full_name, role, is_active = user

    if not is_active:
        return None

    return role


def is_admin_role(user_id: int) -> bool:
    role = get_role_by_user_id(user_id)
    return role in ["superadmin", "admin"]


def is_teacher_role(user_id: int) -> bool:
    role = get_role_by_user_id(user_id)
    return role == "teacher"


def get_admin_reply_menu(user_id: int):
    return get_superadmin_menu() if user_id in SUPERADMINS else get_admin_menu()


def get_home_menu_by_user_id(user_id: int):
    role = get_role_by_user_id(user_id)
    if user_id in SUPERADMINS:
        return get_superadmin_menu()
    if role == "admin":
        return get_admin_menu()
    if role == "teacher":
        return get_teacher_menu()
    if role == "student":
        return get_student_menu()
    return None


def role_title(role: str) -> str:
    return {
        "superadmin": "Суперадмин",
        "admin": "Администратор",
        "teacher": "Преподаватель",
        "student": "Ученик",
    }.get(role, role)
