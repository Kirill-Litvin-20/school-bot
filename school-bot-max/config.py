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

MAX_BOT_TOKEN = _require("SCHOOL_MAX_BOT_TOKEN")

# TG bot token — used only for forwarding payment receipts and applications to TG chats
TG_BOT_TOKEN = _require("SCHOOL_BOT_TOKEN")
TG_BOT_USERNAME = os.getenv("SCHOOL_BOT_USERNAME", "integral_school_ru_bot")
PAYMENTS_CHAT_ID = _require_int("SCHOOL_BOT_PAYMENTS_CHAT_ID")
APPLICATIONS_CHAT_ID = _require_int("SCHOOL_BOT_APPLICATIONS_CHAT_ID")

# Платёжные реквизиты (shared with TG bot)
PAYMENT_BANK_NUMBER = os.getenv("SCHOOL_PAYMENT_BANK_NUMBER", "89996604789")
PAYMENT_BANK_NAME = os.getenv("SCHOOL_PAYMENT_BANK_NAME", "СБЕР")
PAYMENT_ACCOUNT_HOLDER = os.getenv("SCHOOL_PAYMENT_ACCOUNT_HOLDER", "Александр Сергеевич К.")
LESSON_PRICE = int(os.getenv("LESSON_PRICE", "0"))
PACKAGE_PRICES: dict[int, int] = {}
MAX_ADMIN_USERNAME = os.getenv("MAX_ADMIN_USERNAME", "")
MAX_ADMIN_TG_USERNAME = os.getenv("MAX_ADMIN_TG_USERNAME", "integral_school_ru")
MAX_ADMIN_PHONE = os.getenv("MAX_ADMIN_PHONE", "")
MAX_BOT_USERNAME = os.getenv("SCHOOL_MAX_BOT_USERNAME", "")
PAYMENT_PHOTO_FILE_ID = os.getenv("SCHOOL_PAYMENT_PHOTO_FILE_ID", "")
TARIFF_PHOTO_FILE_ID = os.getenv("SCHOOL_TARIFF_PHOTO_FILE_ID", "")
_raw_packages = os.getenv("PACKAGE_PRICES", "")
for _item in _raw_packages.split(","):
    _item = _item.strip()
    if ":" in _item:
        _k, _v = _item.split(":", 1)
        try:
            PACKAGE_PRICES[int(_k.strip())] = int(_v.strip())
        except ValueError:
            pass
