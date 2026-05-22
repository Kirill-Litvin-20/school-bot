import json
import os
import importlib.util
import secrets
import re
from html import escape
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import psycopg2
except ImportError:  # pragma: no cover - optional dependency
    psycopg2 = None

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


def _prepare_sql(sql: str) -> str:
    """Normalise a SQL string for psycopg2.

    Replaces ? parameter markers with %s and escapes literal % inside string
    literals as %% so psycopg2 does not misinterpret them as parameter markers.
    Also converts legacy CREATE TABLE and INSERT syntax to PostgreSQL equivalents.
    """
    # -- syntax conversions --
    adapted = sql
    adapted = adapted.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    if re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", adapted, flags=re.IGNORECASE):
        adapted = re.sub(
            r"\bINSERT\s+OR\s+IGNORE\s+INTO\b",
            "INSERT INTO",
            adapted,
            flags=re.IGNORECASE,
        )
        if "ON CONFLICT" not in adapted.upper():
            adapted = adapted.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    if re.search(
        r"\bINSERT\s+OR\s+REPLACE\s+INTO\s+debt_daily_snapshots\b",
        adapted,
        flags=re.IGNORECASE,
    ):
        adapted = re.sub(
            r"\bINSERT\s+OR\s+REPLACE\s+INTO\b",
            "INSERT INTO",
            adapted,
            flags=re.IGNORECASE,
        )
        if "ON CONFLICT" not in adapted.upper():
            adapted = (
                adapted.rstrip().rstrip(";")
                + " ON CONFLICT (snapshot_date, student_lesson_id)"
                + " DO UPDATE SET lesson_balance = EXCLUDED.lesson_balance"
            )

    if re.search(
        r"\bINSERT\s+OR\s+REPLACE\s+INTO\s+debt_report_runs\b",
        adapted,
        flags=re.IGNORECASE,
    ):
        adapted = re.sub(
            r"\bINSERT\s+OR\s+REPLACE\s+INTO\b",
            "INSERT INTO",
            adapted,
            flags=re.IGNORECASE,
        )
        if "ON CONFLICT" not in adapted.upper():
            adapted = (
                adapted.rstrip().rstrip(";")
                + " ON CONFLICT (report_date)"
                + " DO UPDATE SET sent_at = EXCLUDED.sent_at"
            )

    # -- parameter marker conversion --
    result: list[str] = []
    in_single_quote = False
    in_double_quote = False
    escaped = False

    for ch in adapted:
        if ch == "\\" and not escaped:
            escaped = True
            result.append(ch)
            continue

        if ch == "'" and not in_double_quote and not escaped:
            in_single_quote = not in_single_quote
            result.append(ch)
            escaped = False
            continue

        if ch == '"' and not in_single_quote and not escaped:
            in_double_quote = not in_double_quote
            result.append(ch)
            escaped = False
            continue

        if ch == "?" and not in_single_quote and not in_double_quote:
            result.append("%s")
        elif ch == "%" and (in_single_quote or in_double_quote):
            result.append("%%")
        else:
            result.append(ch)
        escaped = False

    return "".join(result)


