import asyncio
import contextlib
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, MenuButtonCommands

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import ADMIN_ID, BOT_TOKEN
from handlers import routers
from shared.database import (
    get_debt_rows_for_reminder,
    get_stale_pending_payment_requests,
    init_db,
    log_admin_action,
    mark_debt_reminder_sent,
    run_startup_maintenance_from_env,
    try_transition_payment_request_status,
)
from shared.health import start_health_server
from shared.logging_setup import get_log_settings, setup_logging


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
try:
    MSK_TZ = ZoneInfo("Europe/Moscow")
except Exception:
    MSK_TZ = timezone(timedelta(hours=3))

for router in routers:
    dp.include_router(router)


async def debt_reminder_worker(bot: Bot):
    logger = logging.getLogger(__name__)
    reminder_weekday_raw = os.getenv("SCHOOL_DEBT_REMINDER_WEEKDAY", "0").strip()
    reminder_hour_raw = os.getenv("SCHOOL_DEBT_REMINDER_HOUR", "10").strip()
    try:
        reminder_weekday = min(6, max(0, int(reminder_weekday_raw)))
    except ValueError:
        reminder_weekday = 0
    try:
        reminder_hour = min(23, max(0, int(reminder_hour_raw)))
    except ValueError:
        reminder_hour = 10

    while True:
        try:
            now = datetime.now(MSK_TZ)
            iso_year, iso_week, _ = now.isocalendar()
            reminder_key = f"{iso_year}-W{iso_week:02d}"
            schedule_reached = (
                now.weekday() > reminder_weekday
                or (now.weekday() == reminder_weekday and now.hour >= reminder_hour)
            )
            if not schedule_reached:
                await asyncio.sleep(3600)
                continue

            rows = get_debt_rows_for_reminder(reminder_key)
            grouped = defaultdict(list)

            for row in rows:
                student_lesson_id, telegram_id, student_name, teacher_name, subject_name, lesson_balance = row
                grouped[telegram_id].append(
                    (
                        student_lesson_id,
                        student_name,
                        teacher_name,
                        subject_name,
                        lesson_balance,
                    )
                )

            for telegram_id, debts in grouped.items():
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
                lines.append("")
                lines.append("❗❗❗ Пожалуйста, внесите оплату или свяжитесь с администратором школы. ❗❗❗")

                try:
                    await bot.send_message(telegram_id, "\n".join(lines))
                except Exception as exc:
                    logger.warning("Debt reminder send failed for %s: %s", telegram_id, exc)
                    continue

                for student_lesson_id, *_ in debts:
                    mark_debt_reminder_sent(student_lesson_id, reminder_key)
        except Exception as exc:
            logger.exception("Debt reminder worker error: %s", exc)

        await asyncio.sleep(3600)


async def stale_payment_worker(bot: Bot):
    """Once an hour, expire pending/processing payments older than N days.

    Default N = 30, override via SCHOOL_PAYMENT_AUTO_EXPIRE_DAYS. Runs in the
    school bot (not admin) so the DM to the student goes from the same chat
    where they originally uploaded the receipt.
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
                    details=f"days_threshold={days};prev_status={status}",
                    status="success",
                )

                if telegram_user_id:
                    try:
                        await bot.send_message(
                            telegram_user_id,
                            f"⌛ Ваша оплата #{payment_request_id} не была "
                            f"подтверждена в течение {days} дней и переведена в статус "
                            "«просрочена». Если оплата уже сделана — отправьте чек "
                            "ещё раз через раздел оплаты в боте, или свяжитесь с "
                            "администратором.",
                        )
                    except Exception as exc:
                        logger.warning(
                            "Stale payment DM failed for user %s: %s",
                            telegram_user_id,
                            exc,
                        )
        except Exception as exc:
            logger.exception("Stale payment worker error: %s", exc)

        await asyncio.sleep(3600)


async def main():
    log_level, log_dir = get_log_settings()
    log_file = setup_logging("school_bot", log_level=log_level, log_dir=log_dir)
    logging.getLogger(__name__).info("Starting school bot, log file: %s", log_file)
    init_db()
    maintenance_executed = run_startup_maintenance_from_env(preserve_superadmin_ids=[ADMIN_ID])
    if maintenance_executed:
        logging.getLogger(__name__).info("Startup maintenance completed: student data reset for testing.")
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Запустить бота"),
                BotCommand(command="menu", description="Открыть главное меню"),
            ]
        )
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Failed to set bot commands on startup (will continue): %s",
            exc,
        )
    health_runner, health_state = await start_health_server(
        app_name="school_bot",
        port_env="SCHOOL_HEALTH_PORT",
    )

    @dp.update.outer_middleware()
    async def _touch_health(handler, event, data):
        try:
            return await handler(event, data)
        finally:
            health_state.touch()

    reminder_task = asyncio.create_task(debt_reminder_worker(bot))
    stale_task = asyncio.create_task(stale_payment_worker(bot))
    try:
        while True:
            try:
                await dp.start_polling(bot)
                break
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "School polling crashed, retry in 5s: %s",
                    exc,
                )
                await asyncio.sleep(5)
    finally:
        reminder_task.cancel()
        stale_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reminder_task
        with contextlib.suppress(asyncio.CancelledError):
            await stale_task
        if health_runner is not None:
            await health_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
