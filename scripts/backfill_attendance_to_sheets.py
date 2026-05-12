"""Backfill historical attendance records into Google Sheets.

Usage:
    # Перелить всё
    python scripts/backfill_attendance_to_sheets.py

    # Только с определённой даты
    python scripts/backfill_attendance_to_sheets.py --from 2025-01-01

    # Только показать, сколько записей будет, ничего не писать
    python scripts/backfill_attendance_to_sheets.py --dry-run

    # Принудительно переформатировать лист (шапка, цвета, ширины)
    python scripts/backfill_attendance_to_sheets.py --reformat
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from shared.database import get_all_attendance_for_backfill
from shared.sheets import get_sheets_client


# ─── Console helpers ───────────────────────────────────────────────────────────

def _print_header():
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║        Backfill: посещения → Google Sheets           ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()


def _bar(done: int, total: int, width: int = 40) -> str:
    if total == 0:
        return "[" + "─" * width + "] 0/0"
    filled = int(width * done / total)
    bar = "█" * filled + "░" * (width - filled)
    pct = int(100 * done / total)
    return f"[{bar}] {done}/{total} ({pct}%)"


def _print_progress(done: int, total: int, label: str = ""):
    line = f"\r  {_bar(done, total)}  {label:<30}"
    print(line, end="", flush=True)


# ─── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill attendance → Google Sheets")
    parser.add_argument(
        "--from",
        dest="from_date",
        metavar="YYYY-MM-DD",
        help="Начать с этой даты (включительно). По умолчанию — всё.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только посчитать записи, ничего не писать в таблицу.",
    )
    parser.add_argument(
        "--reformat",
        action="store_true",
        help="Применить форматирование листа (цвета, ширины) без добавления строк.",
    )
    args = parser.parse_args()

    _print_header()

    # ── 1. Проверка конфигурации ───────────────────────────────────────────────
    client = get_sheets_client()
    if not client.is_configured():
        print("❌  Sheets не настроен.")
        print("    Добавь в .env:")
        print("    GOOGLE_SERVICE_ACCOUNT_JSON_PATH=/path/to/key.json")
        print("    SHEETS_SPREADSHEET_ID=your_id")
        sys.exit(1)

    print("✅  Конфигурация найдена.")

    # ── 2. Получаем данные из БД ──────────────────────────────────────────────
    from_date: str | None = args.from_date
    if from_date:
        try:
            datetime.strptime(from_date, "%Y-%m-%d")
        except ValueError:
            print(f"❌  Неверный формат даты: {from_date}. Используй YYYY-MM-DD.")
            sys.exit(1)

    print(f"  Загружаю данные из БД{f' (с {from_date})' if from_date else ' (всё)'}...", end="", flush=True)
    rows = get_all_attendance_for_backfill(from_date=from_date)
    print(f"  найдено {len(rows)} записей.")
    print()

    if not rows:
        print("  В базе нет записей посещаемости. Ничего не делаю.")
        return

    # Краткая статистика по данным
    present = sum(1 for r in rows if r["status"] == "Был")
    absent = sum(1 for r in rows if r["status"] == "Не был")
    cancelled = len(rows) - present - absent
    dates = [r["lesson_datetime"][:10] for r in rows if r["lesson_datetime"]]
    date_range = f"{min(dates)} → {max(dates)}" if dates else "—"

    print("  📊 Статистика:")
    print(f"     Всего записей : {len(rows)}")
    print(f"     Был           : {present}")
    print(f"     Не был        : {absent}")
    if cancelled:
        print(f"     Отменено      : {cancelled}")
    print(f"     Период        : {date_range}")
    print()

    if args.dry_run:
        print("  ℹ️  Режим --dry-run: в таблицу ничего не записывается.")
        return

    # ── 3. Форматирование (если --reformat или первый запуск) ─────────────────
    if args.reformat:
        print("  🎨 Применяю форматирование листа...", end="", flush=True)
        try:
            spreadsheet = client._open_spreadsheet()
            if spreadsheet:
                ws = client._get_or_add_worksheet(spreadsheet, "Журнал")
                client._format_journal(ws, spreadsheet)
                print(" готово.")
            else:
                print("\n  ⚠️  Не удалось открыть таблицу.")
        except Exception as exc:
            print(f"\n  ⚠️  Не удалось применить форматирование: {exc}")
        print()

    # ── 4. Пишем в Sheets ─────────────────────────────────────────────────────
    print("  📤 Записываю в Google Sheets...")
    if args.reformat:
        print("  ℹ️  Режим --reformat: очищаю лист и перезаписываю все данные (новые сверху).")
    print()

    if args.reformat:
        # Single full rebuild pass with progress
        _print_progress(0, len(rows), "очищаю и перезаписываю...")
        added_total, skipped_total = client.batch_append_rows(rows, rebuild=True)
        _print_progress(len(rows), len(rows), "готово")
    else:
        # Incremental batches with progress
        batch_size = 200
        total = len(rows)
        added_total = 0
        skipped_total = 0

        for start in range(0, total, batch_size):
            chunk = rows[start:start + batch_size]
            label = f"{chunk[0]['lesson_datetime'][:10]}" if chunk else ""
            _print_progress(start, total, label)
            added, skipped = client.batch_append_rows(chunk)
            added_total += added
            skipped_total += skipped

        _print_progress(total, total, "готово")
    print()
    print()

    # ── 5. Итог ────────────────────────────────────────────────────────────────
    print("╔══════════════════════════════════════════════════════╗")
    if added_total:
        print(f"║  ✅  Добавлено строк    : {added_total:<28}║")
    if skipped_total:
        print(f"║  ⏭   Уже были в таблице : {skipped_total:<28}║")
    print("╚══════════════════════════════════════════════════════╝")
    print()
    print("  Открой таблицу и проверь лист «Журнал».")
    print()


if __name__ == "__main__":
    main()