class PostgresCursorCompat:
    def __init__(self, cursor):
        self._cursor = cursor
        self._manual_rows = None
        self._lastrowid = None

    @property
    def rowcount(self):
        if self._manual_rows is not None:
            return len(self._manual_rows)
        return self._cursor.rowcount

    @staticmethod
    def _extract_pragma_table_name(sql: str) -> str | None:
        match = re.match(r"^\s*PRAGMA\s+table_info\(([^)]+)\)\s*$", sql, flags=re.IGNORECASE)
        if not match:
            return None
        return match.group(1).strip().strip('"').strip("'")

    def execute(self, sql: str, params=()):
        table_name = self._extract_pragma_table_name(sql)
        if table_name:
            self._cursor.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = %s
                ORDER BY ordinal_position
                """,
                (table_name,),
            )
            rows = self._cursor.fetchall()
            self._manual_rows = [
                (idx, row[0], row[1], 0, None, 0)
                for idx, row in enumerate(rows)
            ]
            self._lastrowid = None
            return self

        self._manual_rows = None
        adapted_sql = _prepare_sql(sql)
        self._cursor.execute(adapted_sql, params)
        self._lastrowid = None
        if re.match(r"^\s*INSERT\s+INTO\b", adapted_sql, flags=re.IGNORECASE):
            try:
                self._cursor.execute("SAVEPOINT sp_lastrowid")
                self._cursor.execute("SELECT LASTVAL()")
                row = self._cursor.fetchone()
                self._lastrowid = row[0] if row else None
                self._cursor.execute("RELEASE SAVEPOINT sp_lastrowid")
            except Exception:
                try:
                    self._cursor.execute("ROLLBACK TO SAVEPOINT sp_lastrowid")
                    self._cursor.execute("RELEASE SAVEPOINT sp_lastrowid")
                except Exception:
                    pass
                self._lastrowid = None
        return self

    def fetchall(self):
        if self._manual_rows is not None:
            return self._manual_rows
        return self._cursor.fetchall()

    def fetchone(self):
        if self._manual_rows is not None:
            if not self._manual_rows:
                return None
            return self._manual_rows.pop(0)
        return self._cursor.fetchone()

    def __getattr__(self, item):
        return getattr(self._cursor, item)

    @property
    def lastrowid(self):
        return self._lastrowid


class PostgresConnectionCompat:
    def __init__(self, connection):
        self._connection = connection

    def cursor(self):
        return PostgresCursorCompat(self._connection.cursor())

    def commit(self):
        self._connection.commit()

    def close(self):
        self._connection.close()

    def rollback(self):
        self._connection.rollback()

    def __getattr__(self, item):
        return getattr(self._connection, item)


def get_connection():
    if psycopg2 is None:
        raise RuntimeError(
            "psycopg2 is not installed. Install dependencies from requirements.txt."
        )
    conn = psycopg2.connect(DATABASE_URL)
    return PostgresConnectionCompat(conn)


def get_db_backend_name() -> str:
    return "postgresql"


def get_existing_tables() -> set[str]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        """
    )
    rows = cur.fetchall()
    conn.close()
    return {str(row[0]) for row in rows if row and row[0]}


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT NOT NULL,
        telegram_id INTEGER,
        phone TEXT
    )
    """)
    _ensure_students_table_columns(cur)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE NOT NULL,
        full_name TEXT NOT NULL,
        role TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )
    """)
    _ensure_users_table_columns(cur)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS teachers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE,
        full_name TEXT NOT NULL
    )
    """)
    _ensure_teachers_table_columns(cur)

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS teacher_subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            subject_name TEXT NOT NULL,
            UNIQUE(teacher_id, subject_name),
            FOREIGN KEY(teacher_id) REFERENCES teachers(id)
        )
        """
    )

    cur.execute("""
    CREATE TABLE IF NOT EXISTS student_lessons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        teacher_id INTEGER NOT NULL,
        subject_name TEXT NOT NULL,
        lesson_balance INTEGER NOT NULL DEFAULT 0,
        tariff_type TEXT NOT NULL,
        FOREIGN KEY(student_id) REFERENCES students(id),
        FOREIGN KEY(teacher_id) REFERENCES teachers(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_lesson_id INTEGER NOT NULL,
        lesson_date TEXT NOT NULL,
        status TEXT NOT NULL,
        written_off INTEGER NOT NULL DEFAULT 0,
        marked_by INTEGER,
        FOREIGN KEY(student_lesson_id) REFERENCES student_lessons(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS balance_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_lesson_id INTEGER NOT NULL,
        operation_type TEXT NOT NULL,
        lessons_delta INTEGER NOT NULL,
        comment TEXT,
        created_at TEXT NOT NULL,
        created_by INTEGER,
        FOREIGN KEY(student_lesson_id) REFERENCES student_lessons(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS payment_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_user_id INTEGER,
        telegram_username TEXT,
        telegram_full_name TEXT,
        caption_text TEXT,
        file_id TEXT,
        file_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        approved_by INTEGER,
        rejected_by INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT
    )
    """)
    _ensure_payment_requests_columns(cur)

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_telegram_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            target_type TEXT,
            target_id INTEGER,
            details TEXT,
            status TEXT NOT NULL DEFAULT 'success',
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS publication_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_by INTEGER NOT NULL,
            audience TEXT NOT NULL DEFAULT 'students',
            description TEXT NOT NULL,
            photo_file_id TEXT,
            links_json TEXT,
            status TEXT NOT NULL DEFAULT 'scheduled',
            scheduled_for TEXT NOT NULL,
            sent_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            last_error TEXT
        )
        """
    )
    _ensure_publication_posts_columns(cur)

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS review_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_by INTEGER NOT NULL,
            description TEXT NOT NULL,
            media_file_id TEXT,
            media_type TEXT,
            media_local_path TEXT,
            links_json TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
        """
    )
    _ensure_review_cards_columns(cur)

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS maintenance_runs (
            task_name TEXT PRIMARY KEY,
            executed_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS debt_reminder_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_lesson_id INTEGER NOT NULL,
            reminder_date TEXT NOT NULL,
            reminded_at TEXT NOT NULL,
            UNIQUE(student_lesson_id, reminder_date)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS debt_daily_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            student_lesson_id INTEGER NOT NULL,
            lesson_balance INTEGER NOT NULL,
            UNIQUE(snapshot_date, student_lesson_id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS debt_report_runs (
            report_date TEXT PRIMARY KEY,
            sent_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS known_telegram_users (
            telegram_id INTEGER PRIMARY KEY,
            telegram_username TEXT,
            full_name TEXT,
            last_seen_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_known_telegram_users_username
        ON known_telegram_users(telegram_username)
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS onboarding_invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            role TEXT NOT NULL,
            full_name TEXT NOT NULL,
            telegram_username TEXT NOT NULL,
            entity_type TEXT,
            entity_id INTEGER,
            created_by INTEGER,
            created_at TEXT NOT NULL,
            used_by_telegram_id INTEGER,
            used_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inviter_telegram_id INTEGER NOT NULL,
            invitee_telegram_id INTEGER NOT NULL UNIQUE,
            invitee_student_id INTEGER,
            status TEXT NOT NULL DEFAULT 'captured',
            created_at TEXT NOT NULL,
            rewarded_at TEXT,
            reward_balance_history_id INTEGER
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sheets_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            attendance_id INTEGER NOT NULL UNIQUE,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS max_fsm_state (
            max_user_id INTEGER PRIMARY KEY,
            state TEXT,
            data TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS max_referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inviter_max_id INTEGER NOT NULL,
            invitee_max_id INTEGER NOT NULL UNIQUE,
            invitee_student_id INTEGER,
            status TEXT NOT NULL DEFAULT 'captured',
            created_at TEXT NOT NULL,
            rewarded_at TEXT,
            reward_balance_history_id INTEGER
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS account_link_codes (
            code TEXT PRIMARY KEY,
            telegram_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    cur.execute("""
    CREATE TABLE IF NOT EXISTS promo_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL COLLATE NOCASE,
        discount_type TEXT NOT NULL CHECK(discount_type IN ('percent', 'fixed_rub')),
        discount_value REAL NOT NULL,
        applies_to_packages INTEGER NOT NULL DEFAULT 0,
        max_uses INTEGER,
        used_count INTEGER NOT NULL DEFAULT 0,
        valid_until TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS student_promo_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        promo_code_id INTEGER NOT NULL,
        assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(student_id) REFERENCES students(id),
        FOREIGN KEY(promo_code_id) REFERENCES promo_codes(id),
        UNIQUE(student_id, promo_code_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS schedule_slots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        direction_id INTEGER NOT NULL,
        schedule_type TEXT NOT NULL,
        day_of_week INTEGER,
        specific_date TEXT,
        lesson_time TEXT NOT NULL,
        created_by BIGINT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(direction_id) REFERENCES student_lessons(id)
    )
    """)

    _ensure_postgres_bigint_columns(cur)
    _ensure_promo_codes_columns(cur)
    _ensure_balance_history_columns(cur)
    _cleanup_teacher_profiles_for_non_teacher_users(cur)
    conn.commit()
    _sync_teacher_subject_links()
    conn.close()
    if _is_truthy_env(os.getenv("SCHOOL_SEED_TEACHERS_FROM_CATALOG", "0")):
        sync_teachers_from_catalog()


def _ensure_promo_codes_columns(cur):
    """Add missing columns to promo_codes table (PostgreSQL migration safety)."""
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'promo_codes'
        """
    )
    existing = {row[0] for row in cur.fetchall()}
    if "applies_to_packages" not in existing:
        cur.execute("ALTER TABLE promo_codes ADD COLUMN applies_to_packages INTEGER NOT NULL DEFAULT 0")
    if "max_uses" not in existing:
        cur.execute("ALTER TABLE promo_codes ADD COLUMN max_uses INTEGER")
    if "used_count" not in existing:
        cur.execute("ALTER TABLE promo_codes ADD COLUMN used_count INTEGER NOT NULL DEFAULT 0")
    if "valid_until" not in existing:
        cur.execute("ALTER TABLE promo_codes ADD COLUMN valid_until TEXT")
    if "active" not in existing:
        cur.execute("ALTER TABLE promo_codes ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
    if "created_at" not in existing:
        cur.execute("ALTER TABLE promo_codes ADD COLUMN created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP")
    # Make created_by and created_at nullable (old schema had them NOT NULL without defaults)
    cur.execute("""
        SELECT is_nullable FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'promo_codes' AND column_name = 'created_by'
    """)
    row = cur.fetchone()
    if row and row[0] == "NO":
        cur.execute("ALTER TABLE promo_codes ALTER COLUMN created_by DROP NOT NULL")
    cur.execute("""
        SELECT is_nullable, column_default FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'promo_codes' AND column_name = 'created_at'
    """)
    row = cur.fetchone()
    if row and (row[0] == "NO" or not row[1]):
        cur.execute("ALTER TABLE promo_codes ALTER COLUMN created_at DROP NOT NULL")
        cur.execute("ALTER TABLE promo_codes ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP")
    # payment_requests: track which promo was actually applied
    cur.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'payment_requests'"""
    )
    pr_cols = {r[0] for r in cur.fetchall()}
    if 'promo_code_id_used' not in pr_cols:
        cur.execute("ALTER TABLE payment_requests ADD COLUMN promo_code_id_used INTEGER")

    # student_promo_codes: used_at for one-time-per-student enforcement
    cur.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'student_promo_codes'"""
    )
    spc_cols = {r[0] for r in cur.fetchall()}
    if 'used_at' not in spc_cols:
        cur.execute("ALTER TABLE student_promo_codes ADD COLUMN used_at TEXT")


def _ensure_balance_history_columns(cur):
    """Add amount_paid column to balance_history (actual rubles paid per topup)."""
    cur.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'balance_history'"""
    )
    bh_cols = {r[0] for r in cur.fetchall()}
    if 'amount_paid' not in bh_cols:
        cur.execute("ALTER TABLE balance_history ADD COLUMN amount_paid INTEGER NOT NULL DEFAULT 0")


def _ensure_teachers_table_columns(cur):
    cur.execute("PRAGMA table_info(teachers)")
    existing_columns = {row[1] for row in cur.fetchall()}

    if "subject_name" not in existing_columns:
        cur.execute("ALTER TABLE teachers ADD COLUMN subject_name TEXT")
    if "description" not in existing_columns:
        cur.execute("ALTER TABLE teachers ADD COLUMN description TEXT")
    if "photo_path" not in existing_columns:
        cur.execute("ALTER TABLE teachers ADD COLUMN photo_path TEXT")


def _ensure_publication_posts_columns(cur):
    cur.execute("PRAGMA table_info(publication_posts)")
    existing_columns = {row[1] for row in cur.fetchall()}
    if "audience" not in existing_columns:
        cur.execute("ALTER TABLE publication_posts ADD COLUMN audience TEXT NOT NULL DEFAULT 'students'")


def _ensure_review_cards_columns(cur):
    cur.execute("PRAGMA table_info(review_cards)")
    existing_columns = {row[1] for row in cur.fetchall()}
    if "media_file_id" not in existing_columns:
        cur.execute("ALTER TABLE review_cards ADD COLUMN media_file_id TEXT")
    if "media_type" not in existing_columns:
        cur.execute("ALTER TABLE review_cards ADD COLUMN media_type TEXT")
    if "media_local_path" not in existing_columns:
        cur.execute("ALTER TABLE review_cards ADD COLUMN media_local_path TEXT")
    if "links_json" not in existing_columns:
        cur.execute("ALTER TABLE review_cards ADD COLUMN links_json TEXT")
    if "is_active" not in existing_columns:
        cur.execute("ALTER TABLE review_cards ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
    if "updated_at" not in existing_columns:
        cur.execute("ALTER TABLE review_cards ADD COLUMN updated_at TEXT")


def _sync_teacher_subject_links():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO teacher_subjects (teacher_id, subject_name)
        SELECT id, subject_name
        FROM teachers
        WHERE subject_name IS NOT NULL
          AND TRIM(subject_name) <> ''
        """
    )
    cur.execute(
        """
        DELETE FROM teacher_subjects
        WHERE subject_name IS NULL
           OR TRIM(subject_name) = ''
           OR teacher_id NOT IN (SELECT id FROM teachers)
        """
    )
    # Do not force single-subject mode: a teacher can be linked to multiple subjects.
    conn.commit()
    conn.close()


def _ensure_students_table_columns(cur):
    cur.execute("PRAGMA table_info(students)")
    existing_columns = {row[1] for row in cur.fetchall()}
    if "telegram_username" not in existing_columns:
        cur.execute("ALTER TABLE students ADD COLUMN telegram_username TEXT")
    if "referred_by_telegram_id" not in existing_columns:
        cur.execute("ALTER TABLE students ADD COLUMN referred_by_telegram_id INTEGER")
    if "first_paid_at" not in existing_columns:
        cur.execute("ALTER TABLE students ADD COLUMN first_paid_at TEXT")
    if "first_paid_payment_id" not in existing_columns:
        cur.execute("ALTER TABLE students ADD COLUMN first_paid_payment_id INTEGER")
    if "max_id" not in existing_columns:
        cur.execute("ALTER TABLE students ADD COLUMN max_id INTEGER")
    if "max_username" not in existing_columns:
        cur.execute("ALTER TABLE students ADD COLUMN max_username TEXT")


def _ensure_users_table_columns(cur):
    cur.execute("PRAGMA table_info(users)")
    existing_columns = {row[1] for row in cur.fetchall()}
    if "telegram_username" not in existing_columns:
        cur.execute("ALTER TABLE users ADD COLUMN telegram_username TEXT")
    if "is_visible_to_students" not in existing_columns:
        cur.execute("ALTER TABLE users ADD COLUMN is_visible_to_students INTEGER NOT NULL DEFAULT 1")
    if "max_id" not in existing_columns:
        cur.execute("ALTER TABLE users ADD COLUMN max_id INTEGER")


def _ensure_payment_requests_columns(cur):
    cur.execute("PRAGMA table_info(payment_requests)")
    existing_columns = {row[1] for row in cur.fetchall()}
    if "source_platform" not in existing_columns:
        cur.execute(
            "ALTER TABLE payment_requests ADD COLUMN source_platform TEXT NOT NULL DEFAULT 'telegram'"
        )
    if "max_user_id" not in existing_columns:
        cur.execute("ALTER TABLE payment_requests ADD COLUMN max_user_id INTEGER")
    if "preferred_direction_id" not in existing_columns:
        cur.execute("ALTER TABLE payment_requests ADD COLUMN preferred_direction_id INTEGER")


def _ensure_postgres_bigint_columns(cur):
    bigint_columns: dict[str, set[str]] = {
        "students": {"telegram_id", "referred_by_telegram_id", "max_id"},
        "users": {"telegram_id", "max_id"},
        "teachers": {"telegram_id"},
        "attendance": {"marked_by"},
        "balance_history": {"created_by"},
        "payment_requests": {"telegram_user_id", "approved_by", "rejected_by", "max_user_id"},
        "admin_actions": {"admin_telegram_id"},
        "known_telegram_users": {"telegram_id"},
        "onboarding_invites": {"created_by", "used_by_telegram_id"},
        "publication_posts": {"created_by"},
        "review_cards": {"created_by"},
        "referrals": {"inviter_telegram_id", "invitee_telegram_id"},
        "account_link_codes": {"telegram_id"},
        "max_fsm_state": {"max_user_id"},
    }

    cur.execute(
        """
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
        """
    )

    for table_name, column_name, data_type in cur.fetchall():
        table_rules = bigint_columns.get(str(table_name))
        if not table_rules:
            continue
        if str(column_name) not in table_rules:
            continue
        if str(data_type).lower() == "bigint":
            continue
        cur.execute(
            f"ALTER TABLE {table_name} ALTER COLUMN {column_name} TYPE BIGINT"
        )


def _is_truthy_env(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def reset_student_data_for_testing(preserve_superadmin_ids: list[int] | tuple[int, ...] | None = None):
    preserve_superadmin_ids = tuple(preserve_superadmin_ids or [])
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("BEGIN")

    cur.execute(
        """
        DELETE FROM attendance
        WHERE student_lesson_id IN (SELECT id FROM student_lessons)
        """
    )
    cur.execute(
        """
        DELETE FROM balance_history
        WHERE student_lesson_id IN (SELECT id FROM student_lessons)
        """
    )
    cur.execute("DELETE FROM student_lessons")
    cur.execute("DELETE FROM payment_requests")
    cur.execute("DELETE FROM admin_actions")
    cur.execute("DELETE FROM onboarding_invites")
    cur.execute("DELETE FROM students")
    cur.execute("DELETE FROM users WHERE role = 'student'")
    cur.execute("DELETE FROM users WHERE role = 'admin'")

    if preserve_superadmin_ids:
        placeholders = ",".join("?" for _ in preserve_superadmin_ids)
        cur.execute(
            f"""
            DELETE FROM users
            WHERE role = 'superadmin'
              AND telegram_id NOT IN ({placeholders})
            """,
            preserve_superadmin_ids,
        )
    else:
        cur.execute("DELETE FROM users WHERE role = 'superadmin'")

    conn.commit()
    conn.close()


def load_teacher_cards_from_catalog() -> list[dict]:
    data_path = BASE_DIR.parent / "school-bot" / "data.py"
    if not data_path.exists():
        return []

    try:
        spec = importlib.util.spec_from_file_location("school_bot_data", data_path)
        if spec is None or spec.loader is None:
            return []

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        teachers_data = getattr(module, "TEACHERS_DATA", {})
    except Exception:
        return []

    cards: list[dict] = []
    for subject_name, subject_teachers in teachers_data.items():
        for teacher in subject_teachers:
            name = teacher.get("name")
            if not name:
                continue
            cards.append(
                {
                    "full_name": name,
                    "subject_name": subject_name,
                    "description": teacher.get("description"),
                    "photo_path": teacher.get("photo"),
                    "telegram_id": teacher.get("telegram_id"),
                }
            )
    return cards


def load_teacher_names_from_catalog() -> list[str]:
    cards = load_teacher_cards_from_catalog()
    names: list[str] = []
    for card in cards:
        name = card.get("full_name")
        if name and name not in names:
            names.append(name)
    return names


def sync_teachers_from_catalog() -> int:
    cards = load_teacher_cards_from_catalog()
    if not cards:
        return 0

    conn = get_connection()
    cur = conn.cursor()
    inserted = 0
    for card in cards:
        full_name = card.get("full_name")
        subject_name = card.get("subject_name")
        description = card.get("description")
        photo_path = card.get("photo_path")
        telegram_id = card.get("telegram_id")
        cur.execute(
            "SELECT id FROM teachers WHERE full_name = ? AND COALESCE(subject_name, '') = COALESCE(?, '')",
            (full_name, subject_name),
        )
        exists = cur.fetchone()
        if exists:
            cur.execute(
                """
                UPDATE teachers
                SET description = COALESCE(description, ?),
                    photo_path = COALESCE(photo_path, ?)
                WHERE id = ?
                """,
                (description, photo_path, exists[0]),
            )
            cur.execute(
                """
                INSERT OR IGNORE INTO teacher_subjects (teacher_id, subject_name)
                VALUES (?, ?)
                """,
                (exists[0], subject_name),
            )
            continue
        cur.execute(
            """
            INSERT INTO teachers (telegram_id, full_name, subject_name, description, photo_path)
            VALUES (?, ?, ?, ?, ?)
            """,
            (telegram_id, full_name, subject_name, description, photo_path),
        )
        teacher_id = int(cur.lastrowid)
        cur.execute(
            """
            INSERT OR IGNORE INTO teacher_subjects (teacher_id, subject_name)
            VALUES (?, ?)
            """,
            (teacher_id, subject_name),
        )
        inserted += 1

    conn.commit()
    conn.close()
    return inserted


def reset_system_data_and_seed_teachers(
    preserve_superadmin_ids: list[int] | tuple[int, ...] | None = None,
) -> dict:
    preserve_superadmin_ids = tuple(preserve_superadmin_ids or [])
    teacher_cards = load_teacher_cards_from_catalog()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("BEGIN")

    cur.execute("DELETE FROM attendance")
    cur.execute("DELETE FROM balance_history")
    cur.execute("DELETE FROM student_lessons")
    cur.execute("DELETE FROM payment_requests")
    cur.execute("DELETE FROM admin_actions")
    cur.execute("DELETE FROM onboarding_invites")
    cur.execute("DELETE FROM students")

    cur.execute("DELETE FROM users WHERE role IN ('student', 'admin', 'teacher')")

    if preserve_superadmin_ids:
        placeholders = ",".join("?" for _ in preserve_superadmin_ids)
        cur.execute(
            f"""
            DELETE FROM users
            WHERE role = 'superadmin'
              AND telegram_id NOT IN ({placeholders})
            """,
            preserve_superadmin_ids,
        )
    else:
        cur.execute("DELETE FROM users WHERE role = 'superadmin'")

    cur.execute("DELETE FROM teacher_subjects")
    cur.execute("DELETE FROM teachers")
    for card in teacher_cards:
        cur.execute(
            """
            INSERT INTO teachers (telegram_id, full_name, subject_name, description, photo_path)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                card.get("telegram_id"),
                card.get("full_name"),
                card.get("subject_name"),
                card.get("description"),
                card.get("photo_path"),
            ),
        )
        teacher_id = int(cur.lastrowid)
        cur.execute(
            """
            INSERT OR IGNORE INTO teacher_subjects (teacher_id, subject_name)
            VALUES (?, ?)
            """,
            (teacher_id, card.get("subject_name")),
        )

    # Drop legacy tables if they exist from old revisions.
    cur.execute("DROP TABLE IF EXISTS applications")
    cur.execute("DROP TABLE IF EXISTS lessons")
    cur.execute("DROP TABLE IF EXISTS payments")

    conn.commit()
    conn.close()

    return {
        "teachers_seeded": len(teacher_cards),
        "superadmins_preserved": len(preserve_superadmin_ids),
    }


def reset_system_data_keep_current_teachers(
    preserve_superadmin_ids: list[int] | tuple[int, ...] | None = None,
) -> dict:
    preserve_superadmin_ids = tuple(preserve_superadmin_ids or [])

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("BEGIN")

    cur.execute("DELETE FROM attendance")
    cur.execute("DELETE FROM balance_history")
    cur.execute("DELETE FROM student_lessons")
    cur.execute("DELETE FROM payment_requests")
    cur.execute("DELETE FROM admin_actions")
    cur.execute("DELETE FROM onboarding_invites")
    cur.execute("DELETE FROM debt_reminder_log")
    cur.execute("DELETE FROM debt_daily_snapshots")
    cur.execute("DELETE FROM debt_report_runs")
    cur.execute("DELETE FROM students")

    # Keep teacher profiles and their accounts; clear only admins/students.
    cur.execute("DELETE FROM users WHERE role IN ('student', 'admin')")

    if preserve_superadmin_ids:
        placeholders = ",".join("?" for _ in preserve_superadmin_ids)
        cur.execute(
            f"""
            DELETE FROM users
            WHERE role = 'superadmin'
              AND telegram_id NOT IN ({placeholders})
            """,
            preserve_superadmin_ids,
        )
    else:
        cur.execute("DELETE FROM users WHERE role = 'superadmin'")

    cur.execute("SELECT COUNT(1) FROM teachers")
    teachers_kept = int(cur.fetchone()[0] or 0)

    conn.commit()
    conn.close()

    return {
        "teachers_kept": teachers_kept,
        "superadmins_preserved": len(preserve_superadmin_ids),
    }


def reset_all_system_data() -> dict:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("BEGIN")

    table_order = [
        "attendance",
        "balance_history",
        "student_lessons",
        "payment_requests",
        "admin_actions",
        "onboarding_invites",
        "debt_reminder_log",
        "debt_daily_snapshots",
        "debt_report_runs",
        "teacher_subjects",
        "students",
        "teachers",
        "users",
        "known_telegram_users",
        "maintenance_runs",
    ]

    deleted: dict[str, int] = {}
    for table_name in table_order:
        cur.execute(f"DELETE FROM {table_name}")
        deleted[table_name] = int(cur.rowcount or 0)

    cur.execute("DROP TABLE IF EXISTS applications")
    cur.execute("DROP TABLE IF EXISTS lessons")
    cur.execute("DROP TABLE IF EXISTS payments")

    conn.commit()
    conn.close()
    return deleted


def run_startup_maintenance_from_env(preserve_superadmin_ids: list[int] | tuple[int, ...] | None = None) -> bool:
    if not _is_truthy_env(os.getenv("SCHOOL_RESET_STUDENT_DATA")):
        return False

    task_name = "reset_student_data_for_testing_v1"
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1
        FROM maintenance_runs
        WHERE task_name = ?
        """,
        (task_name,),
    )
    already_executed = cur.fetchone() is not None
    conn.close()

    if already_executed:
        return False

    reset_student_data_for_testing(preserve_superadmin_ids=preserve_superadmin_ids)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO maintenance_runs (task_name, executed_at)
        VALUES (?, ?)
        """,
        (task_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()
    return True


def add_student(
    full_name: str,
    telegram_id: int | None,
    phone: str | None,
    telegram_username: str | None = None,
):
    conn = get_connection()
    cur = conn.cursor()

    if telegram_id is not None:
        cur.execute(
            """
            SELECT id
            FROM students
            WHERE telegram_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (telegram_id,)
        )
        existing = cur.fetchone()

        if existing:
            cur.execute(
                """
                UPDATE students
                SET full_name = ?, phone = ?, telegram_username = COALESCE(?, telegram_username)
                WHERE id = ?
                """,
                (full_name, phone, telegram_username, existing[0])
            )
            conn.commit()
            conn.close()
            return existing[0]

    cur.execute(
        """
        INSERT INTO students (full_name, telegram_id, phone, telegram_username)
        VALUES (?, ?, ?, ?)
        """,
        (full_name, telegram_id, phone, telegram_username)
    )

    student_id = cur.lastrowid
    conn.commit()
    conn.close()
    return student_id


def get_all_students():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT id, full_name, telegram_id, phone FROM students ORDER BY id")
    rows = cur.fetchall()

    conn.close()
    return rows


def add_teacher_if_not_exists(full_name: str, telegram_id: int | None = None):
    conn = get_connection()
    cur = conn.cursor()

    if telegram_id is not None:
        cur.execute(
            "SELECT id FROM teachers WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = cur.fetchone()

        if row:
            cur.execute(
                """
                UPDATE teachers
                SET full_name = ?
                WHERE telegram_id = ?
                """,
                (full_name, telegram_id)
            )
            conn.commit()
            conn.close()
            return row[0]

    cur.execute(
        "SELECT id FROM teachers WHERE full_name = ?",
        (full_name,)
    )
    row = cur.fetchone()

    if row:
        conn.close()
        return row[0]

    cur.execute(
        """
        INSERT INTO teachers (telegram_id, full_name)
        VALUES (?, ?)
        """,
        (telegram_id, full_name)
    )

    teacher_id = cur.lastrowid
    conn.commit()
    conn.close()
    return teacher_id


def ensure_teacher_subject_link(teacher_id: int, subject_name: str):
    normalized_subject = (subject_name or "").strip()
    if not normalized_subject:
        return

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO teacher_subjects (teacher_id, subject_name)
        VALUES (?, ?)
        """,
        (teacher_id, normalized_subject),
    )
    conn.commit()
    conn.close()


def replace_teacher_subject_links(teacher_id: int, subject_name: str):
    normalized_subject = (subject_name or "").strip()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM teacher_subjects
        WHERE teacher_id = ?
        """,
        (teacher_id,),
    )
    if normalized_subject:
        cur.execute(
            """
            INSERT OR IGNORE INTO teacher_subjects (teacher_id, subject_name)
            VALUES (?, ?)
            """,
            (teacher_id, normalized_subject),
        )
    conn.commit()
    conn.close()


def add_or_update_teacher_profile(
    *,
    full_name: str,
    subject_name: str,
    telegram_id: int | None = None,
    description: str | None = None,
    photo_path: str | None = None,
) -> int:
    conn = get_connection()
    cur = conn.cursor()

    if telegram_id is not None:
        cur.execute(
            """
            UPDATE students
            SET telegram_id = NULL
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        )

    if telegram_id is not None:
        cur.execute(
            """
            SELECT id
            FROM teachers
            WHERE telegram_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (telegram_id,),
        )
        same_telegram_row = cur.fetchone()
        if same_telegram_row:
            teacher_id = int(same_telegram_row[0])
            cur.execute(
                """
                UPDATE teachers
                SET full_name = ?,
                    subject_name = ?,
                    description = COALESCE(?, description),
                    photo_path = COALESCE(?, photo_path)
                WHERE id = ?
                """,
                (full_name, subject_name, description, photo_path, teacher_id),
            )
            conn.commit()
            conn.close()
            ensure_teacher_subject_link(teacher_id, subject_name)
            return teacher_id

    cur.execute(
        """
        SELECT id
        FROM teachers
        WHERE full_name = ?
          AND COALESCE(subject_name, '') = COALESCE(?, '')
        ORDER BY id DESC
        LIMIT 1
        """,
        (full_name, subject_name),
    )
    row = cur.fetchone()

    if row:
        teacher_id = int(row[0])
        cur.execute(
            """
            UPDATE teachers
            SET telegram_id = COALESCE(?, telegram_id),
                description = COALESCE(?, description),
                photo_path = COALESCE(?, photo_path)
            WHERE id = ?
            """,
            (telegram_id, description, photo_path, teacher_id),
        )
        conn.commit()
        conn.close()
        ensure_teacher_subject_link(teacher_id, subject_name)
        return teacher_id

    cur.execute(
        """
        INSERT INTO teachers (telegram_id, full_name, subject_name, description, photo_path)
        VALUES (?, ?, ?, ?, ?)
        """,
        (telegram_id, full_name, subject_name, description, photo_path),
    )
    teacher_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    ensure_teacher_subject_link(teacher_id, subject_name)
    return teacher_id


def bind_teacher_telegram_id(full_name: str, telegram_id: int):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE students
        SET telegram_id = NULL
        WHERE telegram_id = ?
        """,
        (telegram_id,),
    )

    cur.execute(
        """
        SELECT id, full_name
        FROM teachers
        WHERE telegram_id = ?
        """,
        (telegram_id,)
    )
    existing_by_telegram = cur.fetchone()

    if existing_by_telegram and existing_by_telegram[1] != full_name:
        conn.close()
        return {
            "ok": False,
            "error": f"Этот Telegram ID уже привязан к преподавателю: {existing_by_telegram[1]}",
        }

    cur.execute(
        """
        SELECT id
        FROM teachers
        WHERE full_name = ?
        """,
        (full_name,)
    )
    existing_by_name = cur.fetchone()

    if existing_by_name:
        cur.execute(
            """
            UPDATE teachers
            SET telegram_id = ?
            WHERE id = ?
            """,
            (telegram_id, existing_by_name[0])
        )
        teacher_id = existing_by_name[0]
        action = "updated"
    else:
        cur.execute(
            """
            INSERT INTO teachers (telegram_id, full_name)
            VALUES (?, ?)
            """,
            (telegram_id, full_name)
        )
        teacher_id = cur.lastrowid
        action = "created"

    conn.commit()
    conn.close()
    return {"ok": True, "teacher_id": teacher_id, "action": action}


def bind_teacher_telegram_by_id(teacher_id: int, telegram_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE students
        SET telegram_id = NULL
        WHERE telegram_id = ?
        """,
        (telegram_id,),
    )
    cur.execute(
        """
        UPDATE teachers
        SET telegram_id = ?
        WHERE id = ?
        """,
        (telegram_id, teacher_id),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def get_teacher_by_telegram_id(telegram_id: int):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, telegram_id, full_name
        FROM teachers
        WHERE telegram_id = ?
        """,
        (telegram_id,)
    )
    row = cur.fetchone()

    conn.close()
    return row


def add_student_lesson(student_id: int, teacher_id: int, subject_name: str, lesson_balance: int, tariff_type: str):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO student_lessons (student_id, teacher_id, subject_name, lesson_balance, tariff_type)
        VALUES (?, ?, ?, ?, ?)
        """,
        (student_id, teacher_id, subject_name, lesson_balance, tariff_type)
    )
    new_lesson_id = cur.lastrowid

    if new_lesson_id and lesson_balance != 0:
        cur.execute(
            """
            INSERT INTO balance_history (
                student_lesson_id, operation_type, lessons_delta,
                comment, created_at, created_by
            ) VALUES (?, 'initial_balance', ?, ?, ?, ?)
            """,
            (
                new_lesson_id,
                lesson_balance,
                "Стартовый баланс при создании направления",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                None,
            ),
        )

    conn.commit()
    conn.close()


def find_students_by_name(search_text: str):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, full_name, telegram_id, phone
        FROM students
        WHERE full_name LIKE ?
        ORDER BY full_name
        """,
        (f"%{search_text}%",)
    )
    rows = cur.fetchall()

    conn.close()
    return rows


def find_students_by_name_with_username(search_text: str):
    conn = get_connection()
    cur = conn.cursor()

    normalized_query = (search_text or "").strip().lower().lstrip("@")
    pattern = f"%{normalized_query}%"

    cur.execute(
        """
        SELECT id, full_name, telegram_id, phone, telegram_username
        FROM students
        WHERE LOWER(full_name) LIKE ?
           OR LOWER(COALESCE(telegram_username, '')) LIKE ?
        ORDER BY full_name
        """,
        (pattern, pattern)
    )
    rows = cur.fetchall()

    conn.close()
    return rows


def find_teacher_students_by_name(teacher_telegram_id: int, search_text: str):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT DISTINCT
            s.id,
            s.full_name,
            s.telegram_id,
            s.phone
        FROM student_lessons sl
        JOIN students s ON sl.student_id = s.id
        JOIN teachers t ON sl.teacher_id = t.id
        WHERE t.telegram_id = ?
          AND s.full_name LIKE ?
        ORDER BY s.full_name
        """,
        (teacher_telegram_id, f"%{search_text}%")
    )
    rows = cur.fetchall()

    conn.close()
    return rows


def get_students_by_teacher_telegram_id(telegram_id: int):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT DISTINCT
            s.id,
            s.full_name,
            s.telegram_id,
            s.phone,
            s.telegram_username
        FROM student_lessons sl
        JOIN students s ON sl.student_id = s.id
        JOIN teachers t ON sl.teacher_id = t.id
        WHERE t.telegram_id = ?
        ORDER BY s.full_name
        """,
        (telegram_id,)
    )
    rows = cur.fetchall()

    conn.close()
    return rows


def get_student_directions(student_id: int):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT sl.id, t.full_name, sl.subject_name, sl.lesson_balance, sl.tariff_type
        FROM student_lessons sl
        JOIN teachers t ON sl.teacher_id = t.id
        WHERE sl.student_id = ?
        ORDER BY sl.id
        """,
        (student_id,)
    )
    rows = cur.fetchall()

    conn.close()
    return rows


def get_student_lesson_by_id(direction_id: int):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT sl.id, sl.student_id, sl.teacher_id, sl.subject_name, sl.lesson_balance, sl.tariff_type,
               s.full_name, t.full_name
        FROM student_lessons sl
        JOIN students s ON sl.student_id = s.id
        JOIN teachers t ON sl.teacher_id = t.id
        WHERE sl.id = ?
        """,
        (direction_id,)
    )
    row = cur.fetchone()

    conn.close()
    return row


