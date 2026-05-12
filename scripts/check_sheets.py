"""Quick connectivity check for Google Sheets integration.

Usage:
    python scripts/check_sheets.py

Reads GOOGLE_SERVICE_ACCOUNT_JSON_PATH and SHEETS_SPREADSHEET_ID from .env,
then writes one test row to the 'Посещения' sheet and prints the result.
"""

import os
import sys
from pathlib import Path
from datetime import datetime

# Allow imports from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from shared.sheets import get_sheets_client


def main() -> None:
    client = get_sheets_client()

    if not client.is_configured():
        json_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_PATH", "")
        sheet_id = os.getenv("SHEETS_SPREADSHEET_ID", "")
        print("❌ Sheets не настроен.")
        if not sheet_id:
            print("   Не задан SHEETS_SPREADSHEET_ID в .env")
        if not json_path:
            print("   Не задан GOOGLE_SERVICE_ACCOUNT_JSON_PATH в .env")
        elif not Path(json_path).is_file():
            print(f"   Файл ключа не найден: {json_path}")
        sys.exit(1)

    print("✅ Конфигурация найдена. Пробуем записать тестовую строку...")

    test_row = {
        "attendance_id": 0,
        "lesson_datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "teacher_name": "Тест Преподаватель",
        "student_name": "Тест Ученик",
        "subject_name": "Тест Предмет",
        "tariff_type": "package",
        "status": "present",
        "balance_before": 10,
        "balance_after": 9,
        "marked_by_name": "check_sheets.py",
    }

    ok = client.append_attendance(test_row)
    if ok:
        print("✅ Тестовая строка записана! Открой таблицу и проверь лист «Посещения».")
    else:
        print("❌ Не удалось записать строку. Смотри логи выше.")
        sys.exit(1)


if __name__ == "__main__":
    main()
