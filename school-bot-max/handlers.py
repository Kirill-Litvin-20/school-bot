"""MAX bot handlers.

The dispatcher calls these functions with parsed update data. All state is
kept in the shared SQLite `max_fsm_state` table so the MAX bot has no
dependency on aiogram's FSM infrastructure.
"""

from __future__ import annotations

import logging
import re
import sys
from io import BytesIO
from pathlib import Path

from aiogram import Bot as TelegramBot
from aiogram.types import BufferedInputFile

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import (
    APPLICATIONS_CHAT_ID,
    LESSON_PRICE,
    PACKAGE_PRICES,
    PAYMENT_ACCOUNT_HOLDER,
    PAYMENT_BANK_NAME,
    PAYMENT_BANK_NUMBER,
    PAYMENTS_CHAT_ID,
    TG_BOT_TOKEN,
    TG_BOT_USERNAME,
)
from keyboards import (
    back_kb,
    back_menu_kb,
    cabinet_kb,
    class_kb,
    contact_method_kb,
    faq_back_kb,
    faq_kb,
    get_all_subject_names,
    goal_kb,
    lesson_type_kb,
    main_menu_kb,
    offers_kb,
    package_selection_kb,
    subjects_kb,
    teacher_card_kb,
    teacher_choice_kb,
    teacher_subjects_kb,
    teachers_list_kb,
    user_type_kb,
)
from shared.database import (
    apply_promo_code_for_student,
    clear_max_fsm_state,
    consume_account_link_code,
    create_payment_request_max,
    find_students_by_max_id,
    find_students_by_telegram_id,
    get_active_promo_for_max_user,
    get_active_promo_for_student_id,
    get_max_fsm_state,
    get_recent_payment_history_by_max_user,
    get_student_directions,
    get_teacher_catalog_name_subject_pairs,
    link_max_to_student,
    set_max_fsm_state,
    try_auto_link_max_by_phone,
)
from shared.max_api import MaxApiClient, btn, keyboard
from states import (
    APP_CLASS,
    APP_COMMENT,
    APP_CONTACT_METHOD,
    APP_CONTACT_VALUE,
    APP_GOAL,
    APP_LESSON_TYPE,
    APP_NAME,
    APP_SUBJECTS,
    APP_TEACHER_CHOICE,
    APP_TEACHER_NAME,
    APP_USER_TYPE,
    ENTER_PROMO,
    LINK_CODE,
    LINK_PHONE,
    MENU,
    PACKAGE_SELECTION,
    PAYMENT_PROOF,
    PAYMENT_TYPE_CHOICE,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _is_valid_phone(text: str) -> bool:
    cleaned = re.sub(r"[^\d+]", "", text.strip())
    if cleaned.startswith("+"):
        return cleaned[1:].isdigit() and 10 <= len(cleaned[1:]) <= 15
    return cleaned.isdigit() and 10 <= len(cleaned) <= 15


def _is_valid_tg_username(text: str) -> bool:
    return bool(re.fullmatch(r"@[A-Za-z0-9_]{5,32}", text.strip()))


def _format_payment_status(status: str) -> str:
    return {
        "pending": "⏳ Ожидает проверки",
        "processing": "🔄 На проверке",
        "approved": "✅ Подтверждена",
        "rejected": "❌ Отклонена",
        "expired": "⌛ Просрочена",
    }.get(status, status)


def _build_cabinet_text(student_name: str, directions: list, payments: list, student_id: int | None = None) -> str:
    positive = sum(d[3] for d in directions if d[3] > 0)
    debt = sum(-d[3] for d in directions if d[3] < 0)

    lines = [
        "╔══════════════════════════╗",
        "  👤 ЛИЧНЫЙ КАБИНЕТ",
        "╚══════════════════════════╝",
        "",
        f"👋 {student_name}",
        "",
        "📊 Сводка",
        f"   • Занятий на балансе: {positive}",
    ]
    if debt > 0:
        lines.append(f"   • Задолженность: {debt} занятий ⚠️")
    else:
        lines.append("   • Задолженность: нет ✅")
    if student_id is not None:
        promo = get_active_promo_for_student_id(student_id)
        if promo:
            _, code, dtype, dvalue, _ = promo
            unit = "%" if dtype == "percent" else "₽"
            lines.append(f"   • 🎟 Активный промокод: {code} (скидка {int(dvalue)}{unit})")

    if directions:
        lines.extend(["", "📚 Ваши направления"])
        for i, d in enumerate(directions, 1):
            _, teacher, subject, balance, tariff = d
            bal_view = f"долг {-balance} ⚠️" if balance < 0 else f"остаток {balance}"
            lines.append(f"   {i}. {subject} — {teacher}: {bal_view}")
    else:
        lines.extend(["", "📚 Ваши направления", "   Активные направления пока не назначены."])

    lines.extend(["", "💳 Последние оплаты"])
    if not payments:
        lines.append("   История оплат пока отсутствует.")
    else:
        for p in payments:
            pid, status, caption, created_at, _, lessons = p
            lines.append(
                f"   • Оплата #{pid} — {_format_payment_status(status)}\n"
                f"     Дата: {created_at[:10]}  Начислено: {lessons}"
            )

    lines.extend(["", "Написать администратору: https://t.me/integral_school_ru"])
    return "\n".join(lines)


def _build_application_text(data: dict) -> str:
    teacher_text = data.get("teacher_choice", "—")
    if data.get("teacher_choice") == "Выбрать конкретного":
        teacher_text = data.get("teacher_name", "—")
    subjects_text = ", ".join(data.get("subjects", [])) or "—"
    return (
        "📌 Новая заявка (из MAX)\n\n"
        f"👤 Кто оставил: {data.get('user_type', '—')}\n"
        f"📝 Имя: {data.get('name', '—')}\n"
        f"🏫 Класс: {data.get('school_class', '—')}\n"
        f"🎯 Цель: {data.get('goal', '—')}\n"
        f"📚 Формат: {data.get('lesson_type', '—')}\n"
        f"📖 Предметы: {subjects_text}\n"
        f"👨‍🏫 Преподаватель: {teacher_text}\n"
        f"📞 Способ связи: {data.get('contact_method', '—')}\n"
        f"🔗 Контакт: {data.get('contact_value', '—')}\n"
        f"💬 Комментарий: {data.get('comment', '—')}"
    )


async def _reply(
    api: MaxApiClient, user_id: int, message_id: str | None, text: str, kb=None
) -> dict:
    """Edit message_id if available, otherwise send a new message."""
    if message_id:
        try:
            return await api.edit_message(message_id, text, kb)
        except Exception as exc:
            logger.debug("edit_message mid=%s failed: %s", message_id, exc)
    return await api.send_message(user_id, text, kb)


async def _show_menu(
    api: MaxApiClient, user_id: int, data: dict | None = None, message_id: str | None = None
) -> None:
    set_max_fsm_state(user_id, MENU, data or {})
    await _reply(api, user_id, message_id, "📋 Выберите нужный раздел:", main_menu_kb())


_SERVER_BASE_URL = "http://151.243.176.132"


async def _send_teacher_card(
    api: MaxApiClient,
    user_id: int,
    message_id: str | None,
    subject: str,
    cards: list[dict],
    index: int,
) -> None:
    card = cards[index]
    name = (card.get("name") or "").strip()
    description = card.get("description") or "Описание будет добавлено позже."
    text = f"👨‍🏫 {name}\n📚 Предмет: {subject}\n\n{description}"
    kb = teacher_card_kb(index, len(cards))
    photo_path = (card.get("photo") or "").strip()
    if photo_path:
        photo_url = f"{_SERVER_BASE_URL}/{photo_path}"
        photo_att = {"type": "image", "payload": {"url": photo_url}}
        if message_id:
            try:
                await api.edit_message(message_id, text, [photo_att] + kb)
                return
            except Exception:
                await api.delete_message(message_id)
        try:
            await api.send_photo_url(user_id, photo_url, caption=text, attachments=kb)
            return
        except Exception as exc:
            logger.warning("Teacher photo send failed %s: %s", photo_url, exc)
    await _reply(api, user_id, message_id, text, kb)


# ──────────────────────────────────────────────────────────────────────────────
# Entry points called by bot.py dispatcher
# ──────────────────────────────────────────────────────────────────────────────

async def handle_bot_started(api: MaxApiClient, user_id: int, name: str, username: str | None) -> None:
    """Fired when the user taps 'Start' for the first time."""
    clear_max_fsm_state(user_id)

    # Check if already linked
    students = find_students_by_max_id(user_id)
    if students:
        student_name = students[0][1]
        set_max_fsm_state(user_id, MENU)
        await api.send_message(
            user_id,
            f"👋 Добро пожаловать, {student_name}!\n\nВаш личный кабинет уже привязан.",
            main_menu_kb(),
        )
        return

    set_max_fsm_state(user_id, LINK_PHONE)
    await api.send_message(
        user_id,
        "👋 Добро пожаловать в бот учебного центра!\n\n"
        "Для привязки вашего личного кабинета введите номер телефона, "
        "который вы указывали при записи (например: +79001234567).\n\n"
        "Если у вас нет кабинета в нашей системе — введите /skip и оставьте заявку через меню.",
    )


async def handle_text(
    api: MaxApiClient,
    user_id: int,
    username: str | None,
    name: str,
    text: str,
) -> None:
    state, data = get_max_fsm_state(user_id)

    if not state:
        state = MENU
        data = {}

    text = text.strip()

    # ── /start / /menu commands ──────────────────────────────────────────
    if text in ("/start", "/menu"):
        await handle_bot_started(api, user_id, name, username)
        return

    if text == "/skip" and state == LINK_PHONE:
        await _show_menu(api, user_id)
        return

    # ── /link CODE ────────────────────────────────────────────────────────
    if text.lower().startswith("/link"):
        parts = text.split(maxsplit=1)
        code = parts[1].strip() if len(parts) > 1 else ""
        await _handle_link_code(api, user_id, username, code)
        return

    # ── state machine ─────────────────────────────────────────────────────
    if state == LINK_PHONE:
        await _handle_link_phone_text(api, user_id, username, text)

    elif state == LINK_CODE:
        await _handle_link_code(api, user_id, username, text)

    elif state == APP_USER_TYPE:
        low = text.lower()
        if "родител" in low:
            data["user_type"] = "Родитель"
        elif "учен" in low:
            data["user_type"] = "Ученик"
        else:
            await api.send_message(user_id, "❓ Напишите: ученик или родитель.")
            return
        set_max_fsm_state(user_id, APP_NAME, data)
        await api.send_message(user_id, "📝 Напишите, как к вам обращаться (имя или имя + фамилия):")

    elif state == APP_NAME:
        data["name"] = text
        set_max_fsm_state(user_id, APP_CLASS, data)
        await api.send_message(user_id, "🏫 Выберите класс:", class_kb())

    elif state == APP_COMMENT:
        data["comment"] = text if text.lower() not in ("-", "нет") else "-"
        await _submit_application(api, user_id, data)

    elif state == APP_CONTACT_VALUE:
        await _handle_contact_value(api, user_id, data, text)

    elif state == APP_TEACHER_NAME:
        data["teacher_name"] = text
        set_max_fsm_state(user_id, APP_CONTACT_METHOD, data)
        await api.send_message(user_id, "📞 Как с вами связаться?", contact_method_kb())

    elif state == ENTER_PROMO:
        code = text.strip().upper()
        students = find_students_by_max_id(user_id)
        if not students or not code:
            set_max_fsm_state(user_id, MENU)
            await api.send_message(user_id, "⚠️ Не удалось применить промокод. Попробуйте через меню.", main_menu_kb())
            return
        student_id = students[0][0]
        ok, result = apply_promo_code_for_student(student_id, code)
        if ok:
            dtype, dvalue = result.split(":", 1)
            unit = "%" if dtype == "percent" else "₽"
            msg = (
                f"✅ Промокод {code} активирован!\n"
                f"Скидка {int(float(dvalue))}{unit} будет применена при следующей оплате."
            )
        elif result == "not_found":
            msg = f"❌ Промокод {code} не найден. Проверьте правильность написания."
        elif result == "inactive":
            msg = f"❌ Промокод {code} деактивирован."
        elif result == "expired":
            msg = f"❌ Срок действия промокода {code} истёк."
        elif result == "limit_reached":
            msg = f"❌ Промокод {code} исчерпал лимит использований."
        elif result == "already_assigned":
            msg = f"✅ Промокод {code} уже применён к вашему аккаунту."
        else:
            msg = "⚠️ Произошла ошибка. Попробуйте позже или обратитесь к администратору."
        set_max_fsm_state(user_id, MENU)
        from shared.max_api import btn as _btn, keyboard as _keyboard
        kb = _keyboard([_btn("💳 Оплатить занятия", "menu_paid")], [_btn("← В меню", "back_to_menu")])
        await api.send_message(user_id, msg, kb)

    elif state == PAYMENT_PROOF:
        await api.send_message(
            user_id,
            "📸 Пожалуйста, отправьте фото или PDF-файл чека об оплате.",
        )

    else:
        await _show_menu(api, user_id, data)


async def handle_photo(
    api: MaxApiClient,
    user_id: int,
    username: str | None,
    name: str,
    photo_url: str,
    caption: str | None,
) -> None:
    state, data = get_max_fsm_state(user_id)
    if state != PAYMENT_PROOF:
        await api.send_message(
            user_id,
            "ℹ️ Чек принимается только в разделе «💳 Оплатить занятия».",
            main_menu_kb(),
        )
        return
    await _process_payment_file(api, user_id, username, name, photo_url, "photo", caption)


async def handle_file(
    api: MaxApiClient,
    user_id: int,
    username: str | None,
    name: str,
    file_url: str,
    filename: str,
    caption: str | None,
) -> None:
    state, data = get_max_fsm_state(user_id)
    if state != PAYMENT_PROOF:
        await api.send_message(
            user_id,
            "ℹ️ Чек принимается только в разделе «💳 Оплатить занятия».",
            main_menu_kb(),
        )
        return
    fname = (filename or "").lower()
    if not fname.endswith(".pdf"):
        await api.send_message(user_id, "❓ Пожалуйста, отправьте PDF-файл чека.")
        return
    await _process_payment_file(api, user_id, username, name, file_url, "pdf", caption)


async def handle_callback(
    api: MaxApiClient,
    callback_id: str,
    user_id: int,
    username: str | None,
    name: str,
    payload: str,
    message_id: str | None = None,
) -> None:
    state, data = get_max_fsm_state(user_id)
    if not state:
        state = MENU
        data = {}

    try:
        await _dispatch_callback(api, callback_id, user_id, username, name, payload, state, data, message_id)
    except Exception:
        logger.exception("callback dispatch error user=%s payload=%s", user_id, payload)
        await api.answer_callback(callback_id)


# ──────────────────────────────────────────────────────────────────────────────
# Internal handlers
# ──────────────────────────────────────────────────────────────────────────────

async def _handle_link_phone_text(
    api: MaxApiClient, user_id: int, username: str | None, text: str
) -> None:
    if not _is_valid_phone(text):
        await api.send_message(
            user_id,
            "❌ Формат номера не распознан. Попробуйте ещё раз (например: +79001234567).\n"
            "Или введите /skip, чтобы пропустить и перейти в меню.",
        )
        return

    student = try_auto_link_max_by_phone(text, user_id, username)
    if student:
        student_name = student[1]
        set_max_fsm_state(user_id, MENU)
        await api.send_message(
            user_id,
            f"✅ Отлично! Ваш кабинет привязан.\n\nДобро пожаловать, {student_name}!",
            main_menu_kb(),
        )
        return

    set_max_fsm_state(user_id, LINK_CODE)
    await api.send_message(
        user_id,
        "🔍 Телефон не найден в базе.\n\n"
        "Если у вас уже есть кабинет в Telegram-боте школы — "
        "откройте там раздел 👤 Личный кабинет и нажмите «🔗 Подключить MAX». "
        "Вам придёт 6-значный код — введите его сюда.\n\n"
        "Или введите /skip чтобы перейти в меню и оставить заявку.",
    )


async def _handle_link_code(
    api: MaxApiClient, user_id: int, username: str | None, code: str
) -> None:
    if code.lower() == "/skip" or not code:
        await _show_menu(api, user_id)
        return

    telegram_id = consume_account_link_code(code)
    if not telegram_id:
        await api.send_message(
            user_id,
            "❌ Код недействителен или устарел. Запросите новый код в Telegram-боте.\n"
            "Или введите /skip чтобы перейти в меню.",
        )
        return

    # Link all students of this TG user to this MAX account
    students = find_students_by_telegram_id(telegram_id)
    if students:
        for student in students:
            link_max_to_student(student[0], user_id, username)
        student_name = students[0][1]
        set_max_fsm_state(user_id, MENU)
        await api.send_message(
            user_id,
            f"✅ Аккаунты связаны! Добро пожаловать, {student_name}!",
            main_menu_kb(),
        )
    else:
        set_max_fsm_state(user_id, MENU)
        await api.send_message(
            user_id,
            "✅ Код принят. Ваш Telegram-аккаунт связан с MAX.\n"
            "(Карточка ученика будет привязана администратором.)",
            main_menu_kb(),
        )


async def _process_payment_file(
    api: MaxApiClient,
    user_id: int,
    username: str | None,
    name: str,
    file_url: str,
    file_type: str,
    caption: str | None,
) -> None:
    try:
        file_bytes = await api.download_bytes(file_url)
    except Exception as exc:
        logger.error("Failed to download file from MAX: %s", exc)
        await api.send_message(user_id, "⚠️ Не удалось скачать файл. Попробуйте ещё раз.")
        return

    try:
        tg_bot = TelegramBot(token=TG_BOT_TOKEN)
        ext = "pdf" if file_type == "pdf" else "jpg"
        input_file = BufferedInputFile(file_bytes, filename=f"receipt.{ext}")

        payment_request_id = create_payment_request_max(
            max_user_id=user_id,
            max_username=username,
            max_full_name=name,
            caption_text=caption,
            file_id="pending",
            file_type=file_type,
        )

        state, fsm_data = get_max_fsm_state(user_id)
        payment_type_label = (fsm_data or {}).get("payment_type_label", "")
        promo = get_active_promo_for_max_user(user_id)
        promo_line = ""
        if promo:
            _, code, dtype, dvalue, _ = promo
            unit = "%" if dtype == "percent" else "₽"
            promo_line = f"\n🎟 Промокод: {code} (-{int(dvalue)}{unit})"
        username_line = f"🔗 Username: @{username}" if username else "🔗 Username: не указан"
        payment_caption = (
            f"💳 Оплата #{payment_request_id}\n\n"
            f"📌 Статус: ⏳ Ожидает проверки\n"
            f"📱 Платформа: MAX\n"
            f"👤 Имя: {name}\n"
            + username_line
            + (f"\n💰 Тип оплаты: {payment_type_label}" if payment_type_label else "")
            + promo_line
        )

        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        tg_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Подтвердить",
                        callback_data=f"payment_approve_{payment_request_id}",
                    ),
                    InlineKeyboardButton(
                        text="❌ Отклонить",
                        callback_data=f"payment_reject_{payment_request_id}",
                    ),
                ]
            ]
        )

        if file_type == "pdf":
            sent = await tg_bot.send_document(
                PAYMENTS_CHAT_ID,
                document=input_file,
                caption=payment_caption,
                reply_markup=tg_keyboard,
            )
        else:
            sent = await tg_bot.send_photo(
                PAYMENTS_CHAT_ID,
                photo=input_file,
                caption=payment_caption,
                reply_markup=tg_keyboard,
            )

        # Update the payment record with the real TG file_id so admin bot can re-use it
        real_file_id = (
            sent.photo[-1].file_id if sent.photo else
            (sent.document.file_id if sent.document else "uploaded")
        )
        from shared.database import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE payment_requests SET file_id = ? WHERE id = ?",
            (real_file_id, payment_request_id),
        )
        conn.commit()
        conn.close()

        await tg_bot.session.close()

    except Exception as exc:
        logger.exception("Failed to forward payment to TG: %s", exc)
        await api.send_message(
            user_id,
            "⚠️ Не удалось отправить чек. Попробуйте ещё раз или свяжитесь с администратором.",
        )
        return

    await api.send_message(
        user_id,
        "✅ Чек отправлен на проверку. После подтверждения занятия будут начислены на ваш баланс.",
        main_menu_kb(),
    )
    set_max_fsm_state(user_id, MENU)


