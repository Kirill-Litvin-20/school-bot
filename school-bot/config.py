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

# MAX канал — авто-зеркало постов из TG-канала
MAX_BOT_TOKEN = os.getenv("SCHOOL_MAX_BOT_TOKEN", "")
_max_channel_id_str = os.getenv("SCHOOL_MAX_CHANNEL_ID", "")
MAX_CHANNEL_ID: int = int(_max_channel_id_str) if _max_channel_id_str else 0
TG_CHANNEL_USERNAME = os.getenv("SCHOOL_TG_CHANNEL_USERNAME", "school_integral_ru")

# Платёжные реквизиты
PAYMENT_BANK_NUMBER = os.getenv("SCHOOL_PAYMENT_BANK_NUMBER", "89996604789")
PAYMENT_BANK_NAME = os.getenv("SCHOOL_PAYMENT_BANK_NAME", "СБЕР")
PAYMENT_ACCOUNT_HOLDER = os.getenv("SCHOOL_PAYMENT_ACCOUNT_HOLDER", "Александр Сергеевич К.")
PAYMENT_PHOTO_FILE_ID = os.getenv("SCHOOL_PAYMENT_PHOTO_FILE_ID", "AgACAgIAAxkBAAINs2n8aaX7z3Gpm8As2nPF20YRMf3ZAALgEmsbVC_oS5OzEg8MiakgAQADAgADeQADOwQ")
LESSON_PRICE = int(os.getenv("LESSON_PRICE", "0"))
PACKAGE_PRICES: dict[int, int] = {}  # lessons_count -> price, loaded below
_raw_packages = os.getenv("PACKAGE_PRICES", "")  # format: "4:5000,8:9000,12:13000"
for _item in _raw_packages.split(","):
    _item = _item.strip()
    if ":" in _item:
        _k, _v = _item.split(":", 1)
        try:
            PACKAGE_PRICES[int(_k.strip())] = int(_v.strip())
        except ValueError:
            pass
