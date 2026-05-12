"""Google Sheets integration for attendance sync.

Configured via environment variables:
  GOOGLE_SERVICE_ACCOUNT_JSON_PATH  — path to the service account JSON key file
  SHEETS_SPREADSHEET_ID             — Google Sheets document ID

If either variable is missing the client silently does nothing, so local dev
and tests work without any Google credentials.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_ATTENDANCE_SHEET = "Посещения"
_HEADER_ROW = [
    "Дата-время",
    "Препод",
    "Ученик",
    "Направление",
    "Тариф",
    "Статус",
    "Баланс до",
    "Баланс после",
    "Кто отметил",
    "ID отметки",
]

# Status display values
_STATUS_LABELS = {
    "present": "Был",
    "absent": "Не был",
    "cancelled": "Отменено",
}

# Тариф display values
_TARIFF_LABELS = {
    "package": "Пакет",
    "per_lesson": "Поурочно",
    "subscription": "Абонемент",
}


class SheetsClient:
    """Thin wrapper around gspread for attendance append operations."""

    def __init__(self) -> None:
        self._spreadsheet_id = os.getenv("SHEETS_SPREADSHEET_ID", "").strip()
        self._json_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_PATH", "").strip()
        self._gc = None  # lazy gspread client

    def is_configured(self) -> bool:
        return bool(self._spreadsheet_id and self._json_path and os.path.isfile(self._json_path))

    def _get_client(self):
        if self._gc is not None:
            return self._gc
        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError:
            logger.error("gspread / google-auth not installed — run pip install -r requirements.txt")
            return None

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(self._json_path, scopes=scopes)
        self._gc = gspread.authorize(creds)
        return self._gc

    def _get_or_create_sheet(self, spreadsheet):
        """Return the attendance worksheet, creating it with a header if new."""
        try:
            ws = spreadsheet.worksheet(_ATTENDANCE_SHEET)
        except Exception:
            ws = spreadsheet.add_worksheet(title=_ATTENDANCE_SHEET, rows=1000, cols=len(_HEADER_ROW))
            ws.append_row(_HEADER_ROW, value_input_option="USER_ENTERED")
            # Freeze header row
            ws.freeze(rows=1)
            logger.info("Created sheet '%s' with header", _ATTENDANCE_SHEET)
        return ws

    def _find_row_by_attendance_id(self, ws, attendance_id: int) -> int | None:
        """Return 1-based row index of a row whose column J equals attendance_id, or None."""
        try:
            col_j = ws.col_values(10)  # column J = index 10
            for i, val in enumerate(col_j):
                if val and str(val).strip() == str(attendance_id):
                    return i + 1  # 1-based
        except Exception:
            pass
        return None

    def append_attendance(self, row: dict[str, Any]) -> bool:
        """Write one attendance record to the sheet.

        row keys: attendance_id, lesson_datetime, teacher_name, student_name,
                  subject_name, tariff_type, status, balance_before,
                  balance_after, marked_by_name
        Returns True on success, False on any error.
        """
        if not self.is_configured():
            return False

        gc = self._get_client()
        if gc is None:
            return False

        try:
            spreadsheet = gc.open_by_key(self._spreadsheet_id)
            ws = self._get_or_create_sheet(spreadsheet)

            attendance_id = row.get("attendance_id")
            if attendance_id and self._find_row_by_attendance_id(ws, attendance_id):
                logger.debug("attendance_id=%s already in sheet, skipping", attendance_id)
                return True

            status_label = _STATUS_LABELS.get(row.get("status", ""), row.get("status", ""))
            tariff_label = _TARIFF_LABELS.get(row.get("tariff_type", ""), row.get("tariff_type", ""))

            values = [
                row.get("lesson_datetime", ""),
                row.get("teacher_name", ""),
                row.get("student_name", ""),
                row.get("subject_name", ""),
                tariff_label,
                status_label,
                row.get("balance_before", ""),
                row.get("balance_after", ""),
                row.get("marked_by_name", ""),
                str(attendance_id) if attendance_id else "",
            ]
            ws.append_row(values, value_input_option="USER_ENTERED")
            logger.info("Sheets: wrote attendance_id=%s", attendance_id)
            return True

        except Exception as exc:
            logger.error("Sheets append failed: %s", exc)
            return False

    def mark_cancelled(self, attendance_id: int, cancelled_by: str, lesson_datetime: str) -> bool:
        """Append a cancellation marker row for a previously synced attendance record."""
        if not self.is_configured():
            return False
        gc = self._get_client()
        if gc is None:
            return False
        try:
            spreadsheet = gc.open_by_key(self._spreadsheet_id)
            ws = self._get_or_create_sheet(spreadsheet)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            values = [
                now,
                "",
                "",
                "",
                "",
                "↩️ Отменено",
                "",
                "",
                cancelled_by,
                str(attendance_id),
            ]
            ws.append_row(values, value_input_option="USER_ENTERED")
            logger.info("Sheets: marked attendance_id=%s as cancelled", attendance_id)
            return True
        except Exception as exc:
            logger.error("Sheets cancel failed: %s", exc)
            return False


# Module-level singleton
_client: SheetsClient | None = None


def get_sheets_client() -> SheetsClient:
    global _client
    if _client is None:
        _client = SheetsClient()
    return _client
