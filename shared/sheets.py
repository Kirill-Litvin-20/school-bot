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

_JOURNAL_HEADER  = ["Дата", "Время", "День", "Препод", "Ученик", "Направление",
                    "Тариф", "Статус", "Баланс до", "Баланс после",
                    "Кто отметил", "ID отметки"]
_WEEKDAYS_SHORT  = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
_PAYOUTS_HEADER  = ["Период", "Преподаватель", "Занятий", "Сумма", "Реквизиты"]
_BALANCES_HEADER = ["Ученик", "Направление", "Препод", "Баланс", "Статус"]
_STATS_HEADER    = ["Показатель", "Занятий", "", "День недели", "Занятий"]  # 5-col split view
_REVENUE_SHEET  = "Выручка"
_REVENUE_HEADER = [
    "Дата / Период", "Время / Даты", "Ученик", "Препод",
    "Тип оплаты", "Занятий", "Сумма (₽)", "Преподу (₽)", "Хозяину (₽)", "Кто добавил",
]
_N_REV = 10  # column count for the revenue sheet
_C_REVENUE_HEADER = {"red": 0.027, "green": 0.408, "blue": 0.392}  # dark teal

_TOPUPS_SHEET  = "Пополнения"
_TOPUPS_HEADER = ["Дата", "Время", "День", "Ученик", "Направление", "Препод",
                  "+Занятий", "Сумма (₽)", "Тип", "Кто добавил", "Комментарий"]
_C_TOPUPS_HEADER = {"red": 0.180, "green": 0.216, "blue": 0.451}   # dark indigo

_DISCOUNTS_SHEET  = "Промокоды"
_DISCOUNTS_HEADER = ["Код", "Скидка", "Применяется к", "Использований",
                     "Действует до", "Назначен ученикам", "Статус", "Создан"]
_C_DISCOUNTS_HEADER = {"red": 0.286, "green": 0.149, "blue": 0.557}  # purple

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


def _now_msk_str() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return datetime.now().strftime("%d.%m.%Y %H:%M")


def _split_dt(dt_str: str) -> tuple[str, str, str]:
    """Split 'YYYY-MM-DD HH:MM:SS' → ('dd.mm.yyyy', 'HH:MM', 'Пн'/'Вт'/…)."""
    try:
        dt = datetime.fromisoformat(str(dt_str)[:19])
        return dt.strftime("%d.%m.%Y"), dt.strftime("%H:%M"), _WEEKDAYS_SHORT[dt.weekday()]
    except Exception:
        return str(dt_str), "", ""


