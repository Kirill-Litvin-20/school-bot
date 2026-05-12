import asyncio
import contextlib
import json
import logging
import os
from io import BytesIO
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.types import BufferedInputFile
from aiogram.types import BotCommand, MenuButtonCommands
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import BOT_TOKEN
from config import SCHOOL_BOT_TOKEN, SUPERADMINS
from handlers import router
from shared.database import (
    build_daily_debt_report,
    get_active_admin_telegram_ids,
    get_active_student_telegram_ids,
    get_due_publication_posts,
    init_db,
    is_daily_debt_report_sent,
    mark_publication_post_failed,
    mark_publication_post_sent,
    mark_daily_debt_report_sent,
    run_startup_maintenance_from_env,
    sheets_outbox_pop_pending,
    sheets_outbox_delete,
    sheets_outbox_increment_attempts,
    get_weekly_payouts,
    get_all_student_balances,
    get_attendance_stats,
)
from shared.sheets import get_sheets_client
from shared.health import start_health_server
from shared.logging_setup import get_log_settings, setup_logging


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
try:
    MSK_TZ = ZoneInfo("Europe/Moscow")
except Exception:
    MSK_TZ = timezone(timedelta(hours=3))


def msk_now_naive() -> datetime:
    return datetime.now(MSK_TZ).replace(tzinfo=None)


def _format_debt_report_text(report_data: dict, overdue_days: int) -> str:
    report_date = report_data.get("report_date", "-")
    total_current_debts = report_data.get("total_current_debts", 0)
    new_debts = report_data.get("new_debts", [])
    closed_debts = report_data.get("closed_debts", [])
    overdue_debts = report_data.get("overdue_debts", [])

    lines = [
        f"📊 <b>Ежедневный отчёт по долгам ({report_date})</b>",
        "",
        f"Текущих долгов по направлениям: <b>{total_current_debts}</b>",
        f"Новые долги за день: <b>{len(new_debts)}</b>",
        f"Закрытые долги за день: <b>{len(closed_debts)}</b>",
        f"Долги старше {overdue_days} дн.: <b>{len(overdue_debts)}</b>",
    ]

    if new_debts:
        lines.append("")
        lines.append("<b>Новые долги:</b>")
        for item in new_debts[:20]:
            lines.append(
                f"- {item.get('student_name', '—')} | {item.get('subject_name', '—')} — "
                f"{item.get('teacher_name', '—')} | долг: {abs(item.get('lesson_balance', 0))}"
            )

    if closed_debts:
        lines.append("")
        lines.append("<b>Закрытые долги:</b>")
        for item in closed_debts[:20]:
            lines.append(
                f"- {item.get('student_name', '—')} | {item.get('subject_name', '—')} — "
                f"{item.get('teacher_name', '—')}"
            )

    if overdue_debts:
        lines.append("")
        lines.append(f"<b>Не оплатили более {overdue_days} дней:</b>")
        for item in overdue_debts[:30]:
            lines.append(
                f"- {item.get('student_name', '—')} | {item.get('subject_name', '—')} — "
                f"{item.get('teacher_name', '—')} | дней: {item.get('age_days', 0)} | "
                f"долг: {abs(item.get('lesson_balance', 0))}"
            )

    return "\n".join(lines)