async def _dispatch_callback(
    api: MaxApiClient,
    callback_id: str,
    user_id: int,
    username: str | None,
    name: str,
    payload: str,
    state: str,
    data: dict,
    message_id: str | None = None,
) -> None:
    await api.answer_callback(callback_id)

    if payload == "back_to_menu":
        await _show_menu(api, user_id, {k: v for k, v in data.items() if k == "user_type"}, message_id=message_id)
        return

    if payload == "back_step":
        await _handle_back(api, user_id, state, data, message_id)
        return

    if payload == "menu_signup":
        data_new = {k: v for k, v in data.items() if k == "user_type"}
        set_max_fsm_state(user_id, APP_USER_TYPE, data_new)
        await _reply(api, user_id, message_id, "Пожалуйста, укажите: вы ученик или родитель?", user_type_kb())
        return

    if payload in ("user_student", "user_parent"):
        data["user_type"] = "Ученик" if payload == "user_student" else "Родитель"
        set_max_fsm_state(user_id, APP_NAME, data)
        await _reply(api, user_id, message_id, "📝 Напишите, как к вам обращаться:")
        return

    if payload == "menu_cabinet":
        students = find_students_by_max_id(user_id)
        if not students:
            await _reply(
                api, user_id, message_id,
                "❌ Мы пока не нашли вас в базе учеников.\n"
                "Введите номер телефона для привязки или обратитесь к администратору.",
            )
            return
        student_id, student_name, telegram_id, _ = students[0]
        directions = get_student_directions(student_id)
        payments = get_recent_payment_history_by_max_user(user_id, limit=4)
        text = _build_cabinet_text(student_name, directions, payments, student_id=student_id)
        await _reply(api, user_id, message_id, text, cabinet_kb(tg_linked=bool(telegram_id)))
        return

    if payload == "enter_promo":
        students = find_students_by_max_id(user_id)
        if not students:
            await _reply(api, user_id, message_id, "❌ Вы не зарегистрированы в системе. Обратитесь к администратору.")
            return
        promo = get_active_promo_for_max_user(user_id)
        if promo:
            _, code, dtype, dvalue, _ = promo
            unit = "%" if dtype == "percent" else "₽"
            await _reply(
                api, user_id, message_id,
                f"✅ У вас уже активен промокод {code} (скидка {int(dvalue)}{unit}).\n\n"
                "Для замены обратитесь к администратору.",
                back_menu_kb(),
            )
            return
        set_max_fsm_state(user_id, ENTER_PROMO, data)
        await _reply(api, user_id, message_id, "🎟 Введите промокод:\n\nНапишите код в следующем сообщении.")
        return

    if payload == "link_tg":
        students = find_students_by_max_id(user_id)
        if students and students[0][2]:  # telegram_id already set
            await _reply(
                api, user_id, message_id,
                "✅ Ваш Telegram-аккаунт уже подключён.\n"
                "Если нужно изменить — обратитесь к администратору.",
                back_menu_kb(),
            )
            return
        tg_bot_username = TG_BOT_USERNAME
        set_max_fsm_state(user_id, LINK_CODE, data)
        await _reply(
            api, user_id, message_id,
            "🔗 Связка с Telegram\n\n"
            "1. Откройте Telegram-бот школы\n"
            f"2. Перейдите в 👤 Личный кабинет\n"
            "3. Нажмите «🔗 Подключить MAX»\n"
            "4. Скопируйте 6-значный код и введите его здесь\n\n"
            f"👉 Бот в Telegram: @{tg_bot_username}",
            back_menu_kb(),
        )
        return

    if payload == "menu_paid":
        students = find_students_by_max_id(user_id)
        if not students:
            await _reply(api, user_id, message_id, "❌ Вы не зарегистрированы в системе. Обратитесь к администратору.")
            return
        student_id = students[0][0]
        directions = get_student_directions(student_id)
        debt_directions = [(d[1], d[2], d[3]) for d in directions if d[3] < 0]
        if debt_directions:
            debt_lines = "\n".join(
                f"• {subj} — {teacher}: долг {abs(bal)} занят{'ие' if abs(bal) == 1 else 'ия' if 2 <= abs(bal) <= 4 else 'ий'}"
                for teacher, subj, bal in debt_directions
            )
            text = (
                "⚠️ У вас есть задолженность по занятиям:\n\n"
                f"{debt_lines}\n\n"
                "Для погашения долга переведите нужную сумму и отправьте чек."
            )
            kb = keyboard([btn("💸 Погасить долг", "pay_debt")], [btn("← В меню", "back_to_menu")])
        else:
            promo = get_active_promo_for_max_user(user_id)
            promo_hint = ""
            if promo:
                _, code, dtype, dvalue, _ = promo
                unit = "%" if dtype == "percent" else "₽"
                scope = "на оплату 1 занятия" if dtype == "percent" else "на занятия и пакеты"
                promo_hint = f"\n🎟 Промокод {code} (-{int(float(dvalue))}{unit}) ({scope})"
            text = f"Выберите вариант оплаты:{promo_hint}"
            kb = keyboard(
                [btn("✨ Оплатить одно занятие", "pay_single")],
                [btn("📦 Выбрать пакет", "pay_package")],
                [btn("← В меню", "back_to_menu")],
            )
        set_max_fsm_state(user_id, PAYMENT_TYPE_CHOICE, data)
        await _reply(api, user_id, message_id, text, kb)
        return

    if payload in ("pay_debt", "pay_single"):
        students = find_students_by_max_id(user_id)
        if not students:
            await _reply(api, user_id, message_id, "❌ Вы не зарегистрированы в системе.")
            return
        student_id = students[0][0]
        type_labels = {
            "pay_debt": "💸 Погашение долга",
            "pay_single": "✨ Разовое занятие",
        }
        payment_type_label = type_labels[payload]
        promo = get_active_promo_for_max_user(user_id)

        price_block = ""
        if LESSON_PRICE:
            if payload == "pay_single":
                dtype_p = dvalue_p = None
                if promo:
                    _, _, dtype_p, dvalue_p, _ = promo
                    dvalue_p = float(dvalue_p)
                base = LESSON_PRICE
                if dtype_p == "fixed_rub":
                    after = max(0, base - int(dvalue_p))
                    price_block = f"\n💵 Стоимость: {base}₽\n🎟 Скидка: -{int(dvalue_p)}₽\n✅ К оплате: {after}₽\n"
                elif dtype_p == "percent":
                    after = int(base * (1 - dvalue_p / 100))
                    price_block = f"\n💵 Стоимость: {base}₽\n🎟 Скидка: -{int(dvalue_p)}%\n✅ К оплате: {after}₽\n"
                else:
                    price_block = f"\n💵 Стоимость: {base}₽\n"
            elif payload == "pay_debt":
                directions = get_student_directions(student_id)
                debt_lessons = sum(abs(d[3]) for d in directions if d[3] < 0)
                if debt_lessons > 0:
                    base = LESSON_PRICE * debt_lessons
                    price_block = f"\n💵 Сумма долга: {debt_lessons} × {LESSON_PRICE}₽ = {base}₽\n"

        promo_block = ""
        if promo and payload != "pay_debt":
            _, code, dtype, dvalue, _ = promo
            unit = "%" if dtype == "percent" else "₽"
            promo_block = f"\n🎟 Применён промокод {code} ({int(float(dvalue))}{unit})\n"

        data["payment_type_label"] = payment_type_label
        set_max_fsm_state(user_id, PAYMENT_PROOF, data)
        payment_kb = None if (promo or payload == "pay_debt") else keyboard([btn("🎟 Ввести промокод", "enter_promo")])
        await _reply(
            api, user_id, message_id,
            f"💰 Тип оплаты: {payment_type_label}"
            f"{price_block}\n"
            f"💳 РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ\n\n"
            f"🏦 Номер счёта: {PAYMENT_BANK_NUMBER}\n"
            f"🏢 Банк: {PAYMENT_BANK_NAME}\n"
            f"👤 Владелец: {PAYMENT_ACCOUNT_HOLDER}\n\n"
            "📝 В комментарии к переводу укажите имя ученика.\n"
            f"{promo_block}\n"
            "📸 После оплаты отправьте фото или PDF-файл чека в этот чат.",
            payment_kb,
        )
        return

    if payload == "pay_package":
        students = find_students_by_max_id(user_id)
        if not students:
            await _reply(api, user_id, message_id, "❌ Вы не зарегистрированы в системе.")
            return
        if not PACKAGE_PRICES:
            # No packages configured — treat as before
            data["payment_type_label"] = "📦 Пакет занятий"
            set_max_fsm_state(user_id, PAYMENT_PROOF, data)
            await _reply(
                api, user_id, message_id,
                "💰 Тип оплаты: 📦 Пакет занятий\n\n"
                f"💳 РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ\n\n"
                f"🏦 Номер счёта: {PAYMENT_BANK_NUMBER}\n"
                f"🏢 Банк: {PAYMENT_BANK_NAME}\n"
                f"👤 Владелец: {PAYMENT_ACCOUNT_HOLDER}\n\n"
                "📸 После оплаты отправьте фото или PDF-файл чека в этот чат.",
            )
            return
        promo = get_active_promo_for_max_user(user_id)
        promo_note = ""
        if promo:
            _, code, dtype, dvalue, _ = promo
            unit = "%" if dtype == "percent" else "₽"
            promo_note = f"\n🎟 Промокод {code} (-{int(float(dvalue))}{unit}) применяется к пакетам."
        set_max_fsm_state(user_id, PACKAGE_SELECTION, data)
        await _reply(
            api, user_id, message_id,
            f"📦 Выбор пакета{promo_note}\n\nВыберите количество занятий:",
            package_selection_kb(PACKAGE_PRICES, promo),
        )
        return

    if payload.startswith("pay_package_"):
        try:
            lessons = int(payload.split("pay_package_", 1)[1])
        except (ValueError, IndexError):
            await _reply(api, user_id, message_id, "Ошибка выбора пакета.")
            return
        price = PACKAGE_PRICES.get(lessons)
        if not price:
            await _reply(api, user_id, message_id, "Пакет не найден.")
            return
        students = find_students_by_max_id(user_id)
        if not students:
            await _reply(api, user_id, message_id, "❌ Вы не зарегистрированы в системе.")
            return
        promo = get_active_promo_for_max_user(user_id)
        payment_type_label = f"📦 Пакет {lessons} занятий"

        # Price calculation
        price_block = f"\n💵 Стоимость пакета: {price}₽\n"
        promo_block = ""
        if promo:
            _, code, dtype, dvalue, _ = promo
            dvalue_f = float(dvalue)
            unit = "%" if dtype == "percent" else "₽"
            if dtype == "fixed_rub":
                after = max(0, price - int(dvalue_f))
                price_block = f"\n💵 Стоимость пакета: {price}₽\n🎟 Скидка: -{int(dvalue_f)}₽\n✅ К оплате: {after}₽\n"
                promo_block = f"\n🎟 Применён промокод {code} ({int(dvalue_f)}{unit})\n"
            elif dtype == "percent":
                after = int(price * (1 - dvalue_f / 100))
                price_block = f"\n💵 Стоимость пакета: {price}₽\n🎟 Скидка: -{int(dvalue_f)}%\n✅ К оплате: {after}₽\n"
                promo_block = f"\n🎟 Применён промокод {code} ({int(dvalue_f)}{unit})\n"

        data["payment_type_label"] = payment_type_label
        set_max_fsm_state(user_id, PAYMENT_PROOF, data)
        await _reply(
            api, user_id, message_id,
            f"💰 Тип оплаты: {payment_type_label}"
            f"{price_block}\n"
            f"💳 РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ\n\n"
            f"🏦 Номер счёта: {PAYMENT_BANK_NUMBER}\n"
            f"🏢 Банк: {PAYMENT_BANK_NAME}\n"
            f"👤 Владелец: {PAYMENT_ACCOUNT_HOLDER}\n\n"
            "📝 В комментарии к переводу укажите имя ученика.\n"
            f"{promo_block}\n"
            "📸 После оплаты отправьте фото или PDF-файл чека в этот чат.",
        )
        return

    if payload == "pay_back_to_type":
        students = find_students_by_max_id(user_id)
        if not students:
            await _show_menu(api, user_id, data, message_id=message_id)
            return
        promo = get_active_promo_for_max_user(user_id)
        promo_hint = ""
        if promo:
            _, code, dtype, dvalue, _ = promo
            unit = "%" if dtype == "percent" else "₽"
            scope = "на оплату 1 занятия" if dtype == "percent" else "на занятия и пакеты"
            promo_hint = f"\n🎟 Промокод {code} (-{int(float(dvalue))}{unit}) ({scope})"
        set_max_fsm_state(user_id, PAYMENT_TYPE_CHOICE, data)
        await _reply(
            api, user_id, message_id,
            f"Выберите вариант оплаты:{promo_hint}",
            keyboard(
                [btn("✨ Оплатить одно занятие", "pay_single")],
                [btn("📦 Выбрать пакет", "pay_package")],
                [btn("← В меню", "back_to_menu")],
            ),
        )
        return

    if payload == "menu_teachers":
        from shared.database import get_teacher_catalog_subjects
        subjects = get_teacher_catalog_subjects()
        if not subjects:
            await _reply(api, user_id, message_id, "Список преподавателей пока пуст.")
            return
        await _reply(api, user_id, message_id, "👨‍🏫 Выберите предмет:", teacher_subjects_kb(subjects))
        return

    if payload.startswith("teacher_subject_"):
        subject = payload.split("teacher_subject_", 1)[1]
        from shared.database import get_teacher_cards_by_subject
        cards = [c for c in get_teacher_cards_by_subject(subject) if (c.get("name") or "").strip()]
        if not cards:
            await _reply(api, user_id, message_id, f"По предмету «{subject}» преподаватели не добавлены.", back_menu_kb())
            return
        data["teacher_subject"] = subject
        data["teacher_index"] = 0
        set_max_fsm_state(user_id, MENU, data)
        await _send_teacher_card(api, user_id, message_id, subject, cards, 0)
        return

    if payload in ("teacher_prev", "teacher_next"):
        subject = data.get("teacher_subject", "")
        index = int(data.get("teacher_index", 0))
        from shared.database import get_teacher_cards_by_subject
        cards = [c for c in get_teacher_cards_by_subject(subject) if (c.get("name") or "").strip()]
        if not cards:
            await _show_menu(api, user_id, data, message_id=message_id)
            return
        index = max(0, index - 1) if payload == "teacher_prev" else min(len(cards) - 1, index + 1)
        data["teacher_index"] = index
        set_max_fsm_state(user_id, MENU, data)
        await _send_teacher_card(api, user_id, message_id, subject, cards, index)
        return

    if payload == "teacher_back_to_subjects":
        from shared.database import get_teacher_catalog_subjects
        subjects = get_teacher_catalog_subjects()
        await _reply(api, user_id, message_id, "👨‍🏫 Выберите предмет:", teacher_subjects_kb(subjects))
        return

    if payload == "teacher_signup":
        subject = data.get("teacher_subject", "")
        index = int(data.get("teacher_index", 0))
        from shared.database import get_teacher_cards_by_subject
        cards = [c for c in get_teacher_cards_by_subject(subject) if (c.get("name") or "").strip()]
        teacher_name = (cards[index].get("name") or "") if 0 <= index < len(cards) else ""
        data_new = {k: v for k, v in data.items() if k not in ("teacher_subject", "teacher_index")}
        data_new["teacher_choice"] = "Выбрать конкретного"
        data_new["teacher_name"] = f"{teacher_name} - {subject}"
        if not data_new.get("user_type"):
            set_max_fsm_state(user_id, APP_USER_TYPE, data_new)
            await _reply(api, user_id, message_id, "Пожалуйста, укажите: вы ученик или родитель?", user_type_kb())
        else:
            set_max_fsm_state(user_id, APP_CONTACT_METHOD, data_new)
            await _reply(api, user_id, message_id, "📞 Как с вами связаться?", contact_method_kb())
        return

    if payload == "menu_offers":
        await _reply(api, user_id, message_id, "🎁 СПЕЦИАЛЬНЫЕ ПРЕДЛОЖЕНИЯ\n\nВыберите предложение:", offers_kb())
        return

    if payload in ("offer_free_diagnosis", "offer_first_package", "offer_referral_program"):
        texts = {
            "offer_free_diagnosis": (
                "🎁 БЕСПЛАТНАЯ ДИАГНОСТИКА\n\n"
                "Первое занятие для новых учеников — бесплатно!\n"
                "Мы определим ваш уровень и подберём план обучения.\n\n"
                "Оставьте заявку через 📝 Оставить заявку."
            ),
            "offer_first_package": (
                "💰 СКИДКА НА ПЕРВЫЙ ПАКЕТ\n\n"
                "Скидка на первый пакет занятий для новых учеников.\n"
                "Размер скидки уточняется при оформлении.\n\n"
                "Оставьте заявку через 📝 Оставить заявку."
            ),
            "offer_referral_program": (
                "🤝 РЕФЕРАЛЬНАЯ ПРОГРАММА\n\n"
                "Пригласите друга — получите бонусное занятие!\n\n"
                "Друг получает скидку 20% на первое занятие.\n"
                "Вы получаете +1 занятие после его первой оплаты.\n\n"
                "Реферальная ссылка доступна в Telegram-боте школы."
            ),
        }
        await _reply(api, user_id, message_id, texts[payload], back_menu_kb())
        return

    if payload == "menu_faq":
        await _reply(api, user_id, message_id, "❓ ПОМОЩЬ И ЧАСТЫЕ ВОПРОСЫ", faq_kb())
        return

    if payload == "faq_pay":
        await _reply(
            api, user_id, message_id,
            f"💳 КАК ОПЛАТИТЬ ЗАНЯТИЯ\n\n"
            f"Реквизиты:\n"
            f"🏦 {PAYMENT_BANK_NUMBER}\n"
            f"🏢 {PAYMENT_BANK_NAME}\n"
            f"👤 {PAYMENT_ACCOUNT_HOLDER}\n\n"
            "Порядок:\n"
            "1. Сделайте перевод на реквизиты выше.\n"
            "2. В комментарии укажите имя ученика.\n"
            "3. Откройте 👤 Личный кабинет → 💳 Оплатить занятия и отправьте фото чека.\n"
            "4. После проверки занятия начислятся на ваш баланс.",
            faq_back_kb(),
        )
        return

    if payload == "faq_package":
        await _reply(
            api, user_id, message_id,
            "📦 ЧТО ТАКОЕ ПАКЕТ ЗАНЯТИЙ\n\n"
            "Пакет — это несколько занятий, оплаченных сразу, по сниженной цене за одно.\n"
            "Чем больше пакет, тем ниже цена за занятие.\n"
            "Занятия хранятся на балансе и не сгорают.",
            faq_back_kb(),
        )
        return

    if payload == "faq_reschedule":
        await _reply(
            api, user_id, message_id,
            "🔄 ПЕРЕНОС И ОТМЕНА ЗАНЯТИЙ\n\n"
            "Предупредите преподавателя или администратора не позже чем за 6 часов.\n"
            "Тогда занятие не списывается с баланса.\n\n"
            "Если предупредить позже или не прийти — занятие списывается.",
            faq_back_kb(),
        )
        return

    if payload == "faq_promo":
        await _reply(
            api, user_id, message_id,
            "🎟 ПРОМОКОДЫ\n\n"
            "В школе «Интеграл» действуют два вида промокодов:\n\n"
            "1. Скидка в рублях (₽)\n"
            "Фиксированная сумма, вычитаемая из стоимости.\n"
            "✅ Применяется ко всем типам оплаты: разовые занятия и пакеты.\n\n"
            "2. Процентная скидка (%)\n"
            "Снижает стоимость занятия на указанный процент.\n"
            "❗ Действует только для разовых занятий — к пакетам не применяется.\n\n"
            "⏰ Срок действия: у промокода может быть указан срок — дата и время истечения. "
            "После этого момента промокод перестаёт работать.\n\n"
            "Промокод активируется автоматически при оплате — "
            "вы увидите его статус при переходе к оплате.\n\n"
            "Если промокод не подходит к выбранному формату — бот сообщит об этом.",
            faq_back_kb(),
        )
        return

    # ── Application form callbacks ─────────────────────────────────────────
    if payload.startswith("class_"):
        data["school_class"] = payload.split("class_", 1)[1]
        set_max_fsm_state(user_id, APP_GOAL, data)
        await _reply(api, user_id, message_id, "🎯 Выберите цель обучения:", goal_kb())
        return

    if payload.startswith("goal_"):
        data["goal"] = payload.split("goal_", 1)[1]
        set_max_fsm_state(user_id, APP_LESSON_TYPE, data)
        await _reply(api, user_id, message_id, "📚 Выберите формат занятий:", lesson_type_kb())
        return

    if payload in ("lesson_individual", "lesson_group"):
        data["lesson_type"] = "Индивидуально" if payload == "lesson_individual" else "Мини-группа"
        data.setdefault("subjects", [])
        set_max_fsm_state(user_id, APP_SUBJECTS, data)
        await _reply(api, user_id, message_id, "📖 Выберите предметы (можно несколько):", subjects_kb(data["subjects"]))
        return

    if payload.startswith("subject_"):
        subj = payload.split("subject_", 1)[1]
        subjects = data.get("subjects", [])
        if subj in subjects:
            subjects.remove(subj)
        else:
            subjects.append(subj)
        data["subjects"] = subjects
        set_max_fsm_state(user_id, APP_SUBJECTS, data)
        await _reply(api, user_id, message_id, "📖 Выберите предметы (✅ — выбраны):", subjects_kb(subjects))
        return

    if payload == "subjects_done":
        if not data.get("subjects"):
            await _reply(api, user_id, message_id, "❓ Выберите хотя бы один предмет.", subjects_kb([]))
            return
        set_max_fsm_state(user_id, APP_TEACHER_CHOICE, data)
        await _reply(api, user_id, message_id, "👨‍🏫 Как выбрать преподавателя?", teacher_choice_kb())
        return

    if payload == "teacher_pick":
        data["teacher_choice"] = "Подобрать преподавателя"
        set_max_fsm_state(user_id, APP_CONTACT_METHOD, data)
        await _reply(api, user_id, message_id, "📞 Как с вами связаться?", contact_method_kb())
        return

    if payload == "teacher_specific":
        data["teacher_choice"] = "Выбрать конкретного"
        set_max_fsm_state(user_id, APP_TEACHER_NAME, data)
        await _reply(api, user_id, message_id, "👨‍🏫 Выберите преподавателя:", teachers_list_kb())
        return

    if payload.startswith("pick_teacher_"):
        idx = int(payload.split("pick_teacher_", 1)[1])
        pairs = get_teacher_catalog_name_subject_pairs() or []
        seen: list[str] = []
        for pname, psubj in pairs:
            label = f"{pname} - {psubj}"
            if label not in seen:
                seen.append(label)
        if 0 <= idx < len(seen):
            data["teacher_name"] = seen[idx]
        set_max_fsm_state(user_id, APP_CONTACT_METHOD, data)
        await _reply(api, user_id, message_id, "📞 Как с вами связаться?", contact_method_kb())
        return

    if payload.startswith("contact_"):
        method = payload.split("contact_", 1)[1]
        data["contact_method"] = method
        if method == "MAX":
            data["contact_value"] = f"@{username}" if username else f"MAX:{user_id}"
            set_max_fsm_state(user_id, APP_COMMENT, data)
            await _reply(api, user_id, message_id, "💬 Оставьте комментарий или напишите «-» чтобы пропустить:")
        elif method == "Telegram":
            set_max_fsm_state(user_id, APP_CONTACT_VALUE, data)
            await _reply(api, user_id, message_id, "📱 Введите ваш Telegram @username (например: @username):", back_kb())
        else:  # Звонок
            set_max_fsm_state(user_id, APP_CONTACT_VALUE, data)
            await _reply(api, user_id, message_id, "📞 Введите номер телефона для звонка (например: +79001234567):", back_kb())
        return

    if payload == "noop":
        return

    # Unhandled — go to menu
    logger.debug("Unhandled callback payload=%s state=%s", payload, state)
    await _show_menu(api, user_id, data, message_id=message_id)