class SheetsClient:

    def __init__(self) -> None:
        self._spreadsheet_id = os.getenv("SHEETS_SPREADSHEET_ID", "").strip()
        self._json_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_PATH", "").strip()
        self._lesson_rate = int(os.getenv("LESSON_RATE", "1000"))
        self._lesson_price = int(os.getenv("LESSON_PRICE", "1500"))
        self._owner_cut = self._lesson_price - self._lesson_rate  # default 500
        self._gc = None
        self._migration_done = False

    def is_configured(self) -> bool:
        return bool(self._spreadsheet_id and self._json_path and os.path.isfile(self._json_path))

    # ── Auth ───────────────────────────────────────────────────────────────────
    def _build_client(self):
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
        return gspread.authorize(creds)

    def _get_client(self, _retry: bool = True):
        if self._gc is None:
            self._gc = self._build_client()
        return self._gc

    def _reset_client(self) -> None:
        """Сбросить кешированный клиент — следующий вызов пересоздаст авторизацию."""
        self._gc = None

    def _open_spreadsheet(self):
        gc = self._get_client()
        if gc is None:
            return None
        try:
            return gc.open_by_key(self._spreadsheet_id)
        except Exception as exc:
            # Re-auth once on token expiry / transport errors
            err = str(exc).lower()
            if any(k in err for k in ("invalid_grant", "token", "401", "403", "unauthorized")):
                logger.warning("Sheets: auth error, re-authenticating: %s", exc)
                self._reset_client()
                gc2 = self._get_client()
                if gc2 is None:
                    return None
                return gc2.open_by_key(self._spreadsheet_id)
            raise

    # ── Low-level helpers ──────────────────────────────────────────────────────
    def _get_or_add_worksheet(self, spreadsheet, title: str, rows: int = 5000, cols: int = 10):
        try:
            return spreadsheet.worksheet(title)
        except Exception:
            return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)

    def _clear_data_rows(self, ws, cols: int = 26) -> None:
        def _col_letter(n: int) -> str:
            """Convert 1-based column number to A1-notation letter(s)."""
            result = ""
            while n > 0:
                n, rem = divmod(n - 1, 26)
                result = chr(65 + rem) + result
            return result

        try:
            ws.batch_clear([f"A2:{_col_letter(cols)}5000"])
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
        _dark = {"red": 0.1, "green": 0.1, "blue": 0.1}
        fmt: dict = {
            "backgroundColor": _rgb(color),
            "textFormat": {"bold": bold, "foregroundColor": _rgb(_dark)},
        }
        return {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": start_row, "endRowIndex": end_row,
                    "startColumnIndex": 0, "endColumnIndex": num_cols,
                },
                "cell": {"userEnteredFormat": fmt},
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }
        }

    # ── Migration: rename «Посещения» → «Журнал» ──────────────────────────────
    def _migrate_sheet_names(self, spreadsheet) -> None:
        if self._migration_done:
            return
        migrated = False
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
                    migrated = True
                except Exception as exc:
                    logger.warning("Could not rename sheet: %s", exc)
        self._migration_done = True  # Don't check again whether migration was needed or not

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
        n = len(_JOURNAL_HEADER)  # 12
        # A=Дата B=Время C=День D=Препод E=Ученик F=Направление G=Тариф H=Статус I=Бал.до J=Бал.после K=Кто L=ID
        col_widths = [90, 60, 40, 150, 150, 150, 85, 85, 80, 80, 140, 70]
        requests = [
            # Ensure sheet has enough columns before setting widths
            {"updateSheetProperties": {
                "properties": {"sheetId": sheet_id,
                               "gridProperties": {"columnCount": n}},
                "fields": "gridProperties.columnCount",
            }},
            self._header_format_request(sheet_id, n, _C_JOURNAL_HEADER),
            self._freeze_request(sheet_id),
            {"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
                "properties": {"pixelSize": 32}, "fields": "pixelSize",
            }},
            self._cond_formula_request(sheet_id, '=$H2="Был"',      _C_GREEN_ROW, index=0, num_cols=n),
            self._cond_formula_request(sheet_id, '=$H2="Не был"',   _C_RED_ROW,   index=1, num_cols=n),
            self._cond_formula_request(sheet_id, '=$H2="Отменено"', _C_GREY_ROW,  index=2, num_cols=n),
        ] + self._col_widths_request(sheet_id, col_widths)
        self._batch_format(spreadsheet, requests)

    def apply_formatting(self, ws, spreadsheet) -> None:
        self._format_journal(ws, spreadsheet)

    def _apply_week_borders(self, ws, spreadsheet) -> None:
        """Draw thick top borders at each week transition (journal is newest-first)."""
        try:
            dates = ws.col_values(1)[1:]  # col A, skip header
            if len(dates) < 2:
                return
            sheet_id = ws.id
            requests = []
            prev_week: tuple | None = None
            for i, date_str in enumerate(dates):
                try:
                    dt = datetime.strptime(date_str.strip(), "%d.%m.%Y")
                    wk = (dt.year, dt.isocalendar()[1])
                except Exception:
                    wk = None
                if prev_week is not None and wk is not None and wk != prev_week:
                    row_idx = i + 1  # 0-based (row 0=header, row 1=first data)
                    requests.append({
                        "updateBorders": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                                "startColumnIndex": 0, "endColumnIndex": len(_JOURNAL_HEADER),
                            },
                            "top": {"style": "SOLID_MEDIUM",
                                    "color": {"red": 0.4, "green": 0.4, "blue": 0.4}},
                        }
                    })
                prev_week = wk
            if requests:
                self._batch_format(spreadsheet, requests)
        except Exception as exc:
            logger.warning("Week borders failed: %s", exc)

    def _find_journal_row_by_id(self, ws, attendance_id: int) -> bool:
        try:
            # ID is in col L (12) in new schema; also check col J (10) for migrated data
            ids_l = set(str(v).strip() for v in ws.col_values(12) if v)
            ids_j = set(str(v).strip() for v in ws.col_values(10) if v)
            return str(attendance_id) in ids_l or str(attendance_id) in ids_j
        except Exception:
            return False

    def _make_journal_row(self, row: dict[str, Any]) -> list:
        date_s, time_s, day_s = _split_dt(row.get("lesson_datetime", ""))
        status = _STATUS_LABELS.get(row.get("status", ""), row.get("status", ""))
        tariff = _TARIFF_LABELS.get(row.get("tariff_type", ""), row.get("tariff_type", ""))
        aid = row.get("attendance_id", "")
        return [
            date_s, time_s, day_s,
            row.get("teacher_name", ""), row.get("student_name", ""),
            row.get("subject_name", ""),
            tariff, status,
            row.get("balance_before", "—"), row.get("balance_after", "—"),
            row.get("marked_by_name", ""), str(aid) if aid else "",
        ]

    def append_attendance(self, row: dict[str, Any]) -> bool:
        if not self.is_configured():
            return False
        try:
            sp = self._open_spreadsheet()
            if sp is None:
                return False
            ws = self._get_or_create_journal(sp)
            aid = row.get("attendance_id")
            if aid and self._find_journal_row_by_id(ws, aid):
                return True
            ws.insert_rows([self._make_journal_row(row)], row=2,
                           value_input_option="USER_ENTERED")
            logger.info("Sheets: wrote attendance_id=%s", aid)
            return True
        except Exception as exc:
            logger.error("Journal append failed: %s", exc)
            return False

    def batch_append_rows(self, rows: list[dict[str, Any]],
                          rebuild: bool = False) -> tuple[int, int]:
        if not self.is_configured() or not rows:
            return 0, 0
        sp = self._open_spreadsheet()
        if sp is None:
            return 0, 0
        ws = self._get_or_create_journal(sp)

        if rebuild:
            # Clear all data rows and rewrite everything newest-first
            self._clear_data_rows(ws, cols=len(_JOURNAL_HEADER))
            sorted_rows = sorted(rows,
                                 key=lambda r: r.get("lesson_datetime", ""),
                                 reverse=True)
            to_add = [self._make_journal_row(r) for r in sorted_rows]
            for i in range(0, len(to_add), 500):
                ws.append_rows(to_add[i:i + 500], value_input_option="USER_ENTERED")
            self._apply_week_borders(ws, sp)
            return len(to_add), 0

        # Incremental: check both col 12 (new) and col 10 (migrated)
        try:
            existing = (set(str(v).strip() for v in ws.col_values(12) if v) |
                        set(str(v).strip() for v in ws.col_values(10) if v))
        except Exception:
            existing = set()
        to_add, skipped = [], 0
        for row in rows:
            aid = str(row.get("attendance_id", ""))
            if aid and aid in existing:
                skipped += 1
                continue
            to_add.append(self._make_journal_row(row))
        for i in range(0, len(to_add), 500):
            ws.append_rows(to_add[i:i + 500], value_input_option="USER_ENTERED")
        return len(to_add), skipped

    # ── «Выплаты» ─────────────────────────────────────────────────────────────
    def update_payouts_sheet(self, payouts: list[dict]) -> bool:
        """Rewrite the «Выплаты» sheet from weekly payout data."""
        if not self.is_configured():
            return False
        try:
            from datetime import date as _date
            sp = self._open_spreadsheet()
            if sp is None:
                return False
            ws = self._get_or_add_worksheet(sp, _PAYOUTS_SHEET, cols=5)
            self._clear_data_rows(ws, cols=5)

            rows_data: list[list] = []
            format_ranges: list[dict] = []

            # "Обновлено" row — prepended so all format_ranges indices shift naturally
            rows_data.append([f"Обновлено: {_now_msk_str()}", "", "", "", ""])
            format_ranges.append({"type": "updated_row", "start": 2, "end": 3})

            for week_idx, week in enumerate(payouts):
                w_start = week["week_start"]
                w_end   = week["week_end"]
                try:
                    period_label = (
                        f"{_date.fromisoformat(w_start).strftime('%d.%m')}"
                        f" – {_date.fromisoformat(w_end).strftime('%d.%m.%y')}"
                    )
                except Exception:
                    period_label = f"{w_start} – {w_end}"

                total_amt = f"{week['total_lessons'] * self._lesson_rate:,}".replace(",", " ") + " ₽"
                n_teachers = len(week["teachers"])
                teacher_word = "препод" if n_teachers == 1 else "препода" if n_teachers in (2, 3, 4) else "преподов"

                # ── Week block header row ──────────────────────────────────────
                week_header_row = len(rows_data) + 2  # 1-based
                rows_data.append([
                    period_label,
                    f"{n_teachers} {teacher_word}",
                    week["total_lessons"],
                    total_amt,
                    "",
                ])
                format_ranges.append({
                    "type": "week_header",
                    "start": week_header_row,
                    "end": week_header_row + 1,
                    "current": week_idx == 0,
                })

                # ── Teacher rows ───────────────────────────────────────────────
                teacher_start = len(rows_data) + 2  # 1-based
                for t in week["teachers"]:
                    amt = f"{t['lessons'] * self._lesson_rate:,}".replace(",", " ") + " ₽"
                    rows_data.append(["", t["name"], t["lessons"], amt, t.get("payment_details", "")])
                teacher_end = len(rows_data) + 1  # 1-based (exclusive)

                if week_idx == 0 and week["teachers"]:
                    format_ranges.append({"type": "current_teachers",
                                          "start": teacher_start, "end": teacher_end})

                # ── Empty separator ────────────────────────────────────────────
                rows_data.append(["", "", "", "", ""])

            if not rows_data:
                rows_data.append(["Нет данных", "", "", "", ""])

            ws.update("A1", [_PAYOUTS_HEADER] + rows_data, value_input_option="USER_ENTERED")

            sheet_id = ws.id
            _C_HEADER_CURRENT = _C_PAYOUTS_HEADER                              # dark green
            _C_HEADER_ARCHIVE = {"red": 0.42, "green": 0.45, "blue": 0.47}    # dark slate
            _C_TEACHER_ROW    = {"red": 0.95, "green": 0.98, "blue": 0.95}    # very light green

            requests = [
                self._header_format_request(sheet_id, 5, _C_PAYOUTS_HEADER),
                self._freeze_request(sheet_id),
            ] + self._col_widths_request(sheet_id, [145, 210, 80, 130, 210])

            for fr in format_ranges:
                s = fr["start"] - 1  # 0-based
                e = fr["end"] - 1

                if fr["type"] == "week_header":
                    color = _C_HEADER_CURRENT if fr["current"] else _C_HEADER_ARCHIVE
                    # Dark bg + white bold text
                    requests.append({
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": s, "endRowIndex": e,
                                "startColumnIndex": 0, "endColumnIndex": 5,
                            },
                            "cell": {"userEnteredFormat": {
                                "backgroundColor": _rgb(color),
                                "textFormat": {
                                    "foregroundColor": _C_WHITE,
                                    "bold": True,
                                    "fontSize": 10,
                                },
                                "verticalAlignment": "MIDDLE",
                            }},
                            "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)",
                        }
                    })
                    # Row height for week headers
                    requests.append({"updateDimensionProperties": {
                        "range": {"sheetId": sheet_id, "dimension": "ROWS",
                                  "startIndex": s, "endIndex": e},
                        "properties": {"pixelSize": 28}, "fields": "pixelSize",
                    }})

                elif fr["type"] == "current_teachers":
                    requests.append(self._row_format_request(sheet_id, s, e, _C_YELLOW_ROW, num_cols=5))

                elif fr["type"] == "updated_row":
                    _C_UPDATED = {"red": 0.949, "green": 0.949, "blue": 0.949}
                    requests.append({"repeatCell": {
                        "range": {"sheetId": sheet_id,
                                   "startRowIndex": s, "endRowIndex": e,
                                   "startColumnIndex": 0, "endColumnIndex": 5},
                        "cell": {"userEnteredFormat": {
                            "backgroundColor": _rgb(_C_UPDATED),
                            "textFormat": {"italic": True,
                                           "foregroundColor": {"red": 0.5, "green": 0.5, "blue": 0.5},
                                           "fontSize": 9},
                        }},
                        "fields": "userEnteredFormat(backgroundColor,textFormat)",
                    }})

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
        try:
            sp = self._open_spreadsheet()
            if sp is None:
                return False
            ws = self._get_or_add_worksheet(sp, _BALANCES_SHEET, cols=5)
            self._clear_data_rows(ws, cols=5)

            def _balance_sort_key(b: dict) -> tuple:
                bal = b["lesson_balance"]
                # 0=долг, 1=нет занятий, 2=мало, 3=ок — критичные наверху
                if bal < 0:
                    return (0, bal)
                if bal == 0:
                    return (1, 0)
                if bal <= 2:
                    return (2, bal)
                return (3, bal)

            rows_data: list[list] = []
            for b in sorted(balances, key=_balance_sort_key):
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

            updated_row = [f"Обновлено: {_now_msk_str()}", "", "", "", ""]
            ws.update("A1", [_BALANCES_HEADER, updated_row] + rows_data, value_input_option="USER_ENTERED")

            sheet_id = ws.id
            n = len(_BALANCES_HEADER)
            _C_UPDATED_ROW = {"red": 0.949, "green": 0.949, "blue": 0.949}
            requests = [
                self._header_format_request(sheet_id, n, _C_BALANCES_HEADER),
                self._freeze_request(sheet_id, rows=2),
                # "Обновлено" row (index 1) — subtle grey, italic
                {"repeatCell": {
                    "range": {"sheetId": sheet_id,
                               "startRowIndex": 1, "endRowIndex": 2,
                               "startColumnIndex": 0, "endColumnIndex": n},
                    "cell": {"userEnteredFormat": {
                        "backgroundColor": _rgb(_C_UPDATED_ROW),
                        "textFormat": {"italic": True,
                                       "foregroundColor": {"red": 0.5, "green": 0.5, "blue": 0.5},
                                       "fontSize": 9},
                    }},
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }},
                # Data starts at row 3 (index 2), formulas reference $D3
                self._cond_formula_request(sheet_id, "=$D3>2",              _C_GREEN_ROW,  index=0, num_cols=n, start_row=2),
                self._cond_formula_request(sheet_id, "=($D3>=1)*($D3<=2)",  _C_ORANGE_ROW, index=1, num_cols=n, start_row=2),
                self._cond_formula_request(sheet_id, "=$D3<=0",             _C_RED_ROW,    index=2, num_cols=n, start_row=2),
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
        try:
            sp = self._open_spreadsheet()
            if sp is None:
                return False
            ws = self._get_or_add_worksheet(sp, _STATS_SHEET, cols=5)
            ws.clear()

            _C_GREY_LIGHT = {"red": 0.937, "green": 0.937, "blue": 0.937}
            _C_SECTION    = {"red": 0.851, "green": 0.886, "blue": 0.953}  # light blue section header

            # ── Left column: periods + weekly history + monthly history ────────
            periods_block = [
                ["Сегодня",         stats.get("today", 0)],
                ["Эта неделя",      stats.get("week", 0)],
                ["Прошлая неделя",  stats.get("last_week", 0)],
                ["Этот месяц",      stats.get("month", 0)],
                ["Прошлый месяц",   stats.get("last_month", 0)],
                ["Всего",           stats.get("total", 0)],
            ]

            weekly  = stats.get("weekly_history", [])
            monthly = stats.get("monthly_history", [])

            # Build rows: header row 1, then data.
            # Layout: col A=Показатель, B=Занятий, C=gap, D=День недели, E=Занятий
            weekdays = [[name, cnt] for name, cnt in stats.get("by_weekday", [])]

            all_rows: list[list] = []
            format_requests_meta: list[dict] = []  # {row_idx, type}

            def _row(a="", b="", d="", e="") -> list:
                return [a, b, "", d, e]

            # Updated timestamp
            all_rows.append(_row(f"Обновлено: {stats.get('updated_at', '')}"))
            # blank
            all_rows.append(_row())

            # ── Period block ───────────────────────────────────────────────────
            section_row = len(all_rows) + 2  # 1-based (header=1, so +1 for header offset)
            all_rows.append(_row("▸ Периоды", "Занятий"))
            format_requests_meta.append({"row": section_row, "type": "section_header"})

            for i, (label, cnt) in enumerate(periods_block):
                r = len(all_rows) + 2
                wd_label, wd_cnt = (weekdays[i][0], weekdays[i][1]) if i < len(weekdays) else ("", "")
                all_rows.append(_row(label, cnt, wd_label, wd_cnt))
                if label == "Всего":
                    format_requests_meta.append({"row": r, "type": "total"})

            # Remaining weekdays
            for i in range(len(periods_block), len(weekdays)):
                all_rows.append(_row("", "", weekdays[i][0], weekdays[i][1]))

            all_rows.append(_row())  # blank separator

            # ── Weekly history block ───────────────────────────────────────────
            if weekly:
                section_row = len(all_rows) + 2
                all_rows.append(_row("▸ По неделям", "Занятий"))
                format_requests_meta.append({"row": section_row, "type": "section_header"})
                for w in weekly:
                    all_rows.append(_row(w["label"], w["count"]))
                all_rows.append(_row())

            # ── Monthly history block ──────────────────────────────────────────
            if monthly:
                section_row = len(all_rows) + 2
                all_rows.append(_row("▸ По месяцам", "Занятий"))
                format_requests_meta.append({"row": section_row, "type": "section_header"})
                for m in monthly:
                    all_rows.append(_row(m["label"], m["count"]))

            header_row = ["Показатель", "Занятий", "", "День недели", "Занятий"]
            ws.update("A1", [header_row] + all_rows, value_input_option="USER_ENTERED")

            sheet_id = ws.id
            requests = [
                self._header_format_request(sheet_id, 5, _C_STATS_HEADER),
                self._freeze_request(sheet_id),
            ] + self._col_widths_request(sheet_id, [190, 90, 30, 190, 90])

            for meta in format_requests_meta:
                row_0 = meta["row"] - 1  # convert to 0-based
                if meta["type"] == "section_header":
                    requests.append(self._row_format_request(sheet_id, row_0, row_0 + 1,
                                                             _C_SECTION, bold=True, num_cols=2))
                elif meta["type"] == "total":
                    requests.append(self._row_format_request(sheet_id, row_0, row_0 + 1,
                                                             _C_GREY_LIGHT, bold=True, num_cols=2))

            self._batch_format(sp, requests)
            logger.info("Stats sheet updated")
            return True
        except Exception as exc:
            logger.error("update_stats_sheet failed: %s", exc)
            return False

    def update_revenue_sheet(self, revenue: dict) -> bool:
        """Rewrite the «Выручка» sheet.

        Layout (10 columns — see _REVENUE_HEADER):
          1. Строка «Обновлено»
          2. СВОДКА — быстрые итоги: Сегодня / Эта неделя / Этот месяц / Всё время
          3. ПО НЕДЕЛЯМ — последние 8 недель
          4. ПО МЕСЯЦАМ — последние 6 месяцев
          5. ИТОГО ЗА ВСЁ ВРЕМЯ
          6. ВСЕ ПЛАТЕЖИ — детальная история каждого пополнения

        Как считается (правило неизменно):
          Выручка  = фактически уплаченная сумма (amount_paid), или lessons × 1500 для старых записей
          Преподу  = lessons × 1000 (всегда 1000 за занятие, вне зависимости от тарифа)
          Хозяину  = Выручка − Преподу
        """
        if not self.is_configured():
            return False
        try:
            from datetime import datetime as _dt, timedelta as _td
            import calendar as _cal
            sp = self._open_spreadsheet()
            if sp is None:
                return False
            ws = self._get_or_add_worksheet(sp, _REVENUE_SHEET, cols=_N_REV)
            ws.clear()

            # ── helpers ──────────────────────────────────────────────────────
            _months_ru = {
                "01": "январь",  "02": "февраль", "03": "март",    "04": "апрель",
                "05": "май",     "06": "июнь",    "07": "июль",    "08": "август",
                "09": "сентябрь","10": "октябрь", "11": "ноябрь",  "12": "декабрь",
            }
            _months_ru_gen = {
                "01": "января",  "02": "февраля", "03": "марта",   "04": "апреля",
                "05": "мая",     "06": "июня",    "07": "июля",    "08": "августа",
                "09": "сентября","10": "октября", "11": "ноября",  "12": "декабря",
            }

            def _parse_date(s: str) -> _dt | None:
                try:
                    return _dt.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
                try:
                    return _dt.strptime(str(s)[:10], "%Y-%m-%d")
                except Exception:
                    return None

            def _rub(n: int) -> str:
                return f"{n:,}".replace(",", " ") + " ₽"

            def _type_label(tariff: str, op_type: str, lessons: int,
                             promo_code: str = "") -> str:
                if op_type == "initial_balance":
                    base = "Стартовый баланс"
                elif tariff in ("per_lesson", "single"):
                    base = "Одиночное занятие"
                elif tariff == "package":
                    base = f"Пакет × {lessons} зан."
                elif tariff == "subscription":
                    base = "Абонемент"
                else:
                    base = tariff or "Прочее"
                return f"{base}  🏷 {promo_code}" if promo_code else base

            # Row builders — all produce _N_REV=10 columns
            def _empty() -> list:
                return [""] * _N_REV

            def _section_row(title: str) -> list:
                return [title] + [""] * (_N_REV - 1)

            def _period_row(label: str, dates: str, d: dict, bold_hint: bool = False) -> list:
                # cols: A=label, B=dates, C-E=blank, F=lessons, G=revenue, H=teacher, I=owner, J=blank
                return [label, dates, "", "", "",
                        d.get("lessons", 0),
                        _rub(d.get("revenue", 0)),
                        _rub(d.get("teacher_pay", 0)),
                        _rub(d.get("owner_cut", 0)),
                        ""]

            def _payment_row(p: dict) -> list:
                date_s, time_s, _ = _split_dt(p["created_at"])
                amount_str = _rub(p["eff_amount"])
                if p["is_estimated"]:
                    amount_str += " *"
                promo = p.get("promo_code", "")
                return [
                    date_s, time_s,
                    p["student_name"], p["teacher_name"],
                    _type_label(p["tariff_type"], p["operation_type"],
                                p["lessons_delta"], promo),
                    p["lessons_delta"],
                    amount_str,
                    _rub(p["teacher_pay"]),
                    _rub(p["owner_cut"]),
                    p["created_by_name"],
                ]

            # ── Build rows with format metadata ──────────────────────────────
            rows_data: list[list] = []
            fmt: list[dict] = []  # {type, row} — row is 1-based data index (sheet row = row+1 due to header)

            def _add(row: list, fmt_type: str | None = None) -> None:
                rows_data.append(row)
                if fmt_type:
                    fmt.append({"type": fmt_type, "row": len(rows_data)})

            # ── Строка «Обновлено» ────────────────────────────────────────────
            _add([f"Обновлено: {_now_msk_str()}"] + [""] * (_N_REV - 1), "updated_row")
            _add(_empty())

            # ── 1. СВОДКА ─────────────────────────────────────────────────────
            _add(_section_row("📊 ИТОГОВАЯ СВОДКА"), "section")
            # sub-header for the summary columns
            _add(["Период", "", "", "", "", "Занятий", "Выручка", "Преподу", "Хозяину", ""], "subheader_num")
            summary = revenue.get("summary", {})
            _summary_rows = [
                ("Сегодня",       summary.get("today",      {})),
                ("Эта неделя",    summary.get("this_week",  {})),
                ("Этот месяц",    summary.get("this_month", {})),
            ]
            for label, d in _summary_rows:
                _add(_period_row(label, "", d or {}), "summary_row")
            _add(_period_row("🏆 Всё время", "С начала работы",
                             summary.get("total", {
                                 "lessons":     revenue.get("total", 0),
                                 "revenue":     revenue.get("total_revenue", 0),
                                 "teacher_pay": revenue.get("total_teacher_pay", 0),
                                 "owner_cut":   revenue.get("total_owner_cut", 0),
                             })), "summary_total")
            _add(_empty())

            # ── 2. ПО НЕДЕЛЯМ ─────────────────────────────────────────────────
            weeks = revenue.get("weeks", [])
            if weeks:
                _add(_section_row("📅 ПО НЕДЕЛЯМ"), "section")
                _add(["Неделя", "Даты", "", "", "", "Занятий", "Выручка", "Преподу", "Хозяину", ""],
                     "subheader_num")
                for i, w in enumerate(weeks):
                    d = _parse_date(w.get("week_start", ""))
                    if d:
                        d_end = d + _td(days=6)
                        if d.month == d_end.month:
                            dates = (f"{d.day}–{d_end.day} "
                                     f"{_months_ru_gen.get(d.strftime('%m'), '')} {d.year}")
                        else:
                            dates = (f"{d.day} {_months_ru_gen.get(d.strftime('%m'), '')} – "
                                     f"{d_end.day} {_months_ru_gen.get(d_end.strftime('%m'), '')} {d_end.year}")
                        label = f"Неделя {d.isocalendar()[1]}"
                    else:
                        dates = w.get("week_key", "")
                        label = "Неделя"
                    _add(_period_row(label, dates, w),
                         "week_current" if i == 0 else "week")
                _add(_empty())

            # ── 3. ПО МЕСЯЦАМ ─────────────────────────────────────────────────
            months = revenue.get("months", [])
            if months:
                _add(_section_row("📆 ПО МЕСЯЦАМ"), "section")
                _add(["Месяц", "Даты", "", "", "", "Занятий", "Выручка", "Преподу", "Хозяину", ""],
                     "subheader_num")
                for i, m in enumerate(months):
                    try:
                        year, mon = m["month_key"].split("-")
                        label = f"{_months_ru.get(mon, mon).capitalize()} {year}"
                        last_day = _cal.monthrange(int(year), int(mon))[1]
                        dates = f"1–{last_day} {_months_ru_gen.get(mon, '')} {year}"
                    except Exception:
                        label = m.get("month_key", "")
                        dates = ""
                    _add(_period_row(label, dates, m),
                         "month_current" if i == 0 else "month")
                _add(_empty())

            # ── 4. ИТОГО ЗА ВСЁ ВРЕМЯ ────────────────────────────────────────
            _add(_period_row(
                "🏆 ИТОГО ЗА ВСЁ ВРЕМЯ", "С начала работы",
                {"lessons":     revenue.get("total", 0),
                 "revenue":     revenue.get("total_revenue", 0),
                 "teacher_pay": revenue.get("total_teacher_pay", 0),
                 "owner_cut":   revenue.get("total_owner_cut", 0)},
            ), "grand_total")
            _add(_empty())

            # ── 5. ВСЕ ПЛАТЕЖИ ────────────────────────────────────────────────
            payments = revenue.get("payments", [])
            _add(_section_row("💳 ВСЕ ПЛАТЕЖИ — ДЕТАЛЬНАЯ ИСТОРИЯ"), "section")
            # sub-header mirrors the main frozen header
            _add(list(_REVENUE_HEADER), "detail_subheader")

            if payments:
                for p in payments:
                    op     = p.get("operation_type", "")
                    tariff = p.get("tariff_type", "per_lesson")
                    promo  = p.get("promo_code", "")
                    if op == "initial_balance":
                        fmt_type = "pay_initial"
                    elif promo:
                        fmt_type = "pay_promo"
                    elif tariff == "package":
                        fmt_type = "pay_package"
                    elif tariff == "subscription":
                        fmt_type = "pay_subscription"
                    else:
                        fmt_type = "pay_per_lesson"
                    _add(_payment_row(p), fmt_type)
            else:
                _add(["Нет данных"] + [""] * (_N_REV - 1))

            # ── Write to sheet ────────────────────────────────────────────────
            ws.update("A1", [list(_REVENUE_HEADER)] + rows_data, value_input_option="USER_ENTERED")

            # ── Formatting ────────────────────────────────────────────────────
            sheet_id = ws.id
            _C_SECTION      = {"red": 0.122, "green": 0.137, "blue": 0.192}   # очень тёмно-синий
            _C_SUBHDR_NUM   = {"red": 0.420, "green": 0.447, "blue": 0.518}   # средний серо-синий
            _C_SUMMARY_ROW  = {"red": 0.988, "green": 0.957, "blue": 0.820}   # мягко-жёлтый
            _C_SUMMARY_TOT  = {"red": 0.988, "green": 0.918, "blue": 0.737}   # золотистый
            _C_WEEK_CUR     = {"red": 0.796, "green": 0.953, "blue": 0.816}   # ярко-зелёный
            _C_WEEK         = {"red": 0.918, "green": 0.980, "blue": 0.918}   # бледно-зелёный
            _C_MONTH_CUR    = {"red": 0.796, "green": 0.878, "blue": 0.980}   # ярко-синий
            _C_MONTH        = {"red": 0.918, "green": 0.945, "blue": 0.980}   # бледно-синий
            _C_GRAND_TOTAL  = {"red": 0.988, "green": 0.878, "blue": 0.698}   # насыщенный золотой
            _C_DET_SUBHDR   = {"red": 0.027, "green": 0.490, "blue": 0.471}   # тёмный тил (как шапка, но светлее)
            _C_PAY_LESSON   = {"red": 0.851, "green": 0.933, "blue": 0.843}   # светло-зелёный
            _C_PAY_PACKAGE  = {"red": 0.839, "green": 0.863, "blue": 0.980}   # светло-лавандовый
            _C_PAY_SUBS     = {"red": 0.988, "green": 0.906, "blue": 0.796}   # светло-оранжевый
            _C_PAY_INIT     = {"red": 0.882, "green": 0.882, "blue": 0.882}   # светло-серый
            _C_PAY_PROMO    = {"red": 0.929, "green": 0.796, "blue": 0.961}   # светло-фиолетовый (промокод)
            _C_UPDATED      = {"red": 0.949, "green": 0.949, "blue": 0.949}

            _color_map = {
                "week_current":    (_C_WEEK_CUR,    True),
                "week":            (_C_WEEK,         False),
                "month_current":   (_C_MONTH_CUR,   True),
                "month":           (_C_MONTH,        False),
                "summary_row":     (_C_SUMMARY_ROW,  False),
                "summary_total":   (_C_SUMMARY_TOT,  True),
                "grand_total":     (_C_GRAND_TOTAL,  True),
                "pay_per_lesson":  (_C_PAY_LESSON,   False),
                "pay_package":     (_C_PAY_PACKAGE,  False),
                "pay_subscription":(_C_PAY_SUBS,     False),
                "pay_initial":     (_C_PAY_INIT,     False),
                "pay_promo":       (_C_PAY_PROMO,    False),
            }

            end_row = len(rows_data) + 2  # 0-based exclusive end for range

            requests: list[dict] = [
                self._header_format_request(sheet_id, _N_REV, _C_REVENUE_HEADER),
                self._freeze_request(sheet_id),
                {"updateSheetProperties": {
                    "properties": {"sheetId": sheet_id,
                                   "gridProperties": {"columnCount": _N_REV}},
                    "fields": "gridProperties.columnCount",
                }},
                # Right-align numeric columns F–I (indices 5–8) for all data rows
                {"repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": end_row,
                              "startColumnIndex": 5, "endColumnIndex": 9},
                    "cell": {"userEnteredFormat": {"horizontalAlignment": "RIGHT"}},
                    "fields": "userEnteredFormat(horizontalAlignment)",
                }},
            ] + self._col_widths_request(sheet_id,
                [120, 90, 160, 145, 145, 70, 140, 135, 130, 150])

            for meta in fmt:
                r = meta["row"]   # 1-based data index → 0-based sheet row index = r (header at 0)
                t = meta["type"]
                n = _N_REV

                if t == "updated_row":
                    requests.append({"repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": r, "endRowIndex": r + 1,
                                  "startColumnIndex": 0, "endColumnIndex": n},
                        "cell": {"userEnteredFormat": {
                            "backgroundColor": _rgb(_C_UPDATED),
                            "textFormat": {"italic": True, "fontSize": 9,
                                           "foregroundColor": {"red": 0.5, "green": 0.5, "blue": 0.5}},
                        }},
                        "fields": "userEnteredFormat(backgroundColor,textFormat)",
                    }})

                elif t == "section":
                    requests.append({"repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": r, "endRowIndex": r + 1,
                                  "startColumnIndex": 0, "endColumnIndex": n},
                        "cell": {"userEnteredFormat": {
                            "backgroundColor": _rgb(_C_SECTION),
                            "textFormat": {"foregroundColor": _C_WHITE, "bold": True, "fontSize": 10},
                            "horizontalAlignment": "LEFT",
                        }},
                        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
                    }})
                    requests.append({"updateDimensionProperties": {
                        "range": {"sheetId": sheet_id, "dimension": "ROWS",
                                  "startIndex": r, "endIndex": r + 1},
                        "properties": {"pixelSize": 28}, "fields": "pixelSize",
                    }})

                elif t == "subheader_num":
                    requests.append({"repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": r, "endRowIndex": r + 1,
                                  "startColumnIndex": 0, "endColumnIndex": n},
                        "cell": {"userEnteredFormat": {
                            "backgroundColor": _rgb(_C_SUBHDR_NUM),
                            "textFormat": {"foregroundColor": _C_WHITE, "bold": True, "fontSize": 9},
                        }},
                        "fields": "userEnteredFormat(backgroundColor,textFormat)",
                    }})

                elif t == "detail_subheader":
                    requests.append({"repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": r, "endRowIndex": r + 1,
                                  "startColumnIndex": 0, "endColumnIndex": n},
                        "cell": {"userEnteredFormat": {
                            "backgroundColor": _rgb(_C_DET_SUBHDR),
                            "textFormat": {"foregroundColor": _C_WHITE, "bold": True, "fontSize": 9},
                        }},
                        "fields": "userEnteredFormat(backgroundColor,textFormat)",
                    }})

                elif t in _color_map:
                    color, bold = _color_map[t]
                    requests.append(self._row_format_request(sheet_id, r, r + 1, color, bold=bold, num_cols=n))

            self._batch_format(sp, requests)
            logger.info("Sheets: Выручка updated (%d weeks, %d months, %d payments)",
                        len(weeks), len(months), len(payments))
            return True
        except Exception as exc:
            logger.error("update_revenue_sheet failed: %s", exc)
            return False

    def mark_cancelled(self, attendance_id: int, cancelled_by: str, lesson_datetime: str) -> bool:
        if not self.is_configured():
            return False
        try:
            sp = self._open_spreadsheet()
            if sp is None:
                return False
            ws = self._get_or_create_journal(sp)
            date_s, time_s, day_s = _split_dt(lesson_datetime)
            ws.insert_rows([[date_s, time_s, day_s, "", "", "", "", "Отменено", "", "", cancelled_by, str(attendance_id)]],
                           row=2, value_input_option="USER_ENTERED")
            return True
        except Exception as exc:
            logger.error("Sheets cancel failed: %s", exc)
            return False

    # ── «Пополнения» ──────────────────────────────────────────────────────────
    def update_topups_sheet(self, data: dict) -> bool:
        """Rewrite the «Пополнения» sheet with balance top-up history."""
        if not self.is_configured():
            return False
        try:
            sp = self._open_spreadsheet()
            if sp is None:
                return False

            ws = self._get_or_add_worksheet(sp, _TOPUPS_SHEET,
                                            rows=5000, cols=len(_TOPUPS_HEADER))
            ws.clear()

            price = self._lesson_price
            n = len(_TOPUPS_HEADER)  # 11

            _OP_LABELS = {
                "manual_topup":    "Пополнение",
                "initial_balance": "Стартовый",
            }
            _C_TOPUP_ROW    = {"red": 0.851, "green": 0.937, "blue": 0.855}   # нежно-зелёный
            _C_INITIAL_ROW  = {"red": 0.827, "green": 0.882, "blue": 0.949}   # нежно-синий
            _C_SUMMARY_BG   = {"red": 0.255, "green": 0.302, "blue": 0.502}   # тёмно-индиго (как шапка)
            _C_SUMMARY_DATA = {"red": 0.988, "green": 0.918, "blue": 0.737}   # золотистый
            _C_UPDATED_ROW  = {"red": 0.949, "green": 0.949, "blue": 0.949}

            def _rub_amount(amount: int) -> str:
                return f"{amount:,}".replace(",", " ") + " ₽"

            rows_data: list[list] = []
            format_meta: list[dict] = []  # {type, row_1based}

            # ── Строка «Обновлено» ──────────────────────────────────────────
            rows_data.append([f"Обновлено: {_now_msk_str()}", *[""] * (n - 1)])
            format_meta.append({"type": "updated", "row": len(rows_data)})

            # ── Сводный блок ────────────────────────────────────────────────
            rows_data.append(["📊 ИТОГИ ПОПОЛНЕНИЙ", *[""] * (n - 1)])
            format_meta.append({"type": "summary_header", "row": len(rows_data)})

            total   = data.get("total_lessons",  0)
            month   = data.get("month_lessons",  0)
            week    = data.get("week_lessons",   0)
            today_l = data.get("today_lessons",  0)
            total_rev   = data.get("total_revenue",  0)
            month_rev   = data.get("month_revenue",  0)
            week_rev    = data.get("week_revenue",   0)
            today_rev   = data.get("today_revenue",  0)
            for label, lessons_val, rev_val in [
                ("Всего занятий добавлено",   total,   total_rev),
                ("За этот месяц",              month,   month_rev),
                ("За эту неделю",              week,    week_rev),
                ("Сегодня",                    today_l, today_rev),
            ]:
                rows_data.append([label, lessons_val, "", _rub_amount(rev_val), *[""] * (n - 4)])
                format_meta.append({"type": "summary_data", "row": len(rows_data)})

            # ── Разделитель ─────────────────────────────────────────────────
            rows_data.append([""] * n)

            # ── Строка-подзаголовок перед данными ───────────────────────────
            rows_data.append(["▸ Все пополнения (новые сверху)", *[""] * (n - 1)])
            format_meta.append({"type": "data_header", "row": len(rows_data)})

            # ── Данные ──────────────────────────────────────────────────────
            for row in data.get("rows", []):
                date_s, time_s, day_s = _split_dt(row["created_at"])
                op_label = _OP_LABELS.get(row["operation_type"], row["operation_type"])
                lessons  = row["lessons_delta"]
                # Use stored amount_paid; fall back to lessons * price for historical rows
                raw_amount = row.get("amount_paid", 0)
                amount = _rub_amount(raw_amount if raw_amount > 0 else lessons * price)
                rows_data.append([
                    date_s, time_s, day_s,
                    row["student_name"],
                    row["subject_name"],
                    row["teacher_name"],
                    lessons,
                    amount,
                    op_label,
                    row["created_by_name"],
                    row["comment"],
                ])
                format_meta.append({
                    "type": "topup" if row["operation_type"] == "manual_topup" else "initial",
                    "row": len(rows_data),
                })

            if not data.get("rows"):
                rows_data.append(["Нет данных", *[""] * (n - 1)])

            header = list(_TOPUPS_HEADER)
            header[7] = "Сумма (₽)"
            ws.update("A1", [header] + rows_data, value_input_option="USER_ENTERED")

            sheet_id = ws.id
            _C_DATA_HEADER = {"red": 0.831, "green": 0.871, "blue": 0.980}  # light lavender

            requests = [
                self._header_format_request(sheet_id, n, _C_TOPUPS_HEADER),
                self._freeze_request(sheet_id, rows=2),
            ] + self._col_widths_request(sheet_id, [90, 60, 40, 170, 150, 150, 70, 130, 100, 150, 200])

            for meta in format_meta:
                r = meta["row"]   # 1-based data row → 0-based sheet row = r (header=row0)
                t = meta["type"]

                if t == "updated":
                    requests.append({"repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": r, "endRowIndex": r + 1,
                                   "startColumnIndex": 0, "endColumnIndex": n},
                        "cell": {"userEnteredFormat": {
                            "backgroundColor": _rgb(_C_UPDATED_ROW),
                            "textFormat": {"italic": True,
                                           "foregroundColor": {"red": 0.5, "green": 0.5, "blue": 0.5},
                                           "fontSize": 9},
                        }},
                        "fields": "userEnteredFormat(backgroundColor,textFormat)",
                    }})

                elif t == "summary_header":
                    requests.append({"repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": r, "endRowIndex": r + 1,
                                   "startColumnIndex": 0, "endColumnIndex": n},
                        "cell": {"userEnteredFormat": {
                            "backgroundColor": _rgb(_C_SUMMARY_BG),
                            "textFormat": {"foregroundColor": _C_WHITE, "bold": True, "fontSize": 10},
                            "horizontalAlignment": "LEFT",
                        }},
                        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
                    }})
                    requests.append({"updateDimensionProperties": {
                        "range": {"sheetId": sheet_id, "dimension": "ROWS",
                                   "startIndex": r, "endIndex": r + 1},
                        "properties": {"pixelSize": 28}, "fields": "pixelSize",
                    }})

                elif t == "summary_data":
                    requests.append({"repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": r, "endRowIndex": r + 1,
                                   "startColumnIndex": 0, "endColumnIndex": 4},
                        "cell": {"userEnteredFormat": {
                            "backgroundColor": _rgb(_C_SUMMARY_DATA),
                            "textFormat": {"bold": False},
                        }},
                        "fields": "userEnteredFormat(backgroundColor,textFormat)",
                    }})
                    # Right-align the number and amount columns
                    requests.append({"repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": r, "endRowIndex": r + 1,
                                   "startColumnIndex": 1, "endColumnIndex": 4},
                        "cell": {"userEnteredFormat": {"horizontalAlignment": "RIGHT"}},
                        "fields": "userEnteredFormat(horizontalAlignment)",
                    }})

                elif t == "data_header":
                    requests.append({"repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": r, "endRowIndex": r + 1,
                                   "startColumnIndex": 0, "endColumnIndex": n},
                        "cell": {"userEnteredFormat": {
                            "backgroundColor": _rgb(_C_DATA_HEADER),
                            "textFormat": {"bold": True, "fontSize": 9},
                        }},
                        "fields": "userEnteredFormat(backgroundColor,textFormat)",
                    }})

                elif t in ("topup", "initial"):
                    color = _C_TOPUP_ROW if t == "topup" else _C_INITIAL_ROW
                    requests.append(self._row_format_request(sheet_id, r, r + 1, color, num_cols=n))

            # Right-align +Занятий и Сумма (cols G=6, H=7, 0-based)
            data_start = next((m["row"] for m in format_meta if m["type"] == "data_header"), 7) + 1
            requests.append({"repeatCell": {
                "range": {"sheetId": sheet_id,
                           "startRowIndex": data_start, "endRowIndex": len(rows_data) + 2,
                           "startColumnIndex": 6, "endColumnIndex": 8},
                "cell": {"userEnteredFormat": {"horizontalAlignment": "RIGHT"}},
                "fields": "userEnteredFormat(horizontalAlignment)",
            }})

            self._batch_format(sp, requests)
            logger.info("Sheets: Пополнения updated (%d rows)", len(data.get("rows", [])))
            return True
        except Exception as exc:
            logger.error("update_topups_sheet failed: %s", exc)
            return False

    def update_discounts_sheet(self, promos: list[dict]) -> bool:
        """Rewrite the «Промокоды» sheet with all promo codes."""
        if not self.is_configured():
            return False
        try:
            sp = self._open_spreadsheet()
            if sp is None:
                return False

            n = len(_DISCOUNTS_HEADER)
            ws = self._get_or_add_worksheet(sp, _DISCOUNTS_SHEET, rows=500, cols=n)
            ws.clear()

            _C_ACTIVE = {"red": 0.851, "green": 0.937, "blue": 0.855}   # light green
            _C_ARCHIVE = {"red": 0.949, "green": 0.949, "blue": 0.949}  # light grey
            _C_UPDATED = {"red": 0.949, "green": 0.949, "blue": 0.949}

            _APPLIES = {0: "Разовые занятия", 1: "Пакеты занятий", 2: "Все"}

            rows_data: list[list] = []
            format_meta: list[dict] = []

            # "Обновлено" row
            rows_data.append([f"Обновлено: {_now_msk_str()}", *[""] * (n - 1)])
            format_meta.append({"type": "updated", "row": len(rows_data)})

            for p in promos:
                dtype = p["discount_type"]
                dval = p["discount_value"]
                discount_str = f"{int(dval)}%" if dtype == "percent" else f"{int(dval)} ₽"

                max_uses = p["max_uses"]
                used = p["used_count"] or 0
                uses_str = f"{used} / {max_uses}" if max_uses else f"{used} / ∞"

                valid = p["valid_until"] or "бессрочно"
                if valid and valid != "бессрочно":
                    valid = valid[:10]  # date only

                applies = _APPLIES.get(int(p.get("applies_to_packages") or 0), "Все")
                status = "✅ Активен" if p["active"] else "📦 Архив"
                created = (p.get("created_at") or "")[:10]
                assigned = p.get("assigned_students") or "—"

                rows_data.append([
                    p["code"], discount_str, applies, uses_str,
                    valid, assigned, status, created,
                ])
                is_active = bool(p["active"])
                format_meta.append({"type": "active" if is_active else "archive",
                                    "row": len(rows_data)})

            ws.update("A1", [_DISCOUNTS_HEADER, *rows_data], value_input_option="USER_ENTERED")

            sheet_id = ws.id
            requests = [
                self._header_format_request(sheet_id, n, _C_DISCOUNTS_HEADER),
                self._freeze_request(sheet_id, rows=1),
                # "Обновлено" row (index 1)
                {"repeatCell": {
                    "range": {"sheetId": sheet_id,
                              "startRowIndex": 1, "endRowIndex": 2,
                              "startColumnIndex": 0, "endColumnIndex": n},
                    "cell": {"userEnteredFormat": {
                        "backgroundColor": _rgb(_C_UPDATED),
                        "textFormat": {"italic": True,
                                       "foregroundColor": {"red": 0.5, "green": 0.5, "blue": 0.5},
                                       "fontSize": 9},
                    }},
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }},
            ]

            # Colour active / archive rows
            for meta in format_meta:
                if meta["type"] == "updated":
                    continue
                row_idx = meta["row"] + 1  # +1 because header row is first
                colour = _C_ACTIVE if meta["type"] == "active" else _C_ARCHIVE
                requests.append({"repeatCell": {
                    "range": {"sheetId": sheet_id,
                              "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                              "startColumnIndex": 0, "endColumnIndex": n},
                    "cell": {"userEnteredFormat": {"backgroundColor": _rgb(colour)}},
                    "fields": "userEnteredFormat(backgroundColor)",
                }})

            # Auto-resize columns
            requests.append({"autoResizeDimensions": {
                "dimensions": {"sheetId": sheet_id, "dimension": "COLUMNS",
                               "startIndex": 0, "endIndex": n}
            }})
            self._batch_format(sp, requests)
            logger.info("Sheets: Промокоды updated (%d rows)", len(promos))
            return True
        except Exception as exc:
            logger.error("update_discounts_sheet failed: %s", exc)
            return False


_client: SheetsClient | None = None


def get_sheets_client() -> SheetsClient:
    global _client
    if _client is None:
        _client = SheetsClient()
    return _client
