"""Google Sheets integration: attendance journal + summary sheets.

Configured via environment variables:
  GOOGLE_SERVICE_ACCOUNT_JSON_PATH  — path to service account JSON key
  SHEETS_SPREADSHEET_ID             — Google Sheets document ID
  LESSON_RATE                       — rub per lesson (default 1000)

Sheets created / managed:
  «Журнал»     — append-only attendance log (green=present, red=absent)
  «Выплаты»    — weekly teacher payouts (auto-updated every Tuesday)
  «Балансы»    — student balances (updated after every attendance mark)
  «Статистика» — period and weekday stats (updated daily)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ── Sheet names ────────────────────────────────────────────────────────────────
_JOURNAL_SHEET   = "Журнал"
_PAYOUTS_SHEET   = "Выплаты"
_BALANCES_SHEET  = "Балансы"
_STATS_SHEET     = "Статистика"

_JOURNAL_HEADER  = ["Дата-время", "Препод", "Ученик", "Направление",
                    "Тариф", "Статус", "Баланс до", "Баланс после",
                    "Кто отметил", "ID отметки"]
_PAYOUTS_HEADER  = ["Период", "Преподаватель", "Занятий", "Сумма"]
_BALANCES_HEADER = ["Ученик", "Направление", "Препод", "Баланс", "Статус"]
_STATS_HEADER    = ["Показатель", "Занятий", "", "День недели", "Занятий"]  # 5-col split view

# ── Display maps ───────────────────────────────────────────────────────────────
_STATUS_LABELS = {"present": "Был", "absent": "Не был", "cancelled": "Отменено"}
_TARIFF_LABELS = {"package": "Пакет", "per_lesson": "Поурочно", "subscription": "Абонемент"}

# ── Colors (RGB 0–1) ───────────────────────────────────────────────────────────
_C_JOURNAL_HEADER  = {"red": 0.196, "green": 0.365, "blue": 0.659}   # dark blue
_C_PAYOUTS_HEADER  = {"red": 0.106, "green": 0.471, "blue": 0.216}   # dark green
_C_BALANCES_HEADER = {"red": 0.600, "green": 0.290, "blue": 0.000}   # dark orange
_C_STATS_HEADER    = {"red": 0.357, "green": 0.149, "blue": 0.612}   # dark purple
_C_WHITE           = {"red": 1.0, "green": 1.0, "blue": 1.0}
_C_GREEN_ROW       = {"red": 0.851, "green": 0.918, "blue": 0.827}
_C_RED_ROW         = {"red": 0.957, "green": 0.800, "blue": 0.800}
_C_GREY_ROW        = {"red": 0.878, "green": 0.878, "blue": 0.878}
_C_YELLOW_ROW      = {"red": 1.000, "green": 0.961, "blue": 0.800}
_C_ORANGE_ROW      = {"red": 0.988, "green": 0.902, "blue": 0.800}
_C_TOTAL_ROW       = {"red": 0.851, "green": 0.918, "blue": 0.827}


def _rgb(c: dict) -> dict:
    return {"red": c["red"], "green": c["green"], "blue": c["blue"]}


class SheetsClient:

    def __init__(self) -> None:
        self._spreadsheet_id = os.getenv("SHEETS_SPREADSHEET_ID", "").strip()
        self._json_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_PATH", "").strip()
        self._lesson_rate = int(os.getenv("LESSON_RATE", "1000"))
        self._gc = None

    def is_configured(self) -> bool:
        return bool(self._spreadsheet_id and self._json_path and os.path.isfile(self._json_path))

    # ── Auth ───────────────────────────────────────────────────────────────────
    def _get_client(self):
        if self._gc is not None:
            return self._gc
        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError:
            logger.error("gspread / google-auth not installed")
            return None
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(self._json_path, scopes=scopes)
        self._gc = gspread.authorize(creds)
        return self._gc

    def _open_spreadsheet(self):
        gc = self._get_client()
        return gc.open_by_key(self._spreadsheet_id) if gc else None

    # ── Low-level helpers ──────────────────────────────────────────────────────
    def _get_or_add_worksheet(self, spreadsheet, title: str, rows: int = 5000, cols: int = 10):
        try:
            return spreadsheet.worksheet(title)
        except Exception:
            return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)

    def _clear_data_rows(self, ws, cols: int = 26) -> None:
        col_letter = chr(ord("A") + cols - 1)
        try:
            ws.batch_clear([f"A2:{col_letter}5000"])
        except Exception:
            pass

    def _batch_format(self, spreadsheet, requests: list) -> None:
        try:
            spreadsheet.batch_update({"requests": requests})
        except Exception as exc:
            logger.warning("Formatting failed: %s", exc)

    def _header_format_request(self, sheet_id: int, num_cols: int, color: dict) -> dict:
        return {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0, "endRowIndex": 1,
                    "startColumnIndex": 0, "endColumnIndex": num_cols,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": _rgb(color),
                        "textFormat": {"foregroundColor": _C_WHITE, "bold": True, "fontSize": 10},
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
            }
        }

    def _col_widths_request(self, sheet_id: int, widths: list[int]) -> list[dict]:
        return [
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": i, "endIndex": i + 1,
                    },
                    "properties": {"pixelSize": w},
                    "fields": "pixelSize",
                }
            }
            for i, w in enumerate(widths)
        ]

    def _freeze_request(self, sheet_id: int, rows: int = 1) -> dict:
        return {
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": rows}},
                "fields": "gridProperties.frozenRowCount",
            }
        }

    def _cond_formula_request(self, sheet_id: int, formula: str, color: dict,
                               start_row: int = 1, num_cols: int = 10, index: int = 0) -> dict:
        return {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": sheet_id,
                        "startRowIndex": start_row,
                        "startColumnIndex": 0,
                        "endColumnIndex": num_cols,
                    }],
                    "booleanRule": {
                        "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": formula}]},
                        "format": {"backgroundColor": _rgb(color)},
                    },
                },
                "index": index,
            }
        }

    def _row_format_request(self, sheet_id: int, start_row: int, end_row: int,
                             color: dict, bold: bool = False, num_cols: int = 4) -> dict:
        fmt: dict = {"backgroundColor": _rgb(color)}
        if bold:
            fmt["textFormat"] = {"bold": True}
        return {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": start_row, "endRowIndex": end_row,
                    "startColumnIndex": 0, "endColumnIndex": num_cols,
                },
                "cell": {"userEnteredFormat": fmt},
                "fields": "userEnteredFormat(backgroundColor" + (",textFormat" if bold else "") + ")",
            }
        }

    # ── Migration: rename «Посещения» → «Журнал» ──────────────────────────────
    def _migrate_sheet_names(self, spreadsheet) -> None:
        for ws in spreadsheet.worksheets():
            if ws.title == "Посещения":
                try:
                    spreadsheet.batch_update({"requests": [{
                        "updateSheetProperties": {
                            "properties": {"sheetId": ws.id, "title": _JOURNAL_SHEET},
                            "fields": "title",
                        }
                    }]})
                    logger.info("Renamed sheet «Посещения» → «Журнал»")
                except Exception as exc:
                    logger.warning("Could not rename sheet: %s", exc)

    # ── «Журнал» ──────────────────────────────────────────────────────────────
    def _get_or_create_journal(self, spreadsheet):
        self._migrate_sheet_names(spreadsheet)
        try:
            ws = spreadsheet.worksheet(_JOURNAL_SHEET)
        except Exception:
            ws = spreadsheet.add_worksheet(title=_JOURNAL_SHEET, rows=5000, cols=len(_JOURNAL_HEADER))
            ws.append_row(_JOURNAL_HEADER, value_input_option="USER_ENTERED")
            ws.freeze(rows=1)
            self._format_journal(ws, spreadsheet)
        return ws

    def _format_journal(self, ws, spreadsheet) -> None:
        sheet_id = ws.id
        col_widths = [150, 180, 180, 160, 110, 90, 100, 100, 160, 80]
        requests = [
            self._header_format_request(sheet_id, len(_JOURNAL_HEADER), _C_JOURNAL_HEADER),
            self._freeze_request(sheet_id),
            {"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
                "properties": {"pixelSize": 32}, "fields": "pixelSize",
            }},
            self._cond_formula_request(sheet_id, '=$F2="Был"',      _C_GREEN_ROW, index=0, num_cols=len(_JOURNAL_HEADER)),
            self._cond_formula_request(sheet_id, '=$F2="Не был"',   _C_RED_ROW,   index=1, num_cols=len(_JOURNAL_HEADER)),
            self._cond_formula_request(sheet_id, '=$F2="Отменено"', _C_GREY_ROW,  index=2, num_cols=len(_JOURNAL_HEADER)),
        ] + self._col_widths_request(sheet_id, col_widths)
        self._batch_format(spreadsheet, requests)

    def apply_formatting(self, ws, spreadsheet) -> None:
        self._format_journal(ws, spreadsheet)

    def _find_journal_row_by_id(self, ws, attendance_id: int) -> bool:
        try:
            col_j = ws.col_values(10)
            return str(attendance_id) in [str(v).strip() for v in col_j]
        except Exception:
            return False

    def append_attendance(self, row: dict[str, Any]) -> bool:
        if not self.is_configured():
            return False
        gc = self._get_client()
        if gc is None:
            return False
        try:
            sp = gc.open_by_key(self._spreadsheet_id)
            ws = self._get_or_create_journal(sp)
            aid = row.get("attendance_id")
            if aid and self._find_journal_row_by_id(ws, aid):
                return True
            status = _STATUS_LABELS.get(row.get("status", ""), row.get("status", ""))
            tariff = _TARIFF_LABELS.get(row.get("tariff_type", ""), row.get("tariff_type", ""))
            ws.append_row([
                row.get("lesson_datetime", ""),
                row.get("teacher_name", ""),
                row.get("student_name", ""),
                row.get("subject_name", ""),
                tariff, status,
                row.get("balance_before", "—"),
                row.get("balance_after", "—"),
                row.get("marked_by_name", ""),
                str(aid) if aid else "",
            ], value_input_option="USER_ENTERED")
            return True
        except Exception as exc:
            logger.error("Journal append failed: %s", exc)
            return False

    def batch_append_rows(self, rows: list[dict[str, Any]]) -> tuple[int, int]:
        if not self.is_configured() or not rows:
            return 0, 0
        gc = self._get_client()
        if gc is None:
            return 0, 0
        sp = gc.open_by_key(self._spreadsheet_id)
        ws = self._get_or_create_journal(sp)
        try:
            existing = set(str(v).strip() for v in ws.col_values(10) if v)
        except Exception:
            existing = set()
        to_add, skipped = [], 0
        for row in rows:
            aid = str(row.get("attendance_id", ""))
            if aid and aid in existing:
                skipped += 1
                continue
            status = _STATUS_LABELS.get(row.get("status", ""), row.get("status", ""))
            tariff = _TARIFF_LABELS.get(row.get("tariff_type", ""), row.get("tariff_type", ""))
            to_add.append([
                row.get("lesson_datetime", ""), row.get("teacher_name", ""),
                row.get("student_name", ""), row.get("subject_name", ""),
                tariff, status,
                row.get("balance_before", "—"), row.get("balance_after", "—"),
                row.get("marked_by_name", ""), aid,
            ])
        for i in range(0, len(to_add), 500):
            ws.append_rows(to_add[i:i + 500], value_input_option="USER_ENTERED")
        return len(to_add), skipped

    # ── «Выплаты» ─────────────────────────────────────────────────────────────
    def update_payouts_sheet(self, payouts: list[dict]) -> bool:
        """Rewrite the «Выплаты» sheet from weekly payout data."""
        if not self.is_configured():
            return False
        gc = self._get_client()
        if gc is None:
            return False
        try:
            sp = gc.open_by_key(self._spreadsheet_id)
            ws = self._get_or_add_worksheet(sp, _PAYOUTS_SHEET, cols=4)
            self._clear_data_rows(ws, cols=4)

            rows_data: list[list] = []
            # Track which row ranges are "current week" or "total" for formatting
            format_ranges: list[dict] = []  # {type, start, end}

            for week_idx, week in enumerate(payouts):
                w_start = week["week_start"]
                w_end   = week["week_end"]
                try:
                    from datetime import date as _date
                    period_label = (
                        f"{_date.fromisoformat(w_start).strftime('%d.%m')}"
                        f" – {_date.fromisoformat(w_end).strftime('%d.%m.%y')}"
                    )
                except Exception:
                    period_label = f"{w_start} – {w_end}"

                block_start = len(rows_data) + 2  # 1-based, +1 for header

                for t in week["teachers"]:
                    amt = f"{t['lessons'] * self._lesson_rate:,}".replace(",", " ") + " ₽"
                    rows_data.append([period_label, t["name"], t["lessons"], amt])

                total_amt = f"{week['total_lessons'] * self._lesson_rate:,}".replace(",", " ") + " ₽"
                rows_data.append(["", "ИТОГО", week["total_lessons"], total_amt])
                rows_data.append(["", "", "", ""])  # separator

                block_end = len(rows_data) + 1  # 1-based
                total_row = block_end - 2        # 1-based (the ИТОГО row)

                tag = "current" if week_idx == 0 else "archive"
                format_ranges.append({"type": tag, "start": block_start, "end": block_end})
                format_ranges.append({"type": "total", "start": total_row, "end": total_row + 1})

            if not rows_data:
                rows_data.append(["Нет данных", "", "", ""])

            # Write header + data
            ws.update("A1", [_PAYOUTS_HEADER] + rows_data, value_input_option="USER_ENTERED")

            # Formatting
            sheet_id = ws.id
            requests = [
                self._header_format_request(sheet_id, 4, _C_PAYOUTS_HEADER),
                self._freeze_request(sheet_id),
            ] + self._col_widths_request(sheet_id, [160, 200, 90, 130])

            for fr in format_ranges:
                start_idx = fr["start"] - 1
                end_idx   = fr["end"] - 1
                if fr["type"] == "current":
                    requests.append(self._row_format_request(sheet_id, start_idx, end_idx, _C_YELLOW_ROW, num_cols=4))
                elif fr["type"] == "total":
                    requests.append(self._row_format_request(sheet_id, start_idx, end_idx, _C_TOTAL_ROW, bold=True, num_cols=4))

            self._batch_format(sp, requests)
            logger.info("Payouts sheet updated: %d weeks", len(payouts))
            return True
        except Exception as exc:
            logger.error("update_payouts_sheet failed: %s", exc)
            return False

    # ── «Балансы» ─────────────────────────────────────────────────────────────
    def update_balances_sheet(self, balances: list[dict]) -> bool:
        """Rewrite the «Балансы» sheet from student balance data."""
        if not self.is_configured():
            return False
        gc = self._get_client()
        if gc is None:
            return False
        try:
            sp = gc.open_by_key(self._spreadsheet_id)
            ws = self._get_or_add_worksheet(sp, _BALANCES_SHEET, cols=5)
            self._clear_data_rows(ws, cols=5)

            rows_data: list[list] = []
            for b in balances:
                bal = b["lesson_balance"]
                if bal > 2:
                    status = "✅ Ок"
                elif bal > 0:
                    status = "⚠️ Мало"
                elif bal == 0:
                    status = "🔴 Нет занятий"
                else:
                    status = f"🔴 Долг ({abs(bal)})"
                rows_data.append([
                    b["student_name"], b["subject_name"],
                    b["teacher_name"], bal, status,
                ])

            if not rows_data:
                rows_data.append(["Нет данных", "", "", "", ""])

            ws.update("A1", [_BALANCES_HEADER] + rows_data, value_input_option="USER_ENTERED")

            sheet_id = ws.id
            n = len(_BALANCES_HEADER)
            requests = [
                self._header_format_request(sheet_id, n, _C_BALANCES_HEADER),
                self._freeze_request(sheet_id),
                # balance > 2 → green
                self._cond_formula_request(sheet_id, "=$D2>2",              _C_GREEN_ROW,  index=0, num_cols=n),
                # balance 1–2 → orange/yellow
                self._cond_formula_request(sheet_id, "=($D2>=1)*($D2<=2)",  _C_ORANGE_ROW, index=1, num_cols=n),
                # balance <= 0 → red
                self._cond_formula_request(sheet_id, "=$D2<=0",             _C_RED_ROW,    index=2, num_cols=n),
            ] + self._col_widths_request(sheet_id, [190, 170, 190, 80, 140])
            self._batch_format(sp, requests)
            logger.info("Balances sheet updated: %d rows", len(balances))
            return True
        except Exception as exc:
            logger.error("update_balances_sheet failed: %s", exc)
            return False

    # ── «Статистика» ──────────────────────────────────────────────────────────
    def update_stats_sheet(self, stats: dict) -> bool:
        """Rewrite the «Статистика» sheet from stats data."""
        if not self.is_configured():
            return False
        gc = self._get_client()
        if gc is None:
            return False
        try:
            sp = gc.open_by_key(self._spreadsheet_id)
            ws = self._get_or_add_worksheet(sp, _STATS_SHEET, cols=5)
            self._clear_data_rows(ws, cols=5)

            periods = [
                ["Сегодня",       stats.get("today", 0)],
                ["Эта неделя",    stats.get("week", 0)],
                ["Этот месяц",    stats.get("month", 0)],
                ["Всего",         stats.get("total", 0)],
            ]
            weekdays = [[name, cnt] for name, cnt in stats.get("by_weekday", [])]

            # Build a combined grid: left=periods, right=weekdays, same rows
            rows_data: list[list] = []
            max_rows = max(len(periods), len(weekdays))
            for i in range(max_rows):
                left  = periods[i]  if i < len(periods)  else ["", ""]
                right = weekdays[i] if i < len(weekdays) else ["", ""]
                rows_data.append([left[0], left[1], "", right[0], right[1]])

            header_row = ["Показатель", "Занятий", "", "День недели", "Занятий"]
            updated_row = [f"Обновлено: {stats.get('updated_at', '')}", "", "", "", ""]
            ws.update("A1", [header_row, updated_row] + rows_data, value_input_option="USER_ENTERED")

            sheet_id = ws.id
            requests = [
                self._header_format_request(sheet_id, 5, _C_STATS_HEADER),
                self._freeze_request(sheet_id),
                # "Всего" row bold
                self._row_format_request(sheet_id, 4, 5, {"red": 0.937, "green": 0.937, "blue": 0.937}, bold=True, num_cols=2),
            ] + self._col_widths_request(sheet_id, [180, 90, 30, 180, 90])
            self._batch_format(sp, requests)
            logger.info("Stats sheet updated")
            return True
        except Exception as exc:
            logger.error("update_stats_sheet failed: %s", exc)
            return False

    def mark_cancelled(self, attendance_id: int, cancelled_by: str, lesson_datetime: str) -> bool:
        if not self.is_configured():
            return False
        gc = self._get_client()
        if gc is None:
            return False
        try:
            sp = gc.open_by_key(self._spreadsheet_id)
            ws = self._get_or_create_journal(sp)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ws.append_row([now, "", "", "", "", "↩️ Отменено", "", "", cancelled_by, str(attendance_id)],
                          value_input_option="USER_ENTERED")
            return True
        except Exception as exc:
            logger.error("Sheets cancel failed: %s", exc)
            return False


_client: SheetsClient | None = None


def get_sheets_client() -> SheetsClient:
    global _client
    if _client is None:
        _client = SheetsClient()
    return _client