def add_balance_history(student_lesson_id: int, operation_type: str, lessons_delta: int, comment: str | None, created_by: int | None, amount_paid: int = 0):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO balance_history (student_lesson_id, operation_type, lessons_delta, comment, created_at, created_by, amount_paid)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            student_lesson_id,
            operation_type,
            lessons_delta,
            comment,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            created_by,
            amount_paid,
        )
    )

    conn.commit()
    conn.close()


def get_balance_history_by_student(student_id: int):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            bh.id,
            s.full_name,
            t.full_name,
            sl.subject_name,
            bh.operation_type,
            bh.lessons_delta,
            bh.comment,
            bh.created_at,
            bh.created_by
        FROM balance_history bh
        JOIN student_lessons sl ON bh.student_lesson_id = sl.id
        JOIN students s ON sl.student_id = s.id
        JOIN teachers t ON sl.teacher_id = t.id
        WHERE sl.student_id = ?
        ORDER BY bh.id DESC
        """,
        (student_id,)
    )
    rows = cur.fetchall()

    conn.close()
    return rows


def get_attendance_summary_for_student(student_id: int) -> list[dict]:
    """Per-direction attendance aggregates used in the student cabinet."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            sl.id,
            sl.subject_name,
            t.full_name,
            COALESCE(SUM(CASE WHEN a.status IN ('present','completed') THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN a.status IN ('absent','missed','skipped') THEN 1 ELSE 0 END), 0),
            COALESCE(COUNT(a.id), 0),
            MAX(a.lesson_date)
        FROM student_lessons sl
        JOIN teachers t ON t.id = sl.teacher_id
        LEFT JOIN attendance a ON a.student_lesson_id = sl.id
        WHERE sl.student_id = ?
        GROUP BY sl.id, sl.subject_name, t.full_name
        ORDER BY sl.id
        """,
        (int(student_id),),
    )
    rows = cur.fetchall()
    conn.close()
    result: list[dict] = []
    for row in rows:
        result.append(
            {
                "direction_id": int(row[0]),
                "subject_name": (row[1] or "").strip() or "-",
                "teacher_name": (row[2] or "").strip() or "-",
                "attended": int(row[3] or 0),
                "missed": int(row[4] or 0),
                "total": int(row[5] or 0),
                "last_lesson_date": row[6],
            }
        )
    return result


def get_recent_attendance_for_student(student_id: int, limit: int = 5) -> list[dict]:
    """Most recent attendance entries across all directions of a student."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            a.lesson_date,
            a.status,
            sl.subject_name,
            t.full_name
        FROM attendance a
        JOIN student_lessons sl ON sl.id = a.student_lesson_id
        JOIN teachers t ON t.id = sl.teacher_id
        WHERE sl.student_id = ?
        ORDER BY a.id DESC
        LIMIT ?
        """,
        (int(student_id), int(limit)),
    )
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "lesson_date": row[0],
            "status": row[1],
            "subject_name": (row[2] or "").strip() or "-",
            "teacher_name": (row[3] or "").strip() or "-",
        }
        for row in rows
    ]


def mark_attendance(direction_id: int, status: str, marked_by: int) -> int:
    """Insert an attendance record and return the new attendance row id."""
    conn = get_connection()
    cur = conn.cursor()

    lesson_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        INSERT INTO attendance (student_lesson_id, lesson_date, status, written_off, marked_by)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            direction_id,
            lesson_datetime,
            status,
            1 if status == "present" else 0,
            marked_by
        )
    )
    attendance_id = cur.lastrowid

    if status == "present":
        cur.execute(
            """
            UPDATE student_lessons
            SET lesson_balance = lesson_balance - 1
            WHERE id = ?
            """,
            (direction_id,)
        )

        cur.execute(
            """
            INSERT INTO balance_history (student_lesson_id, operation_type, lessons_delta, comment, created_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                direction_id,
                "attendance_writeoff",
                -1,
                "Списание за посещение",
                lesson_datetime,
                marked_by
            )
        )

    conn.commit()
    conn.close()
    return attendance_id




def has_recent_attendance(direction_id: int, within_minutes: int = 5) -> bool:
    """Return True if a present/completed attendance was recorded within the last N minutes."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*) FROM attendance
        WHERE student_lesson_id = ?
          AND status IN ('present', 'completed')
          AND lesson_date >= ?
        """,
        (
            direction_id,
            (datetime.now() - timedelta(minutes=within_minutes)).strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    count = cur.fetchone()[0]
    conn.close()
    return count > 0

def add_lessons_to_balance(direction_id: int, lessons_count: int, created_by: int | None = None, comment: str | None = None, amount_paid: int = 0):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE student_lessons
        SET lesson_balance = lesson_balance + ?
        WHERE id = ?
        """,
        (lessons_count, direction_id)
    )

    cur.execute(
        """
        INSERT INTO balance_history (student_lesson_id, operation_type, lessons_delta, comment, created_at, created_by, amount_paid)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            direction_id,
            "manual_topup",
            lessons_count,
            comment or "Начисление занятий",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            created_by,
            amount_paid,
        )
    )

    conn.commit()
    conn.close()


def create_payment_request(
    telegram_user_id: int | None,
    telegram_username: str | None,
    telegram_full_name: str | None,
    caption_text: str | None,
    file_id: str,
    file_type: str,
    preferred_direction_id: int | None = None,
    promo_code_id_used: int | None = None,
):
    conn = get_connection()
    cur = conn.cursor()
    _MSK = timezone(timedelta(hours=3))
    now = datetime.now(_MSK).strftime("%Y-%m-%d %H:%M:%S")

    cur.execute(
        """
        INSERT INTO payment_requests (
            telegram_user_id,
            telegram_username,
            telegram_full_name,
            caption_text,
            file_id,
            file_type,
            status,
            created_at,
            updated_at,
            preferred_direction_id,
            promo_code_id_used
        )
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
        """,
        (
            telegram_user_id,
            telegram_username,
            telegram_full_name,
            caption_text,
            file_id,
            file_type,
            now,
            now,
            preferred_direction_id,
            promo_code_id_used,
        )
    )

    payment_request_id = cur.lastrowid
    conn.commit()
    conn.close()
    return payment_request_id


def get_payment_request_by_id(payment_request_id: int):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, telegram_user_id, telegram_username, telegram_full_name,
               caption_text, file_id, file_type, status, approved_by,
               rejected_by, created_at, updated_at, preferred_direction_id,
               promo_code_id_used
        FROM payment_requests
        WHERE id = ?
        """,
        (payment_request_id,)
    )
    row = cur.fetchone()

    conn.close()
    return row


def get_student_by_id(student_id: int):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, full_name, telegram_id, phone
        FROM students
        WHERE id = ?
        """,
        (student_id,)
    )
    row = cur.fetchone()

    conn.close()
    return row


def get_student_by_id_with_username(student_id: int):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, full_name, telegram_id, phone, telegram_username
        FROM students
        WHERE id = ?
        """,
        (student_id,)
    )
    row = cur.fetchone()

    conn.close()
    return row


def get_recent_payment_history_by_telegram_user(telegram_user_id: int, limit: int = 4):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            pr.id,
            pr.status,
            pr.caption_text,
            pr.created_at,
            pr.updated_at,
            COALESCE(
                (
                    SELECT SUM(bh.lessons_delta)
                    FROM balance_history bh
                    WHERE bh.operation_type = 'manual_topup'
                      AND COALESCE(bh.comment, '') LIKE '%#' || CAST(pr.id AS TEXT) || '%'
                ),
                0
            ) AS lessons_added
        FROM payment_requests pr
        WHERE pr.telegram_user_id = ?
        ORDER BY pr.id DESC
        LIMIT ?
        """,
        (telegram_user_id, limit),
    )
    rows = cur.fetchall()

    conn.close()
    return rows




def get_recent_payment_history_by_student_id(student_id: int, limit: int = 4) -> list[tuple]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            pr.id, pr.status, pr.caption_text, pr.created_at, pr.updated_at,
            COALESCE(
                (
                    SELECT SUM(bh.lessons_delta)
                    FROM balance_history bh
                    WHERE bh.operation_type = 'manual_topup'
                      AND COALESCE(bh.comment, '') LIKE '%#' || CAST(pr.id AS TEXT) || '%'
                ),
                0
            ) AS lessons_added
        FROM payment_requests pr
        JOIN students s ON s.telegram_id = pr.telegram_user_id
        WHERE s.id = ?
        ORDER BY pr.id DESC
        LIMIT ?
        """,
        (student_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return rows

def update_payment_request_status(payment_request_id: int, status: str, admin_id: int | None = None):
    conn = get_connection()
    cur = conn.cursor()

    approved_by = admin_id if status == "approved" else None
    rejected_by = admin_id if status == "rejected" else None

    cur.execute(
        """
        UPDATE payment_requests
        SET status = ?,
            approved_by = COALESCE(?, approved_by),
            rejected_by = COALESCE(?, rejected_by),
            updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            approved_by,
            rejected_by,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            payment_request_id
        )
    )

    conn.commit()
    conn.close()
    ensure_teacher_subject_link(teacher_id, subject_name)


def get_admin_dashboard_metrics() -> dict:
    """Aggregate metrics shown on the admin dashboard.

    Cheap to compute (one short query per metric) so it can be called every
    time admin opens the dashboard screen.
    """
    conn = get_connection()
    cur = conn.cursor()

    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    cur.execute(
        """
        SELECT status, COUNT(*)
        FROM payment_requests
        WHERE status IN ('pending', 'processing')
        GROUP BY status
        """
    )
    pending_by_status = {row[0]: int(row[1]) for row in cur.fetchall()}

    cur.execute(
        """
        SELECT COUNT(DISTINCT s.id), COALESCE(SUM(ABS(sl.lesson_balance)), 0)
        FROM student_lessons sl
        JOIN students s ON s.id = sl.student_id
        WHERE sl.lesson_balance < 0
        """
    )
    debt_row = cur.fetchone() or (0, 0)
    debtors_count = int(debt_row[0] or 0)
    debt_lessons_total = int(debt_row[1] or 0)

    cur.execute(
        """
        SELECT COUNT(*)
        FROM attendance
        WHERE lesson_date >= ?
          AND status IN ('present', 'completed')
        """,
        (week_ago,),
    )
    lessons_week = int((cur.fetchone() or (0,))[0] or 0)

    # New students = those whose first balance_history row landed in last 7d
    # (we don't have students.created_at). Diagnostic_lesson is the very first
    # row added for any new student.
    cur.execute(
        """
        SELECT COUNT(*)
        FROM balance_history
        WHERE operation_type = 'diagnostic_lesson'
          AND created_at >= ?
        """,
        (week_ago,),
    )
    new_students_week = int((cur.fetchone() or (0,))[0] or 0)

    cur.execute(
        """
        SELECT status, COUNT(*)
        FROM referrals
        GROUP BY status
        """
    )
    referrals_by_status = {row[0]: int(row[1]) for row in cur.fetchall()}

    cur.execute(
        """
        SELECT COUNT(*)
        FROM payment_requests
        WHERE status = 'expired'
          AND COALESCE(updated_at, created_at) >= ?
        """,
        (week_ago,),
    )
    expired_week = int((cur.fetchone() or (0,))[0] or 0)

    conn.close()

    return {
        "payments_pending": pending_by_status.get("pending", 0),
        "payments_processing": pending_by_status.get("processing", 0),
        "debtors_count": debtors_count,
        "debt_lessons_total": debt_lessons_total,
        "lessons_attended_week": lessons_week,
        "new_students_week": new_students_week,
        "referrals_captured": referrals_by_status.get("captured", 0),
        "referrals_linked": referrals_by_status.get("student_linked", 0),
        "referrals_rewarded": referrals_by_status.get("rewarded", 0),
        "payments_expired_week": expired_week,
    }