async def debt_report_worker():
    logger = logging.getLogger(__name__)
    overdue_days_raw = os.getenv("SCHOOL_DEBT_OVERDUE_DAYS", "7").strip()
    report_hour_raw = os.getenv("SCHOOL_DEBT_REPORT_HOUR", "10").strip()
    report_weekday_raw = os.getenv("SCHOOL_DEBT_REPORT_WEEKDAY", "1").strip()

    try:
        overdue_days = max(1, int(overdue_days_raw))
    except ValueError:
        overdue_days = 7

    try:
        report_hour = min(23, max(0, int(report_hour_raw)))
    except ValueError:
        report_hour = 10
    try:
        report_weekday = min(6, max(0, int(report_weekday_raw)))
    except ValueError:
        report_weekday = 1

    while True:
        try:
            now = datetime.now(MSK_TZ)
            iso_year, iso_week, _ = now.isocalendar()
            report_key = f"{iso_year}-W{iso_week:02d}"
            schedule_reached = (
                now.weekday() > report_weekday
                or (now.weekday() == report_weekday and now.hour >= report_hour)
            )
            if schedule_reached and not is_daily_debt_report_sent(report_key):
                report_data = build_daily_debt_report(
                    report_date=date.today().isoformat(),
                    overdue_days=overdue_days,
                )
                text = _format_debt_report_text(report_data, overdue_days)

                recipients = sorted(set(list(SUPERADMINS) + get_active_admin_telegram_ids()))
                for recipient in recipients:
                    try:
                        await bot.send_message(recipient, text, parse_mode="HTML")
                    except Exception as exc:
                        logger.warning("Debt report send failed for %s: %s", recipient, exc)

                mark_daily_debt_report_sent(report_key)
        except Exception as exc:
            logger.exception("Debt report worker error: %s", exc)

        await asyncio.sleep(3600)


def _build_publication_text(description: str, links_json: str | None) -> str:
    links: list[str] = []
    if links_json:
        try:
            parsed = json.loads(links_json)
            if isinstance(parsed, list):
                links = [str(item).strip() for item in parsed if str(item).strip()]
        except Exception:
            links = []

    if not links:
        return description

    lines = [description, "", "Ссылки:"]
    for link in links:
        lines.append(f"- {link}")
    return "\n".join(lines)


async def publication_worker():
    logger = logging.getLogger(__name__)
    publish_bot: Bot = bot
    if SCHOOL_BOT_TOKEN:
        publish_bot = Bot(token=SCHOOL_BOT_TOKEN)

    try:
        while True:
            try:
                due_posts = get_due_publication_posts(
                    limit=20,
                    now_ts=msk_now_naive().strftime("%Y-%m-%d %H:%M:%S"),
                )

                for post in due_posts:
                    (
                        post_id,
                        created_by,
                        audience,
                        description,
                        photo_file_id,
                        links_json,
                        _status,
                        _scheduled_for,
                        _sent_at,
                        _created_at,
                        _updated_at,
                        _last_error,
                    ) = post

                    text = _build_publication_text(description, links_json)
                    recipients = set()
                    if audience == "creator_only":
                        recipients.add(int(created_by))
                    elif audience == "students_plus_creator":
                        recipients.update(get_active_student_telegram_ids())
                        recipients.add(int(created_by))
                    else:
                        recipients.update(get_active_student_telegram_ids())

                    sent_any = False
                    failed_count = 0
                    first_error: str | None = None
                    photo_bytes: bytes | None = None
                    photo_send_enabled = bool(photo_file_id)

                    if photo_file_id and publish_bot is not bot:
                        try:
                            file_info = await bot.get_file(photo_file_id)
                            buffer = BytesIO()
                            await bot.download_file(file_info.file_path, destination=buffer)
                            photo_bytes = buffer.getvalue()
                            if not photo_bytes:
                                photo_send_enabled = False
                        except Exception as exc:
                            logger.warning(
                                "Publication %s: failed to fetch photo for cross-bot send, fallback to text-only: %s",
                                post_id,
                                exc,
                            )
                            photo_send_enabled = False

                    for telegram_id in sorted(recipients):
                        try:
                            if photo_send_enabled and photo_file_id:
                                caption = text if len(text) <= 1024 else text[:1021] + "..."
                                photo_payload = (
                                    BufferedInputFile(photo_bytes, filename=f"publication_{post_id}.jpg")
                                    if photo_bytes is not None
                                    else photo_file_id
                                )
                                await publish_bot.send_photo(
                                    chat_id=telegram_id,
                                    photo=photo_payload,
                                    caption=caption,
                                )
                                if len(text) > 1024:
                                    await publish_bot.send_message(telegram_id, text)
                            else:
                                await publish_bot.send_message(telegram_id, text)
                            sent_any = True
                        except Exception as exc:
                            failed_count += 1
                            if first_error is None:
                                first_error = str(exc)

                    if not recipients:
                        mark_publication_post_failed(
                            int(post_id),
                            "Нет активных учеников с Telegram ID для рассылки.",
                        )
                        logger.warning("Publication %s skipped: no recipients", post_id)
                    elif sent_any:
                        mark_publication_post_sent(int(post_id))
                        logger.info(
                            "Publication %s sent: recipients=%s failed=%s photo=%s",
                            post_id,
                            len(recipients),
                            failed_count,
                            bool(photo_file_id),
                        )
                    else:
                        error_text = f"Не удалось отправить публикацию получателям. Ошибок: {failed_count}"
                        if first_error:
                            error_text += f". Пример ошибки: {first_error}"
                        mark_publication_post_failed(
                            int(post_id),
                            error_text,
                        )
                        logger.warning(
                            "Publication %s failed: recipients=%s failed=%s first_error=%s",
                            post_id,
                            len(recipients),
                            failed_count,
                            first_error,
                        )
            except Exception as exc:
                logger.exception("Publication worker error: %s", exc)

            await asyncio.sleep(30)
    finally:
        if publish_bot is not bot:
            await publish_bot.session.close()


