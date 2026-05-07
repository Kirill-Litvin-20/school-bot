import os
from pathlib import Path


def _load_env() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"Missing required env var: {key}")
    return value


def _require_int(key: str) -> int:
    value = _require(key)
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"Env var {key} must be int, got: {value}") from exc


_load_env()

BOT_TOKEN = _require("SCHOOL_BOT_TOKEN")
ADMIN_ID = _require_int("SCHOOL_BOT_ADMIN_ID")
PAYMENTS_CHAT_ID = _require_int("SCHOOL_BOT_PAYMENTS_CHAT_ID")
APPLICATIONS_CHAT_ID = _require_int("SCHOOL_BOT_APPLICATIONS_CHAT_ID")

# Платёжные реквизиты
PAYMENT_BANK_NUMBER = os.getenv("SCHOOL_PAYMENT_BANK_NUMBER", "89996604789")
PAYMENT_BANK_NAME = os.getenv("SCHOOL_PAYMENT_BANK_NAME", "СБЕР")
PAYMENT_ACCOUNT_HOLDER = os.getenv("SCHOOL_PAYMENT_ACCOUNT_HOLDER", "Александр Сергеевич К.")
