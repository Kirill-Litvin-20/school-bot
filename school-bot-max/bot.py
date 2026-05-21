"""MAX bot entry point.

Long-polling loop over MAX Bot API. Dispatches updates to handlers.py.
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import MAX_BOT_TOKEN
from handlers import (
    handle_bot_started,
    handle_callback,
    handle_file,
    handle_photo,
    handle_text,
)
from shared.database import (
    get_debt_rows_for_reminder_max,
    get_payment_platform_info,
    get_stale_pending_payment_requests,
    init_db,
    log_admin_action,
    mark_debt_reminder_sent,
    try_transition_payment_request_status,
)
from shared.logging_setup import get_log_settings, setup_logging
from shared.max_api import MaxApiClient


async def process_update(api: MaxApiClient, update: dict) -> None:
    update_type = update.get("update_type", "")

    try:
        if update_type == "bot_started":
            user = update.get("user", {})
            await handle_bot_started(
                api=api,
                user_id=user.get("user_id"),
                name=user.get("name", ""),
                username=user.get("username"),
                payload=update.get("payload", ""),
            )

        elif update_type == "message_created":
            msg = update.get("message", {})
            sender = msg.get("sender", {})
            user_id = sender.get("user_id")
            username = sender.get("username")
            name = sender.get("name", "")
            body = msg.get("body", {}) or {}
            text = (body.get("text") or "").strip()
            attachments = body.get("attachments") or []

            photo_att = next((a for a in attachments if a.get("type") in ("photo", "image")), None)
            file_att = next((a for a in attachments if a.get("type") == "file"), None)

            if photo_att:
                photo_url = (photo_att.get("payload") or {}).get("url", "")
                await handle_photo(api, user_id, username, name, photo_url, text or None)
            elif file_att:
                payload = file_att.get("payload") or {}
                await handle_file(
                    api,
                    user_id,
                    username,
                    name,
                    payload.get("url", ""),
                    payload.get("filename", ""),
                    text or None,
                    mime_type=payload.get("mimeType") or payload.get("mime_type") or "",
                )
            elif text:
                await handle_text(api, user_id, username, name, text)

        elif update_type == "message_callback":
            cb = update.get("callback", {})
            user = cb.get("user", {})
            orig_msg = update.get("message") or {}
            message_id = (orig_msg.get("body") or {}).get("mid")
            await handle_callback(
                api=api,
                callback_id=cb.get("callback_id", ""),
                user_id=user.get("user_id"),
                username=user.get("username"),
                name=user.get("name", ""),
                payload=cb.get("payload", ""),
                message_id=message_id,
            )

    except Exception:
        logging.getLogger(__name__).exception(
            "Unhandled error processing update_type=%s", update_type
        )


async def debt_reminder_worker(api: MaxApiClient) -> None:
    logger = logging.getLogger(__name__)
    reminder_weekday = int(os.getenv("SCHOOL_DEBT_REMINDER_WEEKDAY", "0"))
    reminder_hour = int(os.getenv("SCHOOL_DEBT_REMINDER_HOUR", "10"))

    while True:
        try:
            now = datetime.now(timezone.utc).astimezone()
            iso_year, iso_week, _ = now.isocalendar()
            reminder_key = f"{iso_year}-W{iso_week:02d}"
            schedule_reached = (
                now.weekday() > reminder_weekday
                or (now.weekday() == reminder_weekday and now.hour >= reminder_hour)
            )
            if schedule_reached:
                rows = get_debt_rows_for_reminder_max(reminder_key)
                grouped: dict[int, list] = {}
                for student_lesson_id, max_id, student_name, teacher_name, subject_name, lesson_balance in rows:
                    grouped.setdefault(max_id, []).append(
                        (student_lesson_id, student_name, teacher_name, subject_name, lesson_balance)
                    )

                for max_id, debts in grouped.items():
                    student_name = debts[0][1]
                    lines = [
                        "❗❗❗🔴 ВНИМАНИЕ! У ВАС ЗАДОЛЖЕННОСТЬ! 🔴❗❗❗",
                        "",
                        f"Ученик: {student_name}",
                        "",
                        "Направления с задолженностью:",
                    ]
                    for _, _, teacher_name, subject_name, lesson_balance in debts:
                        lines.append(
                            f"- {subject_name} — {teacher_name}: задолженность {abs(lesson_balance)} занят."
                        )
                    lines += ["", "❗❗❗ Пожалуйста, внесите оплату или свяжитесь с администратором школы. ❗❗❗"]

                    try:
                        await api.send_message(max_id, "\n".join(lines))
                    except Exception as exc:
                        logger.warning("MAX debt reminder failed for %s: %s", max_id, exc)
                        continue

                    for student_lesson_id, *_ in debts:
                        mark_debt_reminder_sent(student_lesson_id, reminder_key)

        except Exception as exc:
            logger.exception("MAX debt reminder worker error: %s", exc)

        await asyncio.sleep(3600)


async def stale_payment_worker(api: MaxApiClient) -> None:
    """Once an hour, expire MAX-only pending payments older than N days.

    Only processes payments where telegram_user_id IS NULL (MAX-origin payments).
    TG-origin payments are handled by the school-bot stale worker.
    """
    logger = logging.getLogger(__name__)
    raw = os.getenv("SCHOOL_PAYMENT_AUTO_EXPIRE_DAYS", "30").strip()
    try:
        days = max(1, int(raw))
    except ValueError:
        days = 30

    while True:
        try:
            stale = get_stale_pending_payment_requests(older_than_days=days)
            for row in stale:
                (
                    payment_request_id,
                    telegram_user_id,
                    _username,
                    _full_name,
                    _caption_text,
                    _file_id,
                    _file_type,
                    status,
                    *_rest,
                ) = row
                if telegram_user_id:
                    continue  # TG bot handles these

                transitioned = try_transition_payment_request_status(
                    payment_request_id=payment_request_id,
                    allowed_from_statuses=["pending", "processing"],
                    new_status="expired",
                    admin_id=None,
                )
                if not transitioned:
                    continue

                log_admin_action(
                    admin_telegram_id=None,
                    action_type="payment_auto_expired",
                    target_type="payment_request",
                    target_id=payment_request_id,
                    details=f"days_threshold={days};prev_status={status};platform=max",
                    status="success",
                )

                source_platform, max_user_id = get_payment_platform_info(payment_request_id)
                if source_platform == "max" and max_user_id:
                    try:
                        await api.send_message(
                            max_user_id,
                            f"⌛ Ваша оплата #{payment_request_id} не была "
                            f"подтверждена в течение {days} дней и переведена в статус "
                            "«просрочена». Если оплата уже сделана — отправьте чек "
                            "ещё раз через раздел оплаты в боте, или свяжитесь с "
                            "администратором.",
                        )
                    except Exception as exc:
                        logger.warning(
                            "MAX stale payment DM failed for user %s: %s",
                            max_user_id,
                            exc,
                        )
        except Exception as exc:
            logger.exception("MAX stale payment worker error: %s", exc)

        await asyncio.sleep(3600)


async def polling_loop(api: MaxApiClient) -> None:
    logger = logging.getLogger(__name__)
    marker = 0
    logger.info("MAX bot polling started")

    while True:
        try:
            result = await api.get_updates(offset=marker, timeout=25)
            updates = result.get("updates") or []
            if updates:
                marker = result.get("marker", marker)
                for update in updates:
                    await process_update(api, update)
        except asyncio.CancelledError:
            logger.info("Polling cancelled, shutting down")
            break
        except Exception as exc:
            logger.warning("Polling error, retry in 5s: %s", exc)
            await asyncio.sleep(5)


async def main() -> None:
    log_level, log_dir = get_log_settings()
    log_file = setup_logging("max_bot", log_level=log_level, log_dir=log_dir)
    logging.getLogger(__name__).info("Starting MAX bot, log: %s", log_file)

    init_db()

    api = MaxApiClient(MAX_BOT_TOKEN)
    try:
        me = await api.get_me()
        logging.getLogger(__name__).info("MAX bot identity: %s", me)
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not fetch bot info: %s", exc)

    try:
        await api.set_commands([
            {"name": "start", "description": "Запустить бота"},
            {"name": "menu", "description": "Открыть главное меню"},
        ])
        logging.getLogger(__name__).info("MAX bot commands set")
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not set MAX bot commands (non-critical): %s", exc)

    reminder_task = asyncio.create_task(debt_reminder_worker(api))
    stale_task = asyncio.create_task(stale_payment_worker(api))
    try:
        await polling_loop(api)
    finally:
        reminder_task.cancel()
        stale_task.cancel()
        for t in (reminder_task, stale_task):
            try:
                await t
            except asyncio.CancelledError:
                pass


if __name__ == "__main__":
    asyncio.run(main())