def get_stale_pending_payment_requests(older_than_days: int = 30) -> list[tuple]:
    """Find payment_requests still in pending/processing older than N days.

    Returns rows shaped like get_payment_request_by_id, suitable to feed back
    into try_transition_payment_request_status + DM the student.
    """
    if older_than_days <= 0:
        return []
    cutoff = (datetime.now() - timedelta(days=older_than_days)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, telegram_user_id, telegram_username, telegram_full_name,
               caption_text, file_id, file_type, status, approved_by,
               rejected_by, created_at, updated_at
        FROM payment_requests
        WHERE status IN ('pending', 'processing')
          AND created_at < ?
        ORDER BY id ASC
        """,
        (cutoff,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def try_transition_payment_request_status(
    payment_request_id: int,
    allowed_from_statuses: list[str],
    new_status: str,
    admin_id: int | None = None
) -> bool:
    if not allowed_from_statuses:
        return False

    conn = get_connection()
    cur = conn.cursor()

    approved_by = admin_id if new_status == "approved" else None
    rejected_by = admin_id if new_status == "rejected" else None
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    placeholders = ", ".join("?" for _ in allowed_from_statuses)
    params = [
        new_status,
        approved_by,
        rejected_by,
        now,
        payment_request_id,
        *allowed_from_statuses,
    ]

    cur.execute(
        f"""
        UPDATE payment_requests
        SET status = ?,
            approved_by = COALESCE(?, approved_by),
            rejected_by = COALESCE(?, rejected_by),
            updated_at = ?
        WHERE id = ?
          AND status IN ({placeholders})
        """,
        params
    )

    success = cur.rowcount > 0
    conn.commit()
    conn.close()
    return success


def finalize_payment_with_topup(
    payment_request_id: int,
    direction_id: int,
    lessons_count: int,
    admin_id: int,
    comment: str | None = None,
    amount_paid: int = 0,
) -> bool:
    if lessons_count <= 0:
        return False

    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT status
            FROM payment_requests
            WHERE id = ?
            """,
            (payment_request_id,)
        )
        payment_row = cur.fetchone()
        if not payment_row:
            conn.rollback()
            return False

        current_status = payment_row[0]
        if current_status != "processing":
            conn.rollback()
            return False

        cur.execute(
            """
            UPDATE student_lessons
            SET lesson_balance = lesson_balance + ?
            WHERE id = ?
            """,
            (lessons_count, direction_id)
        )
        if cur.rowcount == 0:
            conn.rollback()
            return False

        _MSK = timezone(timedelta(hours=3))
        now = datetime.now(_MSK).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            INSERT INTO balance_history (student_lesson_id, operation_type, lessons_delta, comment, created_at, created_by, amount_paid)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                direction_id,
                "manual_topup",
                lessons_count,
                comment or f"Начисление после подтверждения оплаты #{payment_request_id}",
                now,
                admin_id,
                amount_paid,
            )
        )

        cur.execute(
            """
            UPDATE payment_requests
            SET status = 'approved',
                approved_by = ?,
                updated_at = ?
            WHERE id = ?
              AND status = 'processing'
            """,
            (admin_id, now, payment_request_id)
        )
        if cur.rowcount == 0:
            conn.rollback()
            return False

        # Mark promo as used only if it was actually applied to this payment
        cur.execute(
            """
            SELECT pr.promo_code_id_used, COALESCE(s.id, s2.id)
            FROM payment_requests pr
            LEFT JOIN students s ON s.telegram_id = pr.telegram_user_id
            LEFT JOIN students s2 ON s2.max_id = pr.max_user_id
            WHERE pr.id = ?
            LIMIT 1
            """,
            (payment_request_id,)
        )
        promo_pay_row = cur.fetchone()
        if promo_pay_row and promo_pay_row[0] and promo_pay_row[1]:
            promo_id_used = promo_pay_row[0]
            student_id_for_promo = promo_pay_row[1]
            cur.execute(
                "UPDATE promo_codes SET used_count = used_count + 1 WHERE id = ?",
                (promo_id_used,)
            )
            cur.execute(
                "UPDATE student_promo_codes SET used_at = CURRENT_TIMESTAMP WHERE student_id = ? AND promo_code_id = ?",
                (student_id_for_promo, promo_id_used)
            )

        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def find_students_by_telegram_id(telegram_user_id: int):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, full_name, telegram_id, phone
        FROM students
        WHERE telegram_id = ?
        ORDER BY id DESC
        """,
        (telegram_user_id,)
    )
    rows = cur.fetchall()

    conn.close()
    return rows


def add_user(
    telegram_id: int,
    full_name: str,
    role: str,
    telegram_username: str | None = None,
):
    conn = get_connection()
    cur = conn.cursor()

    # One Telegram account must not be simultaneously bound as both student and teacher.
    if role == "teacher":
        cur.execute(
            """
            UPDATE students
            SET telegram_id = NULL
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        )
    elif role == "student":
        cur.execute(
            """
            UPDATE teachers
            SET telegram_id = NULL
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        )

    cur.execute(
        """
        INSERT INTO users (telegram_id, full_name, role, is_active, created_at, telegram_username)
        VALUES (?, ?, ?, 1, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            full_name = excluded.full_name,
            role = excluded.role,
            is_active = 1,
            telegram_username = COALESCE(excluded.telegram_username, users.telegram_username)
        """,
        (
            telegram_id,
            full_name,
            role,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            telegram_username,
        )
    )

    conn.commit()
    conn.close()


def normalize_telegram_username(username: str | None) -> str | None:
    if not username:
        return None
    cleaned = username.strip().lstrip("@").lower()
    return cleaned or None


def upsert_known_telegram_user(
    *,
    telegram_id: int,
    telegram_username: str | None,
    full_name: str | None,
):
    normalized_username = normalize_telegram_username(telegram_username)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO known_telegram_users (
            telegram_id, telegram_username, full_name, last_seen_at
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            telegram_username = COALESCE(excluded.telegram_username, known_telegram_users.telegram_username),
            full_name = COALESCE(excluded.full_name, known_telegram_users.full_name),
            last_seen_at = excluded.last_seen_at
        """,
        (
            telegram_id,
            normalized_username,
            full_name,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    conn.close()


def get_known_telegram_user_id_by_username(telegram_username: str | None) -> int | None:
    normalized_username = normalize_telegram_username(telegram_username)
    if not normalized_username:
        return None

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT telegram_id
        FROM known_telegram_users
        WHERE telegram_username = ?
        ORDER BY last_seen_at DESC
        LIMIT 1
        """,
        (normalized_username,),
    )
    row = cur.fetchone()
    conn.close()
    return int(row[0]) if row and row[0] is not None else None


def create_onboarding_invite(
    *,
    role: str,
    full_name: str,
    telegram_username: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    created_by: int | None = None,
) -> str:
    normalized_username = normalize_telegram_username(telegram_username)
    token = secrets.token_urlsafe(18)
    conn = get_connection()
    cur = conn.cursor()

    # Reuse an existing pending invite for the same target to avoid duplicates.
    cur.execute(
        """
        SELECT token
        FROM onboarding_invites
        WHERE role = ?
          AND telegram_username = ?
          AND COALESCE(entity_type, '') = COALESCE(?, '')
          AND COALESCE(entity_id, -1) = COALESCE(?, -1)
          AND used_by_telegram_id IS NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (role, normalized_username, entity_type, entity_id),
    )
    existing = cur.fetchone()
    if existing and existing[0]:
        conn.close()
        return str(existing[0])

    cur.execute(
        """
        INSERT INTO onboarding_invites (
            token, role, full_name, telegram_username, entity_type, entity_id, created_by, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            token,
            role,
            full_name,
            normalized_username,
            entity_type,
            entity_id,
            created_by,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    conn.close()
    return token


def get_latest_pending_invite_by_role_and_username(role: str, telegram_username: str | None):
    normalized_username = normalize_telegram_username(telegram_username)
    if not normalized_username:
        return None
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, token, role, full_name, telegram_username, entity_type, entity_id
        FROM onboarding_invites
        WHERE role = ?
          AND telegram_username = ?
          AND used_by_telegram_id IS NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (role, normalized_username),
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_onboarding_invite_by_token(token: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, token, role, full_name, telegram_username, entity_type, entity_id, used_by_telegram_id
        FROM onboarding_invites
        WHERE token = ?
        LIMIT 1
        """,
        (token,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def mark_onboarding_invite_used(invite_id: int, telegram_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE onboarding_invites
        SET used_by_telegram_id = ?,
            used_at = ?
        WHERE id = ?
        """,
        (telegram_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), invite_id),
    )
    conn.commit()
    conn.close()


REFERRAL_INVITEE_DISCOUNT_PERCENT = 20
REFERRAL_INVITER_BONUS_LESSONS = 1


def capture_referral(inviter_telegram_id: int, invitee_telegram_id: int) -> bool:
    """Record a referral the moment an invitee opens /start ref_<inviter>.

    Idempotent: silently skips self-referrals and any case where the invitee
    already has a referral row (first inviter wins). Returns True if a new row
    was inserted, False otherwise.
    """
    if not inviter_telegram_id or not invitee_telegram_id:
        return False
    if int(inviter_telegram_id) == int(invitee_telegram_id):
        return False

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT 1 FROM referrals WHERE invitee_telegram_id = ? LIMIT 1",
            (int(invitee_telegram_id),),
        )
        if cur.fetchone():
            return False

        cur.execute(
            """
            INSERT INTO referrals (
                inviter_telegram_id,
                invitee_telegram_id,
                status,
                created_at
            ) VALUES (?, ?, 'captured', ?)
            """,
            (
                int(inviter_telegram_id),
                int(invitee_telegram_id),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def get_referral_by_invitee_telegram_id(invitee_telegram_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, inviter_telegram_id, invitee_telegram_id,
               invitee_student_id, status, created_at, rewarded_at,
               reward_balance_history_id
        FROM referrals
        WHERE invitee_telegram_id = ?
        LIMIT 1
        """,
        (int(invitee_telegram_id),),
    )
    row = cur.fetchone()
    conn.close()
    return row


def link_invitee_student(invitee_telegram_id: int, student_id: int) -> bool:
    """Tie a captured referral to the freshly-created student card and also
    backfill students.referred_by_telegram_id for fast lookup.
    """
    referral = get_referral_by_invitee_telegram_id(invitee_telegram_id)
    if not referral:
        return False

    referral_id, inviter_tg, _, existing_student_id, status, *_ = referral
    if status not in ("captured", "student_linked"):
        return False

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE referrals
            SET invitee_student_id = ?,
                status = CASE
                    WHEN status = 'captured' THEN 'student_linked'
                    ELSE status
                END
            WHERE id = ?
            """,
            (int(student_id), int(referral_id)),
        )
        cur.execute(
            """
            UPDATE students
            SET referred_by_telegram_id = ?
            WHERE id = ?
            """,
            (int(inviter_tg), int(student_id)),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def get_active_invitee_discount_percent(student_id: int) -> int | None:
    """Return discount percent for an invitee whose first paid lesson hasn't
    been counted yet. None when no discount applies.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT referred_by_telegram_id, first_paid_at
        FROM students
        WHERE id = ?
        LIMIT 1
        """,
        (int(student_id),),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    referred_by, first_paid_at = row
    if not referred_by:
        return None
    if first_paid_at:
        return None
    return REFERRAL_INVITEE_DISCOUNT_PERCENT


def get_oldest_direction_for_telegram_id(telegram_id: int):
    """Return the oldest student_lesson row owned by the student bound to this
    telegram_id (the row a referral bonus should be credited to). Tuple shape
    matches get_student_lesson_by_id: (id, student_id, teacher_id,
    subject_name, lesson_balance, tariff_type, student_name, teacher_name).
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT sl.id, sl.student_id, sl.teacher_id, sl.subject_name,
               sl.lesson_balance, sl.tariff_type, s.full_name, t.full_name
        FROM student_lessons sl
        JOIN students s ON s.id = sl.student_id
        JOIN teachers t ON t.id = sl.teacher_id
        WHERE s.telegram_id = ?
        ORDER BY sl.id ASC
        LIMIT 1
        """,
        (int(telegram_id),),
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_oldest_direction_for_student_id(student_id: int):
    """Return the oldest student_lesson row for a student_id."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT sl.id, sl.student_id, sl.teacher_id, sl.subject_name,
               sl.lesson_balance, sl.tariff_type, s.full_name, t.full_name
        FROM student_lessons sl
        JOIN students s ON s.id = sl.student_id
        JOIN teachers t ON t.id = sl.teacher_id
        WHERE sl.student_id = ?
        ORDER BY sl.id ASC
        LIMIT 1
        """,
        (int(student_id),),
    )
    row = cur.fetchone()
    conn.close()
    return row


def attach_first_payment(student_id: int, payment_request_id: int) -> bool:
    """Mark the very first paid payment for the student. No-op if already set."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE students
            SET first_paid_at = ?,
                first_paid_payment_id = ?
            WHERE id = ?
              AND first_paid_at IS NULL
            """,
            (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                int(payment_request_id),
                int(student_id),
            ),
        )
        changed = cur.rowcount > 0
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def award_referral_bonus_to_inviter(
    invitee_student_id: int,
    bonus_lessons: int = REFERRAL_INVITER_BONUS_LESSONS,
    admin_id: int | None = None,
) -> dict | None:
    """Find the referral row for this invitee, credit `bonus_lessons` to the
    inviter's oldest direction, write balance_history, flip referral status to
    'rewarded'. Idempotent: returns None if no eligible referral exists or if
    the inviter has no directions yet.

    Returns a dict describing what was credited (used to compose the
    notification): {inviter_telegram_id, direction_id, subject_name,
    teacher_name, lessons_added}. The caller is responsible for sending the
    Telegram message — this function only touches the DB.
    """
    if bonus_lessons <= 0:
        return None

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, inviter_telegram_id, status
        FROM referrals
        WHERE invitee_student_id = ?
          AND status = 'student_linked'
        LIMIT 1
        """,
        (int(invitee_student_id),),
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return None
    referral_id, inviter_tg, _ = row

    inviter_direction = get_oldest_direction_for_telegram_id(int(inviter_tg))
    if not inviter_direction:
        return None

    direction_id = int(inviter_direction[0])
    subject_name = inviter_direction[3]
    teacher_name = inviter_direction[7]

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE student_lessons
            SET lesson_balance = lesson_balance + ?
            WHERE id = ?
            """,
            (int(bonus_lessons), direction_id),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return None

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            INSERT INTO balance_history (
                student_lesson_id, operation_type, lessons_delta,
                comment, created_at, created_by
            ) VALUES (?, 'referral_inviter_bonus', ?, ?, ?, ?)
            """,
            (
                direction_id,
                int(bonus_lessons),
                f"Реферальный бонус за приглашённого ученика #{int(invitee_student_id)}",
                now,
                admin_id,
            ),
        )
        bonus_history_id = cur.lastrowid

        cur.execute(
            """
            UPDATE referrals
            SET status = 'rewarded',
                rewarded_at = ?,
                reward_balance_history_id = ?
            WHERE id = ?
              AND status = 'student_linked'
            """,
            (now, bonus_history_id, int(referral_id)),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return None

        conn.commit()
        return {
            "inviter_telegram_id": int(inviter_tg),
            "direction_id": direction_id,
            "subject_name": subject_name,
            "teacher_name": teacher_name,
            "lessons_added": int(bonus_lessons),
        }
    except Exception:
        conn.rollback()
        return None
    finally:
        conn.close()


def bind_student_telegram_by_id(
    student_id: int,
    telegram_id: int,
    telegram_username: str | None = None,
) -> bool:
    normalized_username = normalize_telegram_username(telegram_username)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE students
        SET telegram_id = ?,
            telegram_username = COALESCE(?, telegram_username)
        WHERE id = ?
        """,
        (telegram_id, normalized_username, student_id),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def get_latest_student_by_username(telegram_username: str | None):
    normalized_username = normalize_telegram_username(telegram_username)
    if not normalized_username:
        return None
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, full_name, telegram_id, phone
        FROM students
        WHERE telegram_username = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (normalized_username,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def _cleanup_teacher_profiles_for_non_teacher_users(cur):
    cur.execute(
        """
        SELECT telegram_id, full_name
        FROM users
        WHERE role <> 'teacher'
          AND telegram_id IS NOT NULL
        """
    )
    rows = cur.fetchall()
    for row in rows:
        if not row or row[0] is None:
            continue
        _delete_teacher_entities_for_user(cur, int(row[0]), row[1] if len(row) > 1 else None)


def _delete_teacher_entities_for_user(cur, telegram_id: int, full_name: str | None):
    teacher_ids: set[int] = set()

    cur.execute(
        """
        SELECT id
        FROM teachers
        WHERE telegram_id = ?
        """,
        (telegram_id,),
    )
    teacher_ids.update(int(row[0]) for row in cur.fetchall() if row and row[0] is not None)

    if full_name:
        cur.execute(
            """
            SELECT id
            FROM teachers
            WHERE full_name = ?
              AND (telegram_id IS NULL OR telegram_id = ?)
            """,
            (full_name, telegram_id),
        )
        teacher_ids.update(int(row[0]) for row in cur.fetchall() if row and row[0] is not None)

    for teacher_id in teacher_ids:
        cur.execute("DELETE FROM teacher_subjects WHERE teacher_id = ?", (teacher_id,))
        cur.execute("SELECT id FROM student_lessons WHERE teacher_id = ?", (teacher_id,))
        lesson_ids = [int(row[0]) for row in cur.fetchall() if row and row[0] is not None]

        for lesson_id in lesson_ids:
            cur.execute("DELETE FROM attendance WHERE student_lesson_id = ?", (lesson_id,))
            cur.execute("DELETE FROM balance_history WHERE student_lesson_id = ?", (lesson_id,))

        cur.execute("DELETE FROM student_lessons WHERE teacher_id = ?", (teacher_id,))
        cur.execute("DELETE FROM teachers WHERE id = ?", (teacher_id,))


def _delete_student_entities_for_user(cur, telegram_id: int, full_name: str | None):
    student_ids: set[int] = set()

    cur.execute(
        """
        SELECT id
        FROM students
        WHERE telegram_id = ?
        """,
        (telegram_id,),
    )
    student_ids.update(int(row[0]) for row in cur.fetchall() if row and row[0] is not None)

    if full_name:
        cur.execute(
            """
            SELECT id
            FROM students
            WHERE full_name = ?
              AND (telegram_id IS NULL OR telegram_id = ?)
            """,
            (full_name, telegram_id),
        )
        student_ids.update(int(row[0]) for row in cur.fetchall() if row and row[0] is not None)

    for student_id in student_ids:
        cur.execute("SELECT id FROM student_lessons WHERE student_id = ?", (student_id,))
        lesson_ids = [int(row[0]) for row in cur.fetchall() if row and row[0] is not None]

        for lesson_id in lesson_ids:
            cur.execute("DELETE FROM attendance WHERE student_lesson_id = ?", (lesson_id,))
            cur.execute("DELETE FROM balance_history WHERE student_lesson_id = ?", (lesson_id,))

        cur.execute("DELETE FROM student_lessons WHERE student_id = ?", (student_id,))
        cur.execute("DELETE FROM students WHERE id = ?", (student_id,))

    cur.execute(
        """
        DELETE FROM payment_requests
        WHERE telegram_user_id = ?
        """,
        (telegram_id,),
    )


def update_user_role(telegram_id: int, role: str) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT full_name, role
        FROM users
        WHERE telegram_id = ?
        LIMIT 1
        """,
        (telegram_id,),
    )
    existing_user = cur.fetchone()
    previous_full_name = existing_user[0] if existing_user else None
    previous_role = existing_user[1] if existing_user else None

    if previous_role != role:
        if role == "teacher":
            _delete_student_entities_for_user(cur, telegram_id, previous_full_name)
        elif role == "student":
            _delete_teacher_entities_for_user(cur, telegram_id, previous_full_name)
        elif role == "admin":
            _delete_teacher_entities_for_user(cur, telegram_id, previous_full_name)
            _delete_student_entities_for_user(cur, telegram_id, previous_full_name)

    cur.execute(
        """
        UPDATE users
        SET role = ?, is_active = 1
        WHERE telegram_id = ?
        """,
        (role, telegram_id),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def set_user_active(telegram_id: int, is_active: bool) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE users
        SET is_active = ?
        WHERE telegram_id = ?
        """,
        (1 if is_active else 0, telegram_id),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def resolve_student_teacher_telegram_conflicts() -> dict:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("BEGIN")

    cur.execute(
        """
        SELECT DISTINCT s.telegram_id
        FROM students s
        JOIN teachers t ON t.telegram_id = s.telegram_id
        WHERE s.telegram_id IS NOT NULL
        """
    )
    conflicted_ids = [int(row[0]) for row in cur.fetchall() if row and row[0] is not None]

    detached_from_students = 0
    detached_from_teachers = 0

    for telegram_id in conflicted_ids:
        cur.execute(
            """
            SELECT role
            FROM users
            WHERE telegram_id = ?
            LIMIT 1
            """,
            (telegram_id,),
        )
        user_row = cur.fetchone()
        preferred_role = user_row[0] if user_row else "teacher"

        if preferred_role == "student":
            cur.execute(
                """
                UPDATE teachers
                SET telegram_id = NULL
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            )
            detached_from_teachers += cur.rowcount
        else:
            cur.execute(
                """
                UPDATE students
                SET telegram_id = NULL
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            )
            detached_from_students += cur.rowcount

    conn.commit()
    conn.close()
    return {
        "conflicted_telegram_ids": len(conflicted_ids),
        "detached_from_students": detached_from_students,
        "detached_from_teachers": detached_from_teachers,
    }


def delete_admin_by_telegram_id(telegram_id: int) -> dict:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("BEGIN")

    cur.execute(
        """
        DELETE FROM users
        WHERE telegram_id = ? AND role = 'admin'
        """,
        (telegram_id,),
    )
    deleted_users = cur.rowcount

    conn.commit()
    conn.close()
    return {
        "ok": deleted_users > 0,
        "deleted_users": deleted_users,
    }


def delete_teacher_by_telegram_id(telegram_id: int) -> dict:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("BEGIN")

    cur.execute(
        """
        SELECT full_name
        FROM users
        WHERE telegram_id = ? AND role = 'teacher'
        LIMIT 1
        """,
        (telegram_id,),
    )
    user_row = cur.fetchone()
    teacher_full_name = user_row[0] if user_row else None

    cur.execute(
        """
        SELECT id
        FROM teachers
        WHERE telegram_id = ?
        """,
        (telegram_id,),
    )
    teacher_ids = {row[0] for row in cur.fetchall()}

    if teacher_full_name:
        cur.execute(
            """
            SELECT id
            FROM teachers
            WHERE full_name = ?
            """,
            (teacher_full_name,),
        )
        teacher_ids.update(row[0] for row in cur.fetchall())

    deleted_lessons = 0
    deleted_attendance = 0
    deleted_balance_history = 0
    deleted_teachers = 0

    for teacher_id in teacher_ids:
        cur.execute("DELETE FROM teacher_subjects WHERE teacher_id = ?", (teacher_id,))
        cur.execute("SELECT id FROM student_lessons WHERE teacher_id = ?", (teacher_id,))
        lesson_ids = [row[0] for row in cur.fetchall()]

        for lesson_id in lesson_ids:
            cur.execute("DELETE FROM attendance WHERE student_lesson_id = ?", (lesson_id,))
            deleted_attendance += cur.rowcount
            cur.execute("DELETE FROM balance_history WHERE student_lesson_id = ?", (lesson_id,))
            deleted_balance_history += cur.rowcount

        cur.execute("DELETE FROM student_lessons WHERE teacher_id = ?", (teacher_id,))
        deleted_lessons += cur.rowcount

        cur.execute("DELETE FROM teachers WHERE id = ?", (teacher_id,))
        deleted_teachers += cur.rowcount

    cur.execute(
        """
        DELETE FROM users
        WHERE telegram_id = ? AND role = 'teacher'
        """,
        (telegram_id,),
    )
    deleted_users = cur.rowcount

    conn.commit()
    conn.close()
    return {
        "ok": deleted_users > 0 or deleted_teachers > 0,
        "deleted_users": deleted_users,
        "deleted_teachers": deleted_teachers,
        "deleted_lessons": deleted_lessons,
        "deleted_attendance": deleted_attendance,
        "deleted_balance_history": deleted_balance_history,
    }


def delete_student_by_telegram_id(telegram_id: int) -> dict:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("BEGIN")

    cur.execute(
        """
        SELECT id
        FROM students
        WHERE telegram_id = ?
        """,
        (telegram_id,),
    )
    student_ids = [row[0] for row in cur.fetchall()]

    deleted_lessons = 0
    deleted_attendance = 0
    deleted_balance_history = 0

    for student_id in student_ids:
        cur.execute("SELECT id FROM student_lessons WHERE student_id = ?", (student_id,))
        lesson_ids = [row[0] for row in cur.fetchall()]

        for lesson_id in lesson_ids:
            cur.execute("DELETE FROM attendance WHERE student_lesson_id = ?", (lesson_id,))
            deleted_attendance += cur.rowcount
            cur.execute("DELETE FROM balance_history WHERE student_lesson_id = ?", (lesson_id,))
            deleted_balance_history += cur.rowcount

        cur.execute("DELETE FROM student_lessons WHERE student_id = ?", (student_id,))
        deleted_lessons += cur.rowcount

    cur.execute(
        """
        DELETE FROM payment_requests
        WHERE telegram_user_id = ?
        """,
        (telegram_id,),
    )
    deleted_payment_requests = cur.rowcount

    cur.execute(
        """
        DELETE FROM students
        WHERE telegram_id = ?
        """,
        (telegram_id,),
    )
    deleted_students = cur.rowcount

    cur.execute(
        """
        DELETE FROM users
        WHERE telegram_id = ? AND role = 'student'
        """,
        (telegram_id,),
    )
    deleted_users = cur.rowcount

    conn.commit()
    conn.close()
    return {
        "ok": deleted_users > 0 or deleted_students > 0,
        "deleted_users": deleted_users,
        "deleted_students": deleted_students,
        "deleted_lessons": deleted_lessons,
        "deleted_attendance": deleted_attendance,
        "deleted_balance_history": deleted_balance_history,
        "deleted_payment_requests": deleted_payment_requests,
    }


def get_teacher_by_id(teacher_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, telegram_id, full_name
        FROM teachers
        WHERE id = ?
        """,
        (teacher_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def search_teacher_profiles(query: str, limit: int = 30):
    conn = get_connection()
    cur = conn.cursor()
    normalized_query = " ".join(query.strip().lower().lstrip("@").split())

    def _run_search(search_text: str):
        pattern = f"%{search_text}%"
        spaced_pattern = f"%{'%'.join(search_text.split())}%"
        cur.execute(
            """
            SELECT
                t.id,
                t.full_name,
                t.subject_name,
                u.telegram_username
            FROM teachers t
            LEFT JOIN users u
                ON u.telegram_id = t.telegram_id
               AND u.role = 'teacher'
            WHERE LOWER(t.full_name) LIKE ?
               OR LOWER(t.full_name) LIKE ?
               OR LOWER(COALESCE(t.subject_name, '')) LIKE ?
               OR EXISTS (
                    SELECT 1
                    FROM teacher_subjects ts
                    WHERE ts.teacher_id = t.id
                      AND LOWER(ts.subject_name) LIKE ?
               )
               OR LOWER(COALESCE(u.telegram_username, '')) LIKE ?
            ORDER BY
                CASE
                    WHEN LOWER(t.full_name) = ? THEN 0
                    WHEN LOWER(t.full_name) LIKE ? THEN 1
                    ELSE 2
                END,
                t.full_name
            LIMIT ?
            """,
            (
                pattern,
                spaced_pattern,
                pattern,
                pattern,
                pattern,
                search_text,
                f"{search_text}%",
                limit,
            ),
        )
        return cur.fetchall()

    rows = _run_search(normalized_query) if normalized_query else []
    if not rows and len(normalized_query) >= 2:
        rows = _run_search(normalized_query[:2])

    conn.close()
    return rows


def list_teacher_profiles(limit: int = 500):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            t.id,
            t.full_name,
            t.subject_name,
            u.telegram_username
        FROM teachers t
        LEFT JOIN users u
            ON u.telegram_id = t.telegram_id
           AND u.role = 'teacher'
        ORDER BY t.full_name
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_teacher_profile_by_id(teacher_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            t.id,
            t.telegram_id,
            t.full_name,
            t.subject_name,
            t.description,
            t.photo_path,
            u.telegram_username
        FROM teachers t
        LEFT JOIN users u
            ON u.telegram_id = t.telegram_id
           AND u.role = 'teacher'
        WHERE t.id = ?
        """,
        (teacher_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def update_teacher_profile_fields(
    teacher_id: int,
    *,
    full_name: str,
    subject_name: str,
    description: str | None,
    photo_path: str | None,
):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE teachers
        SET full_name = ?,
            subject_name = ?,
            description = ?,
            photo_path = ?
        WHERE id = ?
        """,
        (full_name, subject_name, description, photo_path, teacher_id),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    ensure_teacher_subject_link(teacher_id, subject_name)
    return changed


def set_teacher_telegram_id(teacher_id: int, telegram_id: int | None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE teachers
        SET telegram_id = ?
        WHERE id = ?
        """,
        (telegram_id, teacher_id),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def log_admin_action(
    admin_telegram_id: int,
    action_type: str,
    target_type: str | None = None,
    target_id: int | None = None,
    details: str | dict | None = None,
    status: str = "success",
):
    conn = get_connection()
    cur = conn.cursor()

    details_value: str | None
    if isinstance(details, dict):
        details_value = json.dumps(details, ensure_ascii=False)
    elif details is None:
        details_value = None
    else:
        details_value = str(details)

    cur.execute(
        """
        INSERT INTO admin_actions (
            admin_telegram_id, action_type, target_type, target_id, details, status, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            admin_telegram_id,
            action_type,
            target_type,
            target_id,
            details_value,
            status,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )

    conn.commit()
    conn.close()


def get_recent_admin_actions(limit: int = 50):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, admin_telegram_id, action_type, target_type, target_id, details, status, created_at
        FROM admin_actions
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()

    conn.close()
    return rows


def get_weekly_lessons_report_for_teacher_telegram(
    telegram_id: int,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list:
    """Same as get_teacher_weekly_lessons_report but scoped to a single
    teacher (looked up by telegram_id). Used by the teacher self-report
    button in the admin bot."""
    conn = get_connection()
    cur = conn.cursor()

    # attendance.lesson_date is stored as 'YYYY-MM-DD HH:MM:SS'. Compare
    # against the full datetime so a lesson marked at 14:00 today is not
    # accidentally excluded by a date-only upper bound.
    if start_date is None:
        start_dt = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    else:
        start_dt = f"{start_date} 00:00:00"
    if end_date is None:
        end_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    else:
        end_dt = f"{end_date} 23:59:59"

    cur.execute(
        """
        SELECT
            t.id,
            t.full_name,
            sl.subject_name,
            COUNT(DISTINCT a.id) AS lessons_count,
            MAX(a.lesson_date) AS last_lesson_date
        FROM attendance a
        JOIN student_lessons sl ON a.student_lesson_id = sl.id
        JOIN teachers t ON sl.teacher_id = t.id
        WHERE a.lesson_date BETWEEN ? AND ?
          AND a.status IN ('present', 'completed')
          AND t.telegram_id = ?
        GROUP BY t.id, sl.subject_name
        ORDER BY sl.subject_name
        """,
        (start_dt, end_dt, int(telegram_id)),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_teacher_weekly_lessons_report(start_date: str = None, end_date: str = None) -> list:
    """Получает отчет о количестве проведенных занятий по учителям за период.

    Args:
        start_date: начало периода (YYYY-MM-DD), по умолчанию неделю назад
        end_date: конец периода (YYYY-MM-DD), по умолчанию сегодня

    Returns:
        Список кортежей (teacher_id, teacher_name, subject_name, lessons_count, last_lesson_date)
    """
    conn = get_connection()
    cur = conn.cursor()

    # attendance.lesson_date is stored as 'YYYY-MM-DD HH:MM:SS', so the period
    # bounds need full datetime granularity — a date-only upper bound would
    # cut off everything that happened today after 00:00.
    if start_date is None:
        start_dt = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    else:
        start_dt = f"{start_date} 00:00:00"
    if end_date is None:
        end_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    else:
        end_dt = f"{end_date} 23:59:59"

    cur.execute(
        """
        SELECT
            t.id,
            t.full_name,
            sl.subject_name,
            COUNT(DISTINCT a.id) as lessons_count,
            MAX(a.lesson_date) as last_lesson_date
        FROM attendance a
        JOIN student_lessons sl ON a.student_lesson_id = sl.id
        JOIN teachers t ON sl.teacher_id = t.id
        WHERE a.lesson_date BETWEEN ? AND ?
          AND a.status IN ('present', 'completed')
        GROUP BY t.id, sl.subject_name
        ORDER BY t.full_name, sl.subject_name, lessons_count DESC
        """,
        (start_dt, end_dt),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_teachers_with_lessons() -> list[tuple[int, str]]:
    """Return [(teacher_id, full_name)] for teachers who have any attendance records."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT t.id, t.full_name
        FROM teachers t
        JOIN student_lessons sl ON sl.teacher_id = t.id
        JOIN attendance a ON a.student_lesson_id = sl.id
        ORDER BY t.full_name
        """
    )
    rows = cur.fetchall()
    conn.close()
    return [(r[0], r[1]) for r in rows]


def get_teacher_lessons_report(
    teacher_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list:
    """Like get_teacher_weekly_lessons_report but with optional teacher_id filter.

    Returns list of (teacher_id, teacher_name, subject_name, lessons_count, last_lesson_date).
    """
    conn = get_connection()
    cur = conn.cursor()

    if start_date is None:
        start_dt = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    else:
        start_dt = f"{start_date} 00:00:00"
    if end_date is None:
        end_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    else:
        end_dt = f"{end_date} 23:59:59"

    if teacher_id is not None:
        cur.execute(
            """
            SELECT t.id, t.full_name, sl.subject_name,
                   COUNT(DISTINCT a.id) AS lessons_count,
                   MAX(a.lesson_date) AS last_lesson_date,
                   st.full_name AS student_name
            FROM attendance a
            JOIN student_lessons sl ON a.student_lesson_id = sl.id
            JOIN teachers t ON sl.teacher_id = t.id
            JOIN students st ON sl.student_id = st.id
            WHERE a.lesson_date BETWEEN ? AND ?
              AND a.status IN ('present', 'completed')
              AND t.id = ?
            GROUP BY t.id, sl.subject_name, st.id
            ORDER BY sl.subject_name, st.full_name
            """,
            (start_dt, end_dt, teacher_id),
        )
    else:
        cur.execute(
            """
            SELECT t.id, t.full_name, sl.subject_name,
                   COUNT(DISTINCT a.id) AS lessons_count,
                   MAX(a.lesson_date) AS last_lesson_date,
                   NULL AS student_name
            FROM attendance a
            JOIN student_lessons sl ON a.student_lesson_id = sl.id
            JOIN teachers t ON sl.teacher_id = t.id
            WHERE a.lesson_date BETWEEN ? AND ?
              AND a.status IN ('present', 'completed')
            GROUP BY t.id, sl.subject_name
            ORDER BY t.full_name, sl.subject_name
            """,
            (start_dt, end_dt),
        )
    rows = cur.fetchall()
    conn.close()
    return rows


def format_teacher_weekly_report(rows: list, period_desc: str = "за неделю") -> str:
    """Форматирует отчет о занятиях учителей в красивый вид."""
    if not rows:
        return f"📚 Нет данных о занятиях {period_desc}."

    lines = [f"📚 <b>Отчет о занятиях преподавателей {period_desc}</b>\n"]

    current_teacher = None
    for teacher_id, teacher_name, subject_name, lessons_count, last_lesson_date in rows:
        if current_teacher != teacher_name:
            current_teacher = teacher_name
            lines.append(f"\n<b>👨‍🏫 {teacher_name}</b>")

        lines.append(
            f"  • {subject_name}: <b>{lessons_count}</b> занятий (последнее: {last_lesson_date})"
        )

    return "\n".join(lines)


def format_admin_action_log(rows: list) -> str:
    """Форматирует журнал действий админа в красивый вид."""
    if not rows:
        return "📋 Журнал действий пока пуст."

    action_icons = {
        "add_student": "👤",
        "add_teacher": "👨‍🏫",
        "add_payment": "💰",
        "update_balance": "📊",
        "delete_student": "🗑️",
        "delete_teacher": "🗑️",
        "mark_attendance": "✅",
        "edit_teacher": "✏️",
        "role_change": "🔄",
        "publish": "📢",
    }

    status_icons = {
        "success": "✅",
        "error": "❌",
        "pending": "⏳",
    }

    lines = ["📋 <b>Журнал действий администраторов</b>\n"]

    for row in rows:
        action_id, admin_id, action_type, target_type, target_id, details, status, created_at = row

        icon = action_icons.get(action_type, "🔹")
        status_icon = status_icons.get(status, "❓")
        target_info = f"{target_type}#{target_id}" if target_type and target_id else "-"

        # Сокращаем длинный текст details
        details_text = ""
        if details:
            try:
                if details.startswith("{"):
                    details_obj = json.loads(details)
                    details_text = str(details_obj)[:50]
                else:
                    details_text = details[:50]
            except:
                details_text = str(details)[:50]

        lines.append(
            f"{icon} <b>{action_type}</b> {status_icon}\n"
            f"┌ ID: #{action_id}\n"
            f"├ Админ: <code>{admin_id}</code>\n"
            f"├ Цель: {target_info}\n"
            f"├ Время: {created_at}\n"
            f"└ Детали: {details_text if details_text else '-'}\n"
        )

    return "\n".join(lines)


def get_user_by_telegram_id(telegram_id: int):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, telegram_id, full_name, role, is_active
        FROM users
        WHERE telegram_id = ?
        """,
        (telegram_id,)
    )
    row = cur.fetchone()

    conn.close()
    return row


def get_users_by_role(role: str):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, telegram_id, full_name, role, is_active
        FROM users
        WHERE role = ?
        ORDER BY full_name
        """,
        (role,)
    )
    rows = cur.fetchall()

    conn.close()
    return rows


def get_active_student_telegram_ids() -> list[int]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT telegram_id
        FROM (
            SELECT telegram_id
            FROM users
            WHERE role = 'student'
              AND is_active = 1
              AND telegram_id IS NOT NULL
            UNION
            SELECT telegram_id
            FROM students
            WHERE telegram_id IS NOT NULL
        )
        ORDER BY telegram_id
        """
    )
    rows = cur.fetchall()
    conn.close()
    return [int(row[0]) for row in rows if row and row[0] is not None]


def get_student_by_telegram_id(telegram_id: int):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, full_name, telegram_id, phone
        FROM students
        WHERE telegram_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (telegram_id,)
    )
    row = cur.fetchone()

    conn.close()
    return row


def create_publication_post(
    *,
    created_by: int,
    audience: str = "students",
    description: str,
    photo_file_id: str | None = None,
    links: list[str] | None = None,
    scheduled_for: str,
) -> int:
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    links_json = json.dumps(links or [], ensure_ascii=False)
    cur.execute(
        """
        INSERT INTO publication_posts (
            created_by,
            audience,
            description,
            photo_file_id,
            links_json,
            status,
            scheduled_for,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, 'scheduled', ?, ?, ?)
        """,
        (
            created_by,
            audience,
            description.strip(),
            photo_file_id,
            links_json,
            scheduled_for,
            now,
            now,
        ),
    )
    post_id = int(cur.lastrowid) if cur.lastrowid is not None else 0
    conn.commit()
    conn.close()
    return post_id


def create_review_card(
    *,
    created_by: int,
    description: str,
    media_file_id: str | None = None,
    media_type: str | None = None,
    links: list[str] | None = None,
    media_local_path: str | None = None,
) -> int:
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    links_json = json.dumps(links or [], ensure_ascii=False)
    cur.execute(
        """
        INSERT INTO review_cards (
            created_by,
            description,
            media_file_id,
            media_type,
            media_local_path,
            links_json,
            is_active,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            created_by,
            description.strip(),
            media_file_id,
            media_type,
            media_local_path,
            links_json,
            now,
            now,
        ),
    )
    review_id = int(cur.lastrowid) if cur.lastrowid is not None else 0
    conn.commit()
    conn.close()
    return review_id


def update_review_card_media(
    review_id: int,
    *,
    media_file_id: str | None,
    media_type: str | None,
    media_local_path: str | None,
) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        UPDATE review_cards
        SET media_file_id = ?, media_type = ?, media_local_path = ?, updated_at = ?
        WHERE id = ? AND is_active = 1
        """,
        (media_file_id, media_type, media_local_path, now, review_id),
    )
    conn.commit()
    success = cur.rowcount > 0
    conn.close()
    return success


def get_promo_code_by_id(promo_code_id: int):
    """Return (id, code, discount_type, discount_value) for a promo code or None."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, code, discount_type, discount_value FROM promo_codes WHERE id = ?",
        (promo_code_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def deactivate_review_card(review_id: int) -> bool:
    """Деактивировать отзыв (сделать невидимым для учеников)"""
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        UPDATE review_cards
        SET is_active = 0, updated_at = ?
        WHERE id = ?
        """,
        (now, review_id),
    )
    conn.commit()
    success = cur.rowcount > 0
    conn.close()
    return success


def get_active_review_cards(limit: int = 200) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            id,
            description,
            media_file_id,
            media_type,
            media_local_path,
            links_json,
            created_at
        FROM review_cards
        WHERE is_active = 1
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()

    result: list[dict] = []
    for row in rows:
        links_value = row[5] or "[]"
        try:
            links = json.loads(links_value)
            if not isinstance(links, list):
                links = []
        except Exception:
            links = []
        media_type_raw = (row[3] or "").strip()
        media_file_id_raw = row[2]
        media_local_path_raw = row[4]

        result.append(
            {
                "id": int(row[0]),
                "description": (row[1] or "").strip(),
                "media_file_id": media_file_id_raw if media_file_id_raw else None,
                "media_type": media_type_raw if media_type_raw else None,
                "media_local_path": media_local_path_raw if media_local_path_raw else None,
                "links": [str(link).strip() for link in links if str(link).strip()][:8],
                "created_at": row[6],
            }
        )
    return result


def get_due_publication_posts(limit: int = 20, now_ts: str | None = None):
    conn = get_connection()
    cur = conn.cursor()
    now = (now_ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S")).strip()
    cur.execute(
        """
        SELECT
            id,
            created_by,
            audience,
            description,
            photo_file_id,
            links_json,
            status,
            scheduled_for,
            sent_at,
            created_at,
            updated_at,
            last_error
        FROM publication_posts
        WHERE status = 'scheduled'
          AND scheduled_for <= ?
        ORDER BY scheduled_for, id
        LIMIT ?
        """,
        (now, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def mark_publication_post_sent(post_id: int):
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        UPDATE publication_posts
        SET status = 'sent',
            sent_at = ?,
            updated_at = ?,
            last_error = NULL
        WHERE id = ?
        """,
        (now, now, post_id),
    )
    conn.commit()
    conn.close()


def mark_publication_post_failed(post_id: int, error_text: str):
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        UPDATE publication_posts
        SET status = 'failed',
            updated_at = ?,
            last_error = ?
        WHERE id = ?
        """,
        (now, error_text[:500], post_id),
    )
    conn.commit()
    conn.close()


def get_debt_rows_for_reminder(reminder_date: str):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            sl.id,
            s.telegram_id,
            s.full_name,
            t.full_name,
            sl.subject_name,
            sl.lesson_balance
        FROM student_lessons sl
        JOIN students s ON s.id = sl.student_id
        JOIN teachers t ON t.id = sl.teacher_id
        WHERE sl.lesson_balance < 0
          AND s.telegram_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM debt_reminder_log dr
              WHERE dr.student_lesson_id = sl.id
                AND dr.reminder_date = ?
          )
        ORDER BY s.id, sl.id
        """,
        (reminder_date,),
    )
    rows = cur.fetchall()

    conn.close()
    return rows


def get_debt_rows_for_reminder_max(reminder_date: str):
    """Like get_debt_rows_for_reminder but for MAX users (students with max_id set)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            sl.id,
            s.max_id,
            s.full_name,
            t.full_name,
            sl.subject_name,
            sl.lesson_balance
        FROM student_lessons sl
        JOIN students s ON s.id = sl.student_id
        JOIN teachers t ON t.id = sl.teacher_id
        WHERE sl.lesson_balance < 0
          AND s.max_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM debt_reminder_log dr
              WHERE dr.student_lesson_id = sl.id
                AND dr.reminder_date = ?
          )
        ORDER BY s.id, sl.id
        """,
        (reminder_date,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_current_debtors_summary(limit: int = 200) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            s.id,
            s.full_name,
            s.telegram_id,
            s.telegram_username,
            SUM(ABS(sl.lesson_balance)) AS total_debt_lessons,
            COUNT(sl.id) AS debt_directions_count
        FROM student_lessons sl
        JOIN students s ON s.id = sl.student_id
        WHERE sl.lesson_balance < 0
        GROUP BY s.id, s.full_name, s.telegram_id, s.telegram_username
        ORDER BY total_debt_lessons DESC, s.full_name, s.id
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()

    result: list[dict] = []
    for row in rows:
        result.append(
            {
                "student_id": int(row[0]),
                "full_name": (row[1] or "").strip() or f"Ученик #{int(row[0])}",
                "telegram_id": int(row[2]) if row[2] is not None else None,
                "telegram_username": normalize_telegram_username(row[3]),
                "total_debt_lessons": int(row[4] or 0),
                "debt_directions_count": int(row[5] or 0),
            }
        )
    return result


def get_debtor_student_details(student_id: int) -> dict | None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, full_name, telegram_id, telegram_username, phone
        FROM students
        WHERE id = ?
        """,
        (student_id,),
    )
    student_row = cur.fetchone()
    if not student_row:
        conn.close()
        return None

    cur.execute(
        """
        SELECT
            sl.id,
            sl.subject_name,
            t.full_name,
            sl.lesson_balance
        FROM student_lessons sl
        JOIN teachers t ON t.id = sl.teacher_id
        WHERE sl.student_id = ?
          AND sl.lesson_balance < 0
        ORDER BY ABS(sl.lesson_balance) DESC, sl.id
        """,
        (student_id,),
    )
    debt_rows = cur.fetchall()
    conn.close()

    directions: list[dict] = []
    total_debt_lessons = 0
    for lesson_id, subject_name, teacher_name, lesson_balance in debt_rows:
        debt_lessons = abs(int(lesson_balance or 0))
        total_debt_lessons += debt_lessons
        directions.append(
            {
                "student_lesson_id": int(lesson_id),
                "subject_name": (subject_name or "").strip() or "-",
                "teacher_name": (teacher_name or "").strip() or "-",
                "debt_lessons": debt_lessons,
            }
        )

    return {
        "student_id": int(student_row[0]),
        "full_name": (student_row[1] or "").strip() or f"Ученик #{int(student_row[0])}",
        "telegram_id": int(student_row[2]) if student_row[2] is not None else None,
        "telegram_username": normalize_telegram_username(student_row[3]),
        "phone": (student_row[4] or "").strip() or None,
        "total_debt_lessons": total_debt_lessons,
        "directions": directions,
    }


def search_users_by_name_or_username(
    query: str,
    roles: list[str] | tuple[str, ...] | None = None,
    limit: int = 30,
):
    conn = get_connection()
    cur = conn.cursor()

    normalized_query = query.strip().lower().lstrip("@")
    pattern = f"%{normalized_query}%"
    if roles:
        placeholders = ",".join("?" for _ in roles)
        cur.execute(
            f"""
            SELECT id, telegram_id, full_name, role, is_active, telegram_username
            FROM users
            WHERE role IN ({placeholders})
              AND (
                LOWER(full_name) LIKE ?
                OR LOWER(COALESCE(telegram_username, '')) LIKE ?
              )
            ORDER BY full_name
            LIMIT ?
            """,
            (*roles, pattern, pattern, limit),
        )
    else:
        cur.execute(
            """
            SELECT id, telegram_id, full_name, role, is_active, telegram_username
            FROM users
            WHERE LOWER(full_name) LIKE ?
               OR LOWER(COALESCE(telegram_username, '')) LIKE ?
            ORDER BY full_name
            LIMIT ?
            """,
            (pattern, pattern, limit),
        )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_user_by_id(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, telegram_id, full_name, role, is_active, telegram_username
        FROM users
        WHERE id = ?
        """,
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def mark_debt_reminder_sent(student_lesson_id: int, reminder_date: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO debt_reminder_log (
            student_lesson_id, reminder_date, reminded_at
        )
        VALUES (?, ?, ?)
        """,
        (
            student_lesson_id,
            reminder_date,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    conn.close()


def get_active_admin_telegram_ids() -> list[int]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT telegram_id
        FROM users
        WHERE role IN ('admin', 'superadmin')
          AND is_active = 1
        ORDER BY telegram_id
        """
    )
    rows = cur.fetchall()
    conn.close()
    return [int(row[0]) for row in rows if row and row[0] is not None]


def set_admin_visibility(telegram_id: int, is_visible: bool) -> bool:
    """Устанавливает видимость админа для учеников (по умолчанию видимы все)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET is_visible_to_students = ? WHERE telegram_id = ? AND role IN ('admin', 'superadmin')",
        (1 if is_visible else 0, telegram_id),
    )
    conn.commit()
    success = cur.rowcount > 0
    conn.close()
    return success


def get_active_admin_contacts() -> list[tuple[int, str, str | None]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT telegram_id, full_name, telegram_username
        FROM users
        WHERE role IN ('admin', 'superadmin')
          AND is_active = 1
          AND is_visible_to_students = 1
          AND telegram_id IS NOT NULL
        ORDER BY role DESC, full_name
        """
    )
    rows = cur.fetchall()
    conn.close()
    return [
        (
            int(row[0]),
            escape((row[1] or "").strip()) or f"ID {int(row[0])}",
            normalize_telegram_username(row[2]),
        )
        for row in rows
        if row and row[0] is not None
    ]


def get_teacher_catalog_subjects() -> list[str]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT subject_name
        FROM (
            SELECT subject_name
            FROM teachers
            WHERE subject_name IS NOT NULL
              AND TRIM(subject_name) <> ''
            UNION
            SELECT subject_name
            FROM teacher_subjects
            WHERE subject_name IS NOT NULL
              AND TRIM(subject_name) <> ''
        )
        ORDER BY subject_name
        """
    )
    rows = cur.fetchall()
    conn.close()
    return [row[0] for row in rows]


def cleanup_orphan_teacher_subjects() -> dict:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(1) FROM teacher_subjects")
    before_total = int(cur.fetchone()[0] or 0)

    cur.execute(
        """
        DELETE FROM teacher_subjects
        WHERE subject_name IS NULL
           OR TRIM(subject_name) = ''
           OR teacher_id NOT IN (SELECT id FROM teachers)
        """
    )
    deleted_invalid = int(cur.rowcount or 0)

    deleted_not_linked = 0

    cur.execute("SELECT COUNT(1) FROM teacher_subjects")
    after_total = int(cur.fetchone()[0] or 0)

    conn.commit()
    conn.close()
    return {
        "before_total": before_total,
        "after_total": after_total,
        "deleted_invalid": deleted_invalid,
        "deleted_not_linked": deleted_not_linked,
    }


def get_teacher_catalog_names() -> list[str]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT full_name
        FROM teachers
        WHERE full_name IS NOT NULL
          AND TRIM(full_name) <> ''
        ORDER BY full_name
        """
    )
    rows = cur.fetchall()
    conn.close()
    return [row[0] for row in rows]


def get_teacher_catalog_name_subject_pairs() -> list[tuple[str, str]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT full_name, subject_name
        FROM (
            SELECT
                t.full_name AS full_name,
                ts.subject_name AS subject_name
            FROM teachers t
            JOIN teacher_subjects ts ON ts.teacher_id = t.id
            WHERE t.full_name IS NOT NULL
              AND TRIM(t.full_name) <> ''
              AND ts.subject_name IS NOT NULL
              AND TRIM(ts.subject_name) <> ''
            UNION
            SELECT
                t.full_name AS full_name,
                t.subject_name AS subject_name
            FROM teachers t
            WHERE t.full_name IS NOT NULL
              AND TRIM(t.full_name) <> ''
              AND t.subject_name IS NOT NULL
              AND TRIM(t.subject_name) <> ''
        )
        ORDER BY full_name, subject_name
        """
    )
    rows = cur.fetchall()
    conn.close()
    return [(str(row[0]), str(row[1])) for row in rows]


def get_teacher_cards_by_subject(subject_name: str) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT t.id, t.full_name, t.description, t.photo_path, t.telegram_id
        FROM teachers t
        WHERE COALESCE(t.subject_name, '') = ?
           OR EXISTS (
                SELECT 1
                FROM teacher_subjects ts
                WHERE ts.teacher_id = t.id
                  AND ts.subject_name = ?
           )
        ORDER BY t.id
        """,
        (subject_name, subject_name),
    )
    rows = cur.fetchall()
    conn.close()
    result = []
    for row in rows:
        result.append(
            {
                "id": int(row[0]),
                "name": row[1],
                "description": row[2] or "",
                "photo": row[3],
                "telegram_id": row[4],
            }
        )
    return result


def save_daily_debt_snapshot(snapshot_date: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO debt_daily_snapshots (
            snapshot_date, student_lesson_id, lesson_balance
        )
        SELECT ?, id, lesson_balance
        FROM student_lessons
        """,
        (snapshot_date,),
    )
    conn.commit()
    conn.close()


def _get_snapshot_map(snapshot_date: str) -> dict[int, int]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT student_lesson_id, lesson_balance
        FROM debt_daily_snapshots
        WHERE snapshot_date = ?
        """,
        (snapshot_date,),
    )
    rows = cur.fetchall()
    conn.close()
    return {int(row[0]): int(row[1]) for row in rows}


def _get_latest_snapshot_date_before(snapshot_date: str) -> str | None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT MAX(snapshot_date)
        FROM debt_daily_snapshots
        WHERE snapshot_date < ?
        """,
        (snapshot_date,),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def _get_lesson_details_map(lesson_ids: list[int]) -> dict[int, dict]:
    if not lesson_ids:
        return {}

    conn = get_connection()
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in lesson_ids)
    cur.execute(
        f"""
        SELECT
            sl.id,
            s.telegram_id,
            s.full_name,
            t.full_name,
            sl.subject_name,
            sl.lesson_balance
        FROM student_lessons sl
        JOIN students s ON s.id = sl.student_id
        JOIN teachers t ON t.id = sl.teacher_id
        WHERE sl.id IN ({placeholders})
        """,
        lesson_ids,
    )
    rows = cur.fetchall()
    conn.close()

    result = {}
    for row in rows:
        lesson_id, telegram_id, student_name, teacher_name, subject_name, lesson_balance = row
        result[int(lesson_id)] = {
            "lesson_id": int(lesson_id),
            "telegram_id": int(telegram_id) if telegram_id is not None else None,
            "student_name": student_name,
            "teacher_name": teacher_name,
            "subject_name": subject_name,
            "lesson_balance": int(lesson_balance),
        }
    return result


def _calculate_debt_age_days(student_lesson_id: int, report_date: str) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT snapshot_date, lesson_balance
        FROM debt_daily_snapshots
        WHERE student_lesson_id = ?
          AND snapshot_date <= ?
        ORDER BY snapshot_date DESC
        """,
        (student_lesson_id, report_date),
    )
    rows = cur.fetchall()
    conn.close()

    streak_start: str | None = None
    for snapshot_day, balance in rows:
        if balance < 0:
            streak_start = snapshot_day
            continue
        break

    if not streak_start:
        return 0

    start_dt = datetime.strptime(streak_start, "%Y-%m-%d").date()
    end_dt = datetime.strptime(report_date, "%Y-%m-%d").date()
    return (end_dt - start_dt).days + 1


def build_daily_debt_report(report_date: str, overdue_days: int = 7) -> dict:
    save_daily_debt_snapshot(report_date)

    today_map = _get_snapshot_map(report_date)
    prev_date = _get_latest_snapshot_date_before(report_date)
    prev_map = _get_snapshot_map(prev_date) if prev_date else {}

    today_negative_ids = {lesson_id for lesson_id, bal in today_map.items() if bal < 0}
    prev_negative_ids = {lesson_id for lesson_id, bal in prev_map.items() if bal < 0}

    new_debt_ids = sorted(today_negative_ids - prev_negative_ids)
    closed_debt_ids = sorted(prev_negative_ids - today_negative_ids)
    overdue_ids = []
    for lesson_id in sorted(today_negative_ids):
        age_days = _calculate_debt_age_days(lesson_id, report_date)
        if age_days > overdue_days:
            overdue_ids.append((lesson_id, age_days))

    details_map = _get_lesson_details_map(
        sorted(set(new_debt_ids + closed_debt_ids + [lesson_id for lesson_id, _ in overdue_ids]))
    )

    new_debts = []
    for lesson_id in new_debt_ids:
        item = details_map.get(lesson_id, {"lesson_id": lesson_id})
        new_debts.append(item)

    closed_debts = []
    for lesson_id in closed_debt_ids:
        item = details_map.get(lesson_id, {"lesson_id": lesson_id})
        closed_debts.append(item)

    overdue_debts = []
    for lesson_id, age_days in overdue_ids:
        item = details_map.get(lesson_id, {"lesson_id": lesson_id})
        item = {**item, "age_days": age_days}
        overdue_debts.append(item)

    return {
        "report_date": report_date,
        "previous_snapshot_date": prev_date,
        "total_current_debts": len(today_negative_ids),
        "new_debts": new_debts,
        "closed_debts": closed_debts,
        "overdue_debts": overdue_debts,
    }


def is_daily_debt_report_sent(report_date: str) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1
        FROM debt_report_runs
        WHERE report_date = ?
        """,
        (report_date,),
    )
    exists = cur.fetchone() is not None
    conn.close()
    return exists


def mark_daily_debt_report_sent(report_date: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO debt_report_runs (
            report_date, sent_at
        )
        VALUES (?, ?)
        """,
        (report_date, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()


def cleanup_old_data(days: int = 14) -> dict:
    """Удаляет старые данные (старше days дней) для оптимизации БД."""
    conn = get_connection()
    cur = conn.cursor()
    cutoff_date = (datetime.now() - __import__('datetime').timedelta(days=days)).strftime("%Y-%m-%d")

    stats = {}

    # Удаляем старые логи действий админа
    cur.execute("SELECT COUNT(1) FROM admin_actions WHERE created_at < %s", (cutoff_date,))
    count_before = int(cur.fetchone()[0] or 0)
    cur.execute("DELETE FROM admin_actions WHERE created_at < %s", (cutoff_date,))
    stats["admin_actions_deleted"] = int(cur.rowcount or 0)

    # Удаляем старые публикации
    cur.execute("DELETE FROM publication_posts WHERE status = 'sent' AND sent_at < %s", (cutoff_date,))
    stats["publications_deleted"] = int(cur.rowcount or 0)

    conn.commit()
    conn.close()
    return stats


def optimize_database() -> dict:
    """Оптимизирует БД: создаёт индексы и запускает VACUUM."""
    return {"status": "skipped", "message": "PostgreSQL manages optimization automatically"}


# ---------------------------------------------------------------------------
# Backfill helpers
# ---------------------------------------------------------------------------

def get_all_attendance_for_backfill(from_date: str | None = None) -> list[dict]:
    """Return all attendance records with full context for Sheets backfill.

    Each dict contains: attendance_id, lesson_datetime, teacher_name,
    student_name, subject_name, tariff_type, status, marked_by_name.
    Ordered by lesson_date ASC so the sheet fills chronologically.
    """
    conn = get_connection()
    cur = conn.cursor()

    where = "WHERE a.lesson_date >= ?" if from_date else ""
    params = (f"{from_date} 00:00:00",) if from_date else ()

    cur.execute(
        f"""
        SELECT
            a.id                                                         AS attendance_id,
            a.lesson_date                                                AS lesson_datetime,
            t.full_name                                                  AS teacher_name,
            st.full_name                                                 AS student_name,
            sl.subject_name,
            sl.tariff_type,
            a.status,
            COALESCE(ktu.full_name, u.full_name, CAST(a.marked_by AS TEXT)) AS marked_by_name
        FROM attendance a
        JOIN student_lessons sl  ON a.student_lesson_id = sl.id
        JOIN teachers t          ON sl.teacher_id       = t.id
        JOIN students st         ON sl.student_id       = st.id
        LEFT JOIN known_telegram_users ktu ON ktu.telegram_id = a.marked_by
        LEFT JOIN users u                  ON u.telegram_id   = a.marked_by
        {where}
        ORDER BY a.lesson_date ASC
        """,
        params,
    )
    rows = cur.fetchall()
    conn.close()

    tariff_labels = {"package": "Пакет", "per_lesson": "Поурочно", "subscription": "Абонемент"}
    status_labels = {"present": "Был", "absent": "Не был", "cancelled": "Отменено"}

    return [
        {
            "attendance_id": r[0],
            "lesson_datetime": r[1],
            "teacher_name": r[2] or "—",
            "student_name": r[3] or "—",
            "subject_name": r[4] or "—",
            "tariff_type": tariff_labels.get(r[5] or "", r[5] or "—"),
            "status": status_labels.get(r[6] or "", r[6] or "—"),
            "balance_before": "—",
            "balance_after": "—",
            "marked_by_name": r[7] or "—",
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Sheets outbox — гарантированная доставка строк в Google Sheets
# ---------------------------------------------------------------------------

def sheets_outbox_add(attendance_id: int, payload: dict) -> None:
    """Сохранить строку в очередь на случай, если Sheets недоступен."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO sheets_outbox (attendance_id, payload_json, created_at)
        VALUES (?, ?, ?)
        """,
        (attendance_id, json.dumps(payload, ensure_ascii=False), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()


def sheets_outbox_pop_pending(limit: int = 20) -> list[dict]:
    """Вернуть до `limit` записей с attempts < 10, не трогая их пока."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, attendance_id, payload_json, attempts
        FROM sheets_outbox
        WHERE attempts < 10
        ORDER BY id
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    return [
        {"outbox_id": r[0], "attendance_id": r[1], "payload": json.loads(r[2]), "attempts": r[3]}
        for r in rows
    ]


def sheets_outbox_delete(outbox_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM sheets_outbox WHERE id = ?", (outbox_id,))
    conn.commit()
    conn.close()


def sheets_outbox_increment_attempts(outbox_id: int, error: str) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE sheets_outbox SET attempts = attempts + 1, last_error = ? WHERE id = ?",
        (error[:500], outbox_id),
    )
    conn.commit()
    conn.close()


def sheets_outbox_get_dead() -> list[dict]:
    """Вернуть записи, исчерпавшие все попытки (attempts >= 10)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, attendance_id, attempts, last_error, created_at
        FROM sheets_outbox
        WHERE attempts >= 10
        ORDER BY id
        """,
    )
    rows = cur.fetchall()
    conn.close()
    return [
        {"outbox_id": r[0], "attendance_id": r[1], "attempts": r[2],
         "last_error": r[3], "created_at": r[4]}
        for r in rows
    ]


def sheets_outbox_delete_dead() -> int:
    """Удалить все застрявшие записи (attempts >= 10). Вернуть количество удалённых."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM sheets_outbox WHERE attempts >= 10")
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted


# ---------------------------------------------------------------------------
# Sheets summary data — Выплаты / Балансы / Статистика
# ---------------------------------------------------------------------------

def get_weekly_payouts(weeks: int = 8) -> list[dict]:
    """Return per-teacher lesson counts for the last N complete Mon–Sun weeks.

    Returns list of dicts, most recent week first:
      {week_start, week_end, teachers: [{name, lessons}], total_lessons}
    """
    from datetime import date as _date
    conn = get_connection()
    cur = conn.cursor()

    today = _date.today()
    # Most recent complete Sunday
    last_sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    last_monday = last_sunday - timedelta(days=6)

    result = []
    for i in range(weeks):
        w_start = last_monday - timedelta(weeks=i)
        w_end = w_start + timedelta(days=6)
        start_dt = f"{w_start.isoformat()} 00:00:00"
        end_dt = f"{w_end.isoformat()} 23:59:59"

        cur.execute(
            """
            SELECT t.full_name, COUNT(DISTINCT a.id) AS lessons, t.payment_details
            FROM attendance a
            JOIN student_lessons sl ON a.student_lesson_id = sl.id
            JOIN teachers t ON sl.teacher_id = t.id
            WHERE a.lesson_date BETWEEN ? AND ?
              AND a.status IN ('present', 'completed')
            GROUP BY t.id, t.full_name, t.payment_details
            ORDER BY t.full_name
            """,
            (start_dt, end_dt),
        )
        rows = cur.fetchall()
        if rows:
            teachers = [{"name": r[0], "lessons": int(r[1]), "payment_details": r[2] or ""} for r in rows]
            result.append({
                "week_start": w_start.isoformat(),
                "week_end": w_end.isoformat(),
                "teachers": teachers,
                "total_lessons": sum(t["lessons"] for t in teachers),
            })

    conn.close()
    return result


def get_all_student_balances() -> list[dict]:
    """Return all active student directions sorted by balance ascending."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT st.full_name, sl.subject_name, t.full_name, sl.lesson_balance, sl.tariff_type
        FROM student_lessons sl
        JOIN students st ON sl.student_id = st.id
        JOIN teachers t ON sl.teacher_id = t.id
        ORDER BY sl.lesson_balance ASC, st.full_name
        """
    )
    rows = cur.fetchall()
    conn.close()
    tariff_labels = {"package": "Пакет", "per_lesson": "Поурочно", "subscription": "Абонемент"}
    return [
        {
            "student_name": r[0] or "—",
            "subject_name": r[1] or "—",
            "teacher_name": r[2] or "—",
            "lesson_balance": int(r[3] or 0),
            "tariff_type": tariff_labels.get(r[4] or "", r[4] or "—"),
        }
        for r in rows
    ]


def get_attendance_stats() -> dict:
    """Return summary stats for the Статистика sheet."""
    from datetime import date as _date
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT lesson_date FROM attendance WHERE status IN ('present', 'completed') ORDER BY lesson_date"
    )
    all_dates = [r[0][:10] for r in cur.fetchall() if r[0]]
    conn.close()

    today = _date.today()
    today_str = today.isoformat()

    # Current week (Mon–Sun)
    week_start = today - timedelta(days=today.weekday())
    week_start_str = week_start.isoformat()

    # Last complete week
    last_week_end = week_start - timedelta(days=1)
    last_week_start = last_week_end - timedelta(days=6)

    # Current month
    month_start_str = today.replace(day=1).isoformat()

    # Last calendar month
    if today.month == 1:
        last_month_start = today.replace(year=today.year - 1, month=12, day=1)
    else:
        last_month_start = today.replace(month=today.month - 1, day=1)
    last_month_end = today.replace(day=1) - timedelta(days=1)

    today_count      = sum(1 for d in all_dates if d == today_str)
    week_count       = sum(1 for d in all_dates if d >= week_start_str)
    last_week_count  = sum(1 for d in all_dates if last_week_start.isoformat() <= d <= last_week_end.isoformat())
    month_count      = sum(1 for d in all_dates if d >= month_start_str)
    last_month_count = sum(1 for d in all_dates if last_month_start.isoformat() <= d <= last_month_end.isoformat())

    weekday_names = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
    weekday_counts = [0] * 7
    for d in all_dates:
        try:
            weekday_counts[_date.fromisoformat(d).weekday()] += 1
        except Exception:
            pass

    # Last 8 complete Mon–Sun weeks
    last_sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    last_monday = last_sunday - timedelta(days=6)
    weekly_history = []
    for i in range(8):
        w_start = last_monday - timedelta(weeks=i)
        w_end   = w_start + timedelta(days=6)
        count = sum(1 for d in all_dates if w_start.isoformat() <= d <= w_end.isoformat())
        weekly_history.append({
            "label": f"{w_start.strftime('%d.%m')} – {w_end.strftime('%d.%m')}",
            "count": count,
        })

    # Last 6 calendar months
    _month_names = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
    monthly_history = []
    for i in range(6):
        m = today.month - i
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        m_start = _date(y, m, 1)
        m_end = (_date(y, m + 1, 1) if m < 12 else _date(y + 1, 1, 1)) - timedelta(days=1)
        count = sum(1 for d in all_dates if m_start.isoformat() <= d <= m_end.isoformat())
        suffix = f" {y}" if y != today.year else ""
        monthly_history.append({"label": f"{_month_names[m]}{suffix}", "count": count})

    return {
        "today":          today_count,
        "week":           week_count,
        "last_week":      last_week_count,
        "month":          month_count,
        "last_month":     last_month_count,
        "total":          len(all_dates),
        "by_weekday":     list(zip(weekday_names, weekday_counts)),
        "weekly_history":  weekly_history,
        "monthly_history": monthly_history,
        "updated_at":     datetime.now().strftime("%d.%m.%Y %H:%M"),
    }


# ──────────────────────────────────────────────────────────────────────────────
# MAX messenger helpers
# ──────────────────────────────────────────────────────────────────────────────

def find_students_by_max_id(max_user_id: int) -> list[tuple]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, full_name, telegram_id, phone FROM students WHERE max_id = ? ORDER BY id DESC",
        (max_user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def link_max_to_student(student_id: int, max_id: int, max_username: str | None = None) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE students SET max_id = ?, max_username = COALESCE(?, max_username) WHERE id = ?",
        (max_id, max_username, student_id),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    if changed:
        link_invitee_student_max(max_id, student_id)
    return changed


def try_auto_link_max_by_phone(phone: str, max_id: int, max_username: str | None = None):
    """Match a MAX user to a student by last-10-digits of phone number.

    Returns the matched student row (id, full_name, telegram_id, phone) or None.
    Only links if the student has no max_id yet (or the same max_id).
    """
    cleaned_query = re.sub(r"[^\d]", "", phone or "")
    if len(cleaned_query) < 7:
        return None
    key = cleaned_query[-10:]

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, full_name, telegram_id, phone, max_id FROM students WHERE phone IS NOT NULL AND phone != ''"
    )
    rows = cur.fetchall()
    conn.close()

    for student_id, full_name, telegram_id, student_phone, student_max_id in rows:
        if not student_phone:
            continue
        cleaned_stored = re.sub(r"[^\d]", "", student_phone)
        if len(cleaned_stored) < 7:
            continue
        if cleaned_stored[-10:] != key:
            continue
        if student_max_id and student_max_id != max_id:
            return None
        link_max_to_student(student_id, max_id, max_username)
        return (student_id, full_name, telegram_id, student_phone)
    return None


def create_account_link_code(telegram_id: int) -> str:
    """Generate a 6-character code for linking a TG account to MAX. TTL = 15 min."""
    code = secrets.token_hex(3).upper()
    now = datetime.now()
    expires = (now + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM account_link_codes WHERE telegram_id = ?", (telegram_id,))
    cur.execute(
        "INSERT INTO account_link_codes (code, telegram_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (code, telegram_id, now.strftime("%Y-%m-%d %H:%M:%S"), expires),
    )
    conn.commit()
    conn.close()
    return code


def consume_account_link_code(code: str) -> int | None:
    """Validate and consume a link code. Returns telegram_id on success, None otherwise."""
    code = code.strip().upper()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT telegram_id FROM account_link_codes WHERE code = ? AND expires_at > ? AND used = 0",
        (code, now),
    )
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE account_link_codes SET used = 1 WHERE code = ?", (code,))
        conn.commit()
    conn.close()
    return row[0] if row else None


def get_max_fsm_state(max_user_id: int) -> tuple[str | None, dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT state, data FROM max_fsm_state WHERE max_user_id = ?",
        (max_user_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None, {}
    state, data_json = row
    try:
        data = json.loads(data_json) if data_json else {}
    except Exception:
        data = {}
    return state, data


def set_max_fsm_state(max_user_id: int, state: str, data: dict | None = None) -> None:
    data_json = json.dumps(data or {}, ensure_ascii=False)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO max_fsm_state (max_user_id, state, data)
        VALUES (?, ?, ?)
        ON CONFLICT(max_user_id) DO UPDATE SET state = excluded.state, data = excluded.data
        """,
        (max_user_id, state, data_json),
    )
    conn.commit()
    conn.close()


def clear_max_fsm_state(max_user_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM max_fsm_state WHERE max_user_id = ?", (max_user_id,))
    conn.commit()
    conn.close()


def create_payment_request_max(
    max_user_id: int,
    max_username: str | None,
    max_full_name: str | None,
    caption_text: str | None,
    file_id: str,
    file_type: str,
    promo_code_id_used: int | None = None,
) -> int:
    """Create a payment request originating from MAX messenger."""
    _MSK = timezone(timedelta(hours=3))
    now = datetime.now(_MSK).strftime("%Y-%m-%d %H:%M:%S")
    display_name = f"📱[MAX] {max_full_name}" if max_full_name else "📱[MAX]"
    display_username = f"@{max_username}" if max_username else None
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO payment_requests (
            telegram_user_id, telegram_username, telegram_full_name,
            caption_text, file_id, file_type, status,
            source_platform, max_user_id, created_at, updated_at,
            promo_code_id_used
        )
        VALUES (NULL, ?, ?, ?, ?, ?, 'pending', 'max', ?, ?, ?, ?)
        """,
        (
            display_username,
            display_name,
            caption_text,
            file_id,
            file_type,
            max_user_id,
            now,
            now,
            promo_code_id_used,
        ),
    )
    payment_request_id = cur.lastrowid
    conn.commit()
    conn.close()
    return payment_request_id


def get_payment_platform_info(payment_request_id: int) -> tuple[str, int | None]:
    """Return (source_platform, max_user_id) for the given payment request."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(source_platform, 'telegram'), max_user_id FROM payment_requests WHERE id = ?",
        (payment_request_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return "telegram", None
    return row[0], row[1]


def get_recent_payment_history_by_max_user(max_user_id: int, limit: int = 4) -> list[tuple]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            pr.id, pr.status, pr.caption_text, pr.created_at, pr.updated_at,
            COALESCE(
                (
                    SELECT SUM(bh.lessons_delta)
                    FROM balance_history bh
                    WHERE bh.operation_type = 'manual_topup'
                      AND COALESCE(bh.comment, '') LIKE '%#' || CAST(pr.id AS TEXT) || '%'
                ),
                0
            ) AS lessons_added
        FROM payment_requests pr
        WHERE pr.max_user_id = ? AND pr.source_platform = 'max'
        ORDER BY pr.id DESC
        LIMIT ?
        """,
        (max_user_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_recent_payment_history_by_student_id(student_id: int, limit: int = 4) -> list[tuple]:
    """Get recent payments for a student from both Telegram and MAX platforms."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT telegram_id, max_id FROM students WHERE id = ?", (student_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return []
    telegram_id, max_id = row

    conn = get_connection()
    cur = conn.cursor()
    conditions = []
    params: list = []
    if telegram_id:
        conditions.append("pr.telegram_user_id = ?")
        params.append(telegram_id)
    if max_id:
        conditions.append("pr.max_user_id = ?")
        params.append(max_id)
    if not conditions:
        conn.close()
        return []
    where_clause = " OR ".join(conditions)
    params.append(limit)
    cur.execute(
        f"""
        SELECT
            pr.id, pr.status, pr.caption_text, pr.created_at, pr.updated_at,
            COALESCE(
                (
                    SELECT SUM(bh.lessons_delta)
                    FROM balance_history bh
                    WHERE bh.operation_type = 'manual_topup'
                      AND COALESCE(bh.comment, '') LIKE '%#' || CAST(pr.id AS TEXT) || '%'
                ),
                0
            ) AS lessons_added,
            COALESCE(pr.source_platform, 'telegram') AS source_platform
        FROM payment_requests pr
        WHERE {where_clause}
        ORDER BY pr.id DESC
        LIMIT ?
        """,
        params,
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_active_promo_for_student_id(student_id: int):
    """Return active promo for student by student.id (id, code, discount_type, discount_value, applies_to_packages) or None."""
    _MSK = timezone(timedelta(hours=3))
    now = datetime.now(_MSK).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT pc.id, pc.code, pc.discount_type, pc.discount_value, pc.applies_to_packages, pc.valid_until
        FROM promo_codes pc
        JOIN student_promo_codes spc ON spc.promo_code_id = pc.id
        WHERE spc.student_id = ?
          AND pc.active = 1
          AND (pc.valid_until IS NULL OR pc.valid_until >= ?)
          AND (pc.max_uses IS NULL OR pc.used_count < pc.max_uses)
          AND spc.used_at IS NULL
        ORDER BY spc.assigned_at DESC
        LIMIT 1
        """,
        (student_id, now),
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_active_promo_for_user(telegram_user_id: int):
    """Return active promo code for user as (id, code, discount_type, discount_value, applies_to_packages) or None."""
    _MSK = timezone(timedelta(hours=3))
    now = datetime.now(_MSK).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT pc.id, pc.code, pc.discount_type, pc.discount_value, pc.applies_to_packages, pc.valid_until
        FROM promo_codes pc
        JOIN student_promo_codes spc ON spc.promo_code_id = pc.id
        JOIN students s ON s.id = spc.student_id
        WHERE s.telegram_id = ?
          AND pc.active = 1
          AND (pc.valid_until IS NULL OR pc.valid_until >= ?)
          AND (pc.max_uses IS NULL OR pc.used_count < pc.max_uses)
          AND spc.used_at IS NULL
        ORDER BY spc.assigned_at DESC
        LIMIT 1
        """,
        (telegram_user_id, now),
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_active_promo_for_max_user(max_user_id: int):
    """Same as get_active_promo_for_user but for MAX users."""
    _MSK = timezone(timedelta(hours=3))
    now = datetime.now(_MSK).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT pc.id, pc.code, pc.discount_type, pc.discount_value, pc.applies_to_packages, pc.valid_until
        FROM promo_codes pc
        JOIN student_promo_codes spc ON spc.promo_code_id = pc.id
        JOIN students s ON s.id = spc.student_id
        WHERE s.max_id = ?
          AND pc.active = 1
          AND (pc.valid_until IS NULL OR pc.valid_until >= ?)
          AND (pc.max_uses IS NULL OR pc.used_count < pc.max_uses)
          AND spc.used_at IS NULL
        ORDER BY spc.assigned_at DESC
        LIMIT 1
        """,
        (max_user_id, now),
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_student_max_id(student_id: int) -> int | None:
    """Return max_id for a student or None."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT max_id FROM students WHERE id = ?", (student_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def capture_referral_max(inviter_max_id: int, invitee_max_id: int) -> bool:
    """Record a MAX referral when an invitee opens the MAX bot via a referral link.

    Idempotent: skips self-referrals and existing rows. Returns True if inserted.
    """
    if not inviter_max_id or not invitee_max_id:
        return False
    if int(inviter_max_id) == int(invitee_max_id):
        return False
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT 1 FROM max_referrals WHERE invitee_max_id = ? LIMIT 1",
            (int(invitee_max_id),),
        )
        if cur.fetchone():
            return False
        cur.execute(
            """
            INSERT INTO max_referrals (inviter_max_id, invitee_max_id, status, created_at)
            VALUES (?, ?, 'captured', ?)
            """,
            (int(inviter_max_id), int(invitee_max_id), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def link_invitee_student_max(invitee_max_id: int, student_id: int) -> bool:
    """Tie a captured MAX referral to the freshly-linked student card."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE max_referrals
            SET invitee_student_id = ?,
                status = CASE WHEN status = 'captured' THEN 'student_linked' ELSE status END
            WHERE invitee_max_id = ? AND status IN ('captured', 'student_linked')
            """,
            (int(student_id), int(invitee_max_id)),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def award_referral_bonus_max_inviter(invitee_student_id: int, admin_id: int | None = None) -> dict | None:
    """Award bonus to MAX inviter when invitee makes their first payment.

    Looks up max_referrals for this invitee_student_id, credits bonus to inviter's
    oldest direction, and returns a dict with inviter_max_id and lesson details.
    """
    _BONUS = 1
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, inviter_max_id, status
        FROM max_referrals
        WHERE invitee_student_id = ? AND status = 'student_linked'
        LIMIT 1
        """,
        (int(invitee_student_id),),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    referral_id, inviter_max_id, _ = row

    inviter_students = find_students_by_max_id(int(inviter_max_id))
    if not inviter_students:
        return None
    inviter_student_id = inviter_students[0][0]
    inviter_direction = get_oldest_direction_for_student_id(inviter_student_id)
    if not inviter_direction:
        return None

    direction_id = int(inviter_direction[0])
    subject_name = inviter_direction[3]
    teacher_name = inviter_direction[7]

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE student_lessons SET lesson_balance = lesson_balance + ? WHERE id = ?",
            (_BONUS, direction_id),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return None
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            INSERT INTO balance_history (
                student_lesson_id, operation_type, lessons_delta, comment, created_at, created_by
            ) VALUES (?, 'referral_inviter_bonus', ?, ?, ?, ?)
            """,
            (direction_id, _BONUS,
             f"MAX реферальный бонус за ученика #{int(invitee_student_id)}", now, admin_id),
        )
        bonus_history_id = cur.lastrowid
        cur.execute(
            """
            UPDATE max_referrals
            SET status = 'rewarded', rewarded_at = ?, reward_balance_history_id = ?
            WHERE id = ? AND status = 'student_linked'
            """,
            (now, bonus_history_id, int(referral_id)),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return None
        conn.commit()
        return {
            "inviter_max_id": int(inviter_max_id),
            "direction_id": direction_id,
            "subject_name": subject_name,
            "teacher_name": teacher_name,
            "lessons_added": _BONUS,
        }
    except Exception:
        conn.rollback()
        return None
    finally:
        conn.close()


def create_promo_code(
    code: str,
    discount_type: str,
    discount_value: float,
    valid_until: str | None = None,
    max_uses: int | None = None,
    applies_to_packages: int = 0,
) -> int | None:
    """Create a new promo code. Returns the new id, or None if code already exists."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO promo_codes (code, discount_type, discount_value, valid_until, max_uses, applies_to_packages, active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            """,
            (code.upper().strip(), discount_type, discount_value, valid_until, max_uses, applies_to_packages),
        )
        conn.commit()
        row_id = cur.lastrowid
        conn.close()
        return row_id
    except Exception:
        conn.close()
        return None


def apply_promo_code_for_student(student_id: int, code: str) -> tuple[bool, str]:
    """Find promo by code text, validate, assign to student.

    Returns (True, "dtype:dvalue:applies_to_packages") on success or (False, reason) on failure.
    Reasons: not_found, inactive, expired, limit_reached, already_assigned, error
    """
    _MSK = timezone(timedelta(hours=3))
    now = datetime.now(_MSK).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT id, code, discount_type, discount_value, active, valid_until, max_uses, used_count, applies_to_packages
               FROM promo_codes WHERE code = ?""",
            (code.upper().strip(),),
        )
        row = cur.fetchone()
        if not row:
            return False, "not_found"
        promo_id, _, dtype, dvalue, active, valid_until, max_uses, used_count, applies_to_packages = row
        if not active:
            return False, "inactive"
        if valid_until and valid_until < now:
            return False, "expired"
        if max_uses is not None and used_count >= max_uses:
            return False, "limit_reached"
        cur.execute(
            "SELECT id, used_at FROM student_promo_codes WHERE student_id = ? AND promo_code_id = ?",
            (student_id, promo_id),
        )
        existing = cur.fetchone()
        if existing:
            if existing[1]:
                return False, "already_used"
            return False, "already_assigned"
        cur.execute(
            "INSERT INTO student_promo_codes (student_id, promo_code_id) VALUES (?, ?)",
            (student_id, promo_id),
        )
        conn.commit()
        return True, f"{dtype}:{dvalue}:{applies_to_packages}"
    except Exception:
        return False, "error"
    finally:
        conn.close()


def assign_promo_to_student(student_id: int, promo_code_id: int) -> bool:
    """Assign a promo code to a student. Returns False if already assigned."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO student_promo_codes (student_id, promo_code_id) VALUES (?, ?)",
            (student_id, promo_code_id),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.close()
        return False


def list_promo_codes() -> list[dict]:
    """Return all promo codes ordered by created_at desc."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT pc.id, pc.code, pc.discount_type, pc.discount_value,
               pc.valid_until, pc.max_uses, pc.used_count, pc.active,
               COUNT(spc.student_id) AS assigned_count
        FROM promo_codes pc
        LEFT JOIN student_promo_codes spc ON spc.promo_code_id = pc.id
        GROUP BY pc.id
        ORDER BY pc.id DESC
        """
    )
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "id": r[0], "code": r[1], "discount_type": r[2], "discount_value": r[3],
            "valid_until": r[4], "max_uses": r[5], "used_count": r[6],
            "active": r[7], "assigned_count": r[8],
        }
        for r in rows
    ]


def get_promo_code_by_id(promo_id: int) -> dict | None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, code, discount_type, discount_value, valid_until, max_uses, used_count, active FROM promo_codes WHERE id = ?", (promo_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "code": row[1], "discount_type": row[2], "discount_value": row[3],
            "valid_until": row[4], "max_uses": row[5], "used_count": row[6], "active": row[7]}


def deactivate_promo_code(promo_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE promo_codes SET active = 0 WHERE id = ?", (promo_id,))
    conn.commit()
    conn.close()
    return True


def activate_promo_code(promo_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE promo_codes SET active = 1 WHERE id = ?", (promo_id,))
    conn.commit()
    conn.close()
    return True


def delete_promo_code(promo_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM student_promo_codes WHERE promo_code_id = ?", (promo_id,))
    cur.execute("DELETE FROM promo_codes WHERE id = ?", (promo_id,))
    conn.commit()
    conn.close()
    return True


def consume_promo_for_student(student_id: int) -> bool:
    """Remove promo from student after payment confirmed and increment used_count."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT spc.promo_code_id FROM student_promo_codes spc
               JOIN promo_codes pc ON pc.id = spc.promo_code_id
               WHERE spc.student_id = ? AND pc.active = 1
               LIMIT 1""",
            (student_id,),
        )
        row = cur.fetchone()
        if not row:
            conn.close()
            return False
        promo_id = row[0]
        cur.execute("UPDATE promo_codes SET used_count = used_count + 1 WHERE id = ?", (promo_id,))
        cur.execute(
            "DELETE FROM student_promo_codes WHERE student_id = ? AND promo_code_id = ?",
            (student_id, promo_id),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.close()
        return False


def get_revenue_by_period() -> dict:
    """Return revenue stats grouped by week and month for the revenue sheet.

    Revenue comes from balance_history topups (actual money paid).
    For rows with amount_paid = 0 (historical data), falls back to lessons_delta * LESSON_PRICE.
    Teacher payout = lessons_delta * LESSON_RATE (always 1000 per lesson).
    Owner cut = revenue - teacher_payout.
    """
    lesson_price = int(os.getenv("LESSON_PRICE", "1500"))
    lesson_rate  = int(os.getenv("LESSON_RATE",  "1000"))

    conn = get_connection()
    cur = conn.cursor()

    # Revenue is based on topup payments (not attendance).
    # amount_paid stores the real sum; 0 means old record → fallback to lessons * price.
    week_sql = """
        SELECT
            TO_CHAR(bh.created_at::timestamp, 'IYYY-IW') AS week_key,
            MIN(bh.created_at) AS week_start,
            SUM(bh.lessons_delta) AS lessons,
            SUM(CASE WHEN COALESCE(bh.amount_paid, 0) > 0
                     THEN bh.amount_paid
                     ELSE bh.lessons_delta * %(price)s END) AS revenue,
            SUM(bh.lessons_delta) * %(rate)s AS teacher_pay
        FROM balance_history bh
        WHERE bh.operation_type = 'manual_topup'
          AND bh.lessons_delta > 0
        GROUP BY week_key
        ORDER BY week_key DESC
        LIMIT 8
    """
    month_sql = """
        SELECT
            TO_CHAR(bh.created_at::timestamp, 'YYYY-MM') AS month_key,
            SUM(bh.lessons_delta) AS lessons,
            SUM(CASE WHEN COALESCE(bh.amount_paid, 0) > 0
                     THEN bh.amount_paid
                     ELSE bh.lessons_delta * %(price)s END) AS revenue,
            SUM(bh.lessons_delta) * %(rate)s AS teacher_pay
        FROM balance_history bh
        WHERE bh.operation_type = 'manual_topup'
          AND bh.lessons_delta > 0
        GROUP BY month_key
        ORDER BY month_key DESC
        LIMIT 6
    """
    total_sql = """
        SELECT
            SUM(bh.lessons_delta) AS lessons,
            SUM(CASE WHEN COALESCE(bh.amount_paid, 0) > 0
                     THEN bh.amount_paid
                     ELSE bh.lessons_delta * %(price)s END) AS revenue,
            SUM(bh.lessons_delta) * %(rate)s AS teacher_pay
        FROM balance_history bh
        WHERE bh.operation_type = 'manual_topup'
          AND bh.lessons_delta > 0
    """

    params = {"price": lesson_price, "rate": lesson_rate}

    cur.execute(week_sql, params)
    weeks = []
    for r in cur.fetchall():
        revenue     = int(r[3] or 0)
        teacher_pay = int(r[4] or 0)
        weeks.append({
            "week_key":    str(r[0]),
            "week_start":  str(r[1]),
            "lessons":     int(r[2] or 0),
            "revenue":     revenue,
            "teacher_pay": teacher_pay,
            "owner_cut":   revenue - teacher_pay,
        })

    cur.execute(month_sql, params)
    months = []
    for r in cur.fetchall():
        revenue     = int(r[2] or 0)
        teacher_pay = int(r[3] or 0)
        months.append({
            "month_key":   str(r[0]),
            "lessons":     int(r[1] or 0),
            "revenue":     revenue,
            "teacher_pay": teacher_pay,
            "owner_cut":   revenue - teacher_pay,
        })

    cur.execute(total_sql, params)
    tr = cur.fetchone()
    total_lessons     = int(tr[0] or 0) if tr else 0
    total_revenue     = int(tr[1] or 0) if tr else 0
    total_teacher_pay = int(tr[2] or 0) if tr else 0

    conn.close()
    return {
        "weeks":             weeks,
        "months":            months,
        "total":             total_lessons,
        "total_revenue":     total_revenue,
        "total_teacher_pay": total_teacher_pay,
        "total_owner_cut":   total_revenue - total_teacher_pay,
    }


# ---------------------------------------------------------------------------
# Пополнения баланса — для листа «Пополнения» в Google Sheets
# ---------------------------------------------------------------------------

def get_topups_history() -> dict:
    """Вернуть историю пополнений балансов для листа «Пополнения».

    Включает manual_topup и initial_balance (lessons_delta > 0).
    Возвращает dict с rows (список операций) и агрегатами.
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            bh.created_at,
            s.full_name      AS student_name,
            sl.subject_name,
            t.full_name      AS teacher_name,
            bh.lessons_delta,
            bh.operation_type,
            bh.comment,
            COALESCE(u.full_name, '') AS created_by_name,
            COALESCE(bh.amount_paid, 0) AS amount_paid
        FROM balance_history bh
        JOIN student_lessons sl ON bh.student_lesson_id = sl.id
        JOIN students s         ON sl.student_id = s.id
        JOIN teachers t         ON sl.teacher_id = t.id
        LEFT JOIN users u       ON u.telegram_id = bh.created_by
        WHERE bh.operation_type IN ('manual_topup', 'initial_balance')
          AND bh.lessons_delta > 0
        ORDER BY bh.id DESC
        """
    )
    rows = [
        {
            "created_at":      r[0],
            "student_name":    r[1],
            "subject_name":    r[2],
            "teacher_name":    r[3],
            "lessons_delta":   r[4],
            "operation_type":  r[5],
            "comment":         r[6] or "",
            "created_by_name": r[7],
            "amount_paid":     int(r[8] or 0),
        }
        for r in cur.fetchall()
    ]

    # Aggregates
    from datetime import date as _date, timedelta as _td
    lesson_price = int(os.getenv("LESSON_PRICE", "1500"))
    today = _date.today()
    week_start = today - _td(days=today.weekday())
    month_start = today.replace(day=1)

    def _eff_amount(r: dict) -> int:
        return r["amount_paid"] if r["amount_paid"] > 0 else r["lessons_delta"] * lesson_price

    total_lessons  = sum(r["lessons_delta"] for r in rows)
    week_lessons   = sum(r["lessons_delta"] for r in rows
                         if r["created_at"][:10] >= week_start.isoformat())
    month_lessons  = sum(r["lessons_delta"] for r in rows
                         if r["created_at"][:10] >= month_start.isoformat())
    today_lessons  = sum(r["lessons_delta"] for r in rows
                         if r["created_at"][:10] == today.isoformat())
    total_revenue  = sum(_eff_amount(r) for r in rows)
    week_revenue   = sum(_eff_amount(r) for r in rows
                         if r["created_at"][:10] >= week_start.isoformat())
    month_revenue  = sum(_eff_amount(r) for r in rows
                         if r["created_at"][:10] >= month_start.isoformat())
    today_revenue  = sum(_eff_amount(r) for r in rows
                         if r["created_at"][:10] == today.isoformat())

    conn.close()
    return {
        "rows": rows,
        "total_lessons":  total_lessons,
        "week_lessons":   week_lessons,
        "month_lessons":  month_lessons,
        "today_lessons":  today_lessons,
        "total_revenue":  total_revenue,
        "week_revenue":   week_revenue,
        "month_revenue":  month_revenue,
        "today_revenue":  today_revenue,
    }


# ---------------------------------------------------------------------------
# Schedule slots
# ---------------------------------------------------------------------------

def add_schedule_slot(direction_id: int, schedule_type: str, day_of_week, specific_date, lesson_time: str, created_by: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    sql = (
        "INSERT INTO schedule_slots (direction_id, schedule_type, day_of_week, specific_date, lesson_time, created_by, is_active, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, 1, %s)"
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(sql, (direction_id, schedule_type, day_of_week, specific_date, lesson_time, created_by, now))
    conn.commit()
    slot_id = cur.lastrowid
    conn.close()
    return slot_id or 0


def get_teacher_schedule_slots(teacher_telegram_id: int) -> list:
    conn = get_connection()
    cur = conn.cursor()
    sql = """
        SELECT ss.id, ss.schedule_type, ss.day_of_week, ss.lesson_time, ss.specific_date,
               s.full_name AS student_name, subj.name AS subject_name
        FROM schedule_slots ss
        JOIN student_lessons sl ON sl.id = ss.direction_id
        JOIN students s ON s.id = sl.student_id
        JOIN teachers t ON t.id = sl.teacher_id
        LEFT JOIN teacher_subjects subj ON subj.teacher_id = t.id AND subj.id = sl.teacher_subject_id
        WHERE t.telegram_id = %s AND ss.is_active = 1
        ORDER BY ss.day_of_week NULLS LAST, ss.lesson_time
    """
    try:
        cur.execute(sql, (teacher_telegram_id,))
    except Exception:
        sql2 = """
            SELECT ss.id, ss.schedule_type, ss.day_of_week, ss.lesson_time, ss.specific_date,
                   s.full_name AS student_name, sl.subject_name AS subject_name
            FROM schedule_slots ss
            JOIN student_lessons sl ON sl.id = ss.direction_id
            JOIN students s ON s.id = sl.student_id
            JOIN teachers t ON t.id = sl.teacher_id
            WHERE t.telegram_id = %s AND ss.is_active = 1
            ORDER BY ss.day_of_week, ss.lesson_time
        """
        cur.execute(sql2, (teacher_telegram_id,))
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "id": r[0],
            "schedule_type": r[1],
            "day_of_week": r[2],
            "lesson_time": r[3],
            "specific_date": r[4],
            "student_name": r[5],
            "subject_name": r[6],
        }
        for r in rows
    ]


def get_schedule_slot_by_id(slot_id: int) -> dict:
    conn = get_connection()
    cur = conn.cursor()
    sql = (
        "SELECT id, schedule_type, day_of_week, lesson_time, specific_date, direction_id, created_by, is_active "
        "FROM schedule_slots WHERE id = %s"
    )
    cur.execute(sql, (slot_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return {}
    return {
        "id": row[0],
        "schedule_type": row[1],
        "day_of_week": row[2],
        "lesson_time": row[3],
        "specific_date": row[4],
        "direction_id": row[5],
        "created_by": row[6],
        "is_active": row[7],
    }


def deactivate_schedule_slot(slot_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    sql = "UPDATE schedule_slots SET is_active = 0 WHERE id = %s"
    cur.execute(sql, (slot_id,))
    conn.commit()
    conn.close()


def get_teacher_directions_for_schedule(teacher_telegram_id: int) -> list:
    conn = get_connection()
    cur = conn.cursor()
    sql = """
        SELECT sl.id, s.full_name AS student_name, sl.subject_name
        FROM student_lessons sl
        JOIN students s ON s.id = sl.student_id
        JOIN teachers t ON t.id = sl.teacher_id
        WHERE t.telegram_id = %s AND sl.is_active = 1
        ORDER BY s.full_name
    """
    try:
        cur.execute(sql, (teacher_telegram_id,))
        rows = cur.fetchall()
    except Exception:
        rows = []
    conn.close()
    return [{"id": r[0], "student_name": r[1], "subject_name": r[2]} for r in rows]


def get_week_schedule_for_sheet(week_start: str, week_end: str) -> list:
    """Return all active recurring/one_time slots that fall within [week_start, week_end].

    week_start / week_end are ISO date strings 'YYYY-MM-DD'.
    Returns list of dicts with keys:
      lesson_date, lesson_time, teacher_name, student_name, subject_name, attendance_status
    """
    from datetime import date as date_cls, timedelta as td
    try:
        ws = date_cls.fromisoformat(week_start)
        we = date_cls.fromisoformat(week_end)
    except ValueError:
        return []

    conn = get_connection()
    cur = conn.cursor()

    # Pull all active slots with teacher/student/subject info
    sql = """
        SELECT ss.id, ss.schedule_type, ss.day_of_week, ss.specific_date, ss.lesson_time,
               t.full_name AS teacher_name, s.full_name AS student_name, sl.subject_name
        FROM schedule_slots ss
        JOIN student_lessons sl ON sl.id = ss.direction_id
        JOIN students s ON s.id = sl.student_id
        JOIN teachers t ON t.id = sl.teacher_id
        WHERE ss.is_active = 1
    """
    cur.execute(sql)
    slots = cur.fetchall()
    conn.close()

    results = []
    current = ws
    while current <= we:
        dow = current.weekday()  # 0=Mon
        for slot in slots:
            _, stype, day_of_week, specific_date, lesson_time, teacher_name, student_name, subject_name = slot
            match = False
            if stype == "recurring" and day_of_week is not None and int(day_of_week) == dow:
                match = True
            elif stype == "one_time" and specific_date:
                try:
                    if date_cls.fromisoformat(specific_date) == current:
                        match = True
                except ValueError:
                    pass
            if match:
                results.append({
                    "lesson_date": current,
                    "lesson_time": lesson_time or "",
                    "teacher_name": teacher_name or "",
                    "student_name": student_name or "",
                    "subject_name": subject_name or "",
                    "attendance_status": None,
                })
        current += td(days=1)

    results.sort(key=lambda x: (x["lesson_date"], x["lesson_time"]))
    return results