async def _handle_contact_value(
    api: MaxApiClient, user_id: int, data: dict, text: str
) -> None:
    method = data.get("contact_method", "")
    if method == "Telegram":
        if not _is_valid_tg_username(text):
            await api.send_message(
                user_id,
                "❓ Введите Telegram @username — минимум 5 символов, начиная с @.",
            )
            return
    else:
        if not _is_valid_phone(text):
            await api.send_message(
                user_id,
                "❓ Формат номера не распознан. Попробуйте ещё раз (например: +79001234567).",
            )
            return
    data["contact_value"] = text
    set_max_fsm_state(user_id, APP_COMMENT, data)
    await api.send_message(
        user_id,
        "💬 Оставьте комментарий к заявке или напишите «-» чтобы пропустить:",
    )


async def _handle_back(
    api: MaxApiClient, user_id: int, state: str, data: dict, message_id: str | None = None
) -> None:
    back_map = {
        APP_NAME:           (APP_USER_TYPE, "Пожалуйста, укажите: вы ученик или родитель?", user_type_kb()),
        APP_CLASS:          (APP_NAME,      "📝 Напишите, как к вам обращаться:", None),
        APP_GOAL:           (APP_CLASS,     "🏫 Выберите класс:", class_kb()),
        APP_LESSON_TYPE:    (APP_GOAL,      "🎯 Выберите цель обучения:", goal_kb()),
        APP_SUBJECTS:       (APP_LESSON_TYPE, "📚 Выберите формат занятий:", lesson_type_kb()),
        APP_TEACHER_CHOICE: (APP_SUBJECTS,  "📖 Выберите предметы:", subjects_kb(data.get("subjects", []))),
        APP_TEACHER_NAME:   (APP_TEACHER_CHOICE, "👨‍🏫 Как выбрать преподавателя?", teacher_choice_kb()),
        APP_CONTACT_METHOD: (APP_TEACHER_CHOICE, "👨‍🏫 Как выбрать преподавателя?", teacher_choice_kb()),
        APP_CONTACT_VALUE:  (APP_CONTACT_METHOD, "📞 Как с вами связаться?", contact_method_kb()),
        APP_COMMENT:        (APP_CONTACT_VALUE, "Введите контактные данные:", back_kb()),
    }
    if state in back_map:
        new_state, prompt, kb = back_map[state]
        set_max_fsm_state(user_id, new_state, data)
        await _reply(api, user_id, message_id, prompt, kb)
    else:
        await _show_menu(api, user_id, data, message_id=message_id)


async def _submit_application(api: MaxApiClient, user_id: int, data: dict) -> None:
    text = _build_application_text(data)
    try:
        tg_bot = TelegramBot(token=TG_BOT_TOKEN)
        await tg_bot.send_message(APPLICATIONS_CHAT_ID, text)
        await tg_bot.session.close()
    except Exception as exc:
        logger.error("Failed to forward application to TG: %s", exc)
        await api.send_message(
            user_id,
            "⚠️ Не удалось отправить заявку. Попробуйте ещё раз или свяжитесь с администратором.",
        )
        return

    set_max_fsm_state(user_id, MENU, {k: v for k, v in data.items() if k == "user_type"})
    await api.send_message(
        user_id,
        "✅ Заявка отправлена! Мы свяжемся с вами в ближайшее время.",
        main_menu_kb(),
    )