async def update_summary_sheets() -> None:
    """Push Выплаты, Балансы, Статистика to Google Sheets in background threads."""
    logger = logging.getLogger(__name__)
    client = get_sheets_client()
    if not client.is_configured():
        return
    try:
        payouts = await asyncio.to_thread(get_weekly_payouts)
        await asyncio.to_thread(client.update_payouts_sheet, payouts)
        logger.info("Sheets: Выплаты updated")
    except Exception as exc:
        logger.warning("Sheets: Выплаты update failed: %s", exc)
    try:
        balances = await asyncio.to_thread(get_all_student_balances)
        await asyncio.to_thread(client.update_balances_sheet, balances)
        logger.info("Sheets: Балансы updated")
    except Exception as exc:
        logger.warning("Sheets: Балансы update failed: %s", exc)
    try:
        stats = await asyncio.to_thread(get_attendance_stats)
        await asyncio.to_thread(client.update_stats_sheet, stats)
        logger.info("Sheets: Статистика updated")
    except Exception as exc:
        logger.warning("Sheets: Статистика update failed: %s", exc)


async def sheets_summary_worker():
    """Auto-update summary sheets:
    - Every Tuesday 09:00 MSK → Выплаты + Балансы + Статистика
    - Every day    06:00 MSK → Балансы + Статистика
    """
    logger = logging.getLogger(__name__)
    last_tuesday_update = None
    last_daily_update = None

    while True:
        try:
            now = datetime.now(MSK_TZ)
            today_str = now.strftime("%Y-%m-%d")

            # Every Tuesday 09:00
            if now.weekday() == 1 and now.hour == 9 and last_tuesday_update != today_str:
                logger.info("Sheets: Tuesday payout update triggered")
                await update_summary_sheets()
                last_tuesday_update = today_str

            # Every day 06:00
            if now.hour == 6 and last_daily_update != today_str:
                logger.info("Sheets: Daily stats update triggered")
                try:
                    client = get_sheets_client()
                    if client.is_configured():
                        balances = await asyncio.to_thread(get_all_student_balances)
                        await asyncio.to_thread(client.update_balances_sheet, balances)
                        stats = await asyncio.to_thread(get_attendance_stats)
                        await asyncio.to_thread(client.update_stats_sheet, stats)
                except Exception as exc:
                    logger.warning("Sheets daily update failed: %s", exc)
                last_daily_update = today_str

        except Exception as exc:
            logger.exception("Sheets summary worker error: %s", exc)

        await asyncio.sleep(300)  # check every 5 minutes


async def sheets_outbox_worker():
    """Retry attendance rows that failed to reach Google Sheets."""
    logger = logging.getLogger(__name__)
    client = get_sheets_client()
    while True:
        try:
            if client.is_configured():
                pending = await asyncio.to_thread(sheets_outbox_pop_pending)
                for item in pending:
                    try:
                        ok = await asyncio.to_thread(client.append_attendance, item["payload"])
                        if ok:
                            await asyncio.to_thread(sheets_outbox_delete, item["outbox_id"])
                            logger.info("Sheets outbox: flushed attendance_id=%s", item["attendance_id"])
                        else:
                            await asyncio.to_thread(
                                sheets_outbox_increment_attempts, item["outbox_id"], "returned False"
                            )
                    except Exception as exc:
                        await asyncio.to_thread(
                            sheets_outbox_increment_attempts, item["outbox_id"], str(exc)
                        )
                        logger.warning(
                            "Sheets outbox retry failed for attendance_id=%s: %s",
                            item["attendance_id"], exc,
                        )
        except Exception as exc:
            logger.exception("Sheets outbox worker error: %s", exc)
        await asyncio.sleep(60)


async def cleanup_worker():
    """Периодически оптимизирует БД: удаляет старые данные и запускает VACUUM."""
    from shared.database import cleanup_old_data, optimize_database

    logger = logging.getLogger(__name__)
    cleanup_hour = int(os.getenv("SCHOOL_DB_CLEANUP_HOUR", "3"))

    while True:
        try:
            now = datetime.now(MSK_TZ)
            should_cleanup = now.hour == cleanup_hour and now.minute < 5

            if should_cleanup:
                logger.info("Starting database cleanup...")
                cleanup_stats = cleanup_old_data(days=14)
                logger.info("Cleanup completed: %s", cleanup_stats)
                optimize_stats = optimize_database()
                logger.info("Database optimization completed: %s", optimize_stats)
                await asyncio.sleep(3600)
        except Exception as exc:
            logger.exception("Cleanup worker error: %s", exc)

        await asyncio.sleep(3600)


async def main():
    log_level, log_dir = get_log_settings()
    log_file = setup_logging("school_admin_bot", log_level=log_level, log_dir=log_dir)
    logging.getLogger(__name__).info("Starting admin bot, log file: %s", log_file)
    init_db()
    maintenance_executed = run_startup_maintenance_from_env(preserve_superadmin_ids=SUPERADMINS)
    if maintenance_executed:
        logging.getLogger(__name__).info("Startup maintenance completed: student data reset for testing.")
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Запустить бота"),
                BotCommand(command="menu", description="Открыть меню по роли"),
            ]
        )
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Failed to set bot commands on startup (will continue): %s",
            exc,
        )
    dp.include_router(router)

    health_runner, health_state = await start_health_server(
        app_name="school_admin_bot",
        port_env="SCHOOL_ADMIN_HEALTH_PORT",
    )

    @dp.update.outer_middleware()
    async def _touch_health(handler, event, data):
        try:
            return await handler(event, data)
        finally:
            health_state.touch()

    report_task = asyncio.create_task(debt_report_worker())
    publication_task = asyncio.create_task(publication_worker())
    cleanup_task = asyncio.create_task(cleanup_worker())
    sheets_task = asyncio.create_task(sheets_outbox_worker())
    sheets_summary_task = asyncio.create_task(sheets_summary_worker())
    try:
        while True:
            try:
                await dp.start_polling(bot)
                break
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Admin polling crashed, retry in 5s: %s",
                    exc,
                )
                await asyncio.sleep(5)
    finally:
        report_task.cancel()
        publication_task.cancel()
        cleanup_task.cancel()
        sheets_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await report_task
        with contextlib.suppress(asyncio.CancelledError):
            await publication_task
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
        with contextlib.suppress(asyncio.CancelledError):
            await sheets_task
        sheets_summary_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sheets_summary_task
        if health_runner is not None:
            await health_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
