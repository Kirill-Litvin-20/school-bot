import os
from pathlib import Path


def _load_env() -> None:
    env_file_override = os.environ.get("ENV_FILE", "").strip()
    if env_file_override:
        env_path = Path(env_file_override)
    else:
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


def _parse_int_list(raw: str) -> list[int]:
    result: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.append(int(part))
        except ValueError as exc:
            raise RuntimeError(f"SCHOOL_ADMIN_SUPERADMINS must be comma-separated ints, got: {raw}") from exc
    if not result:
        raise RuntimeError("SCHOOL_ADMIN_SUPERADMINS must contain at least one id")
    return result


def _parse_optional_int(key: str) -> int | None:
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{key} must be an integer, got: {raw}") from exc


_load_env()

BOT_TOKEN = _require("SCHOOL_ADMIN_BOT_TOKEN")
SUPERADMINS = _parse_int_list(_require("SCHOOL_ADMIN_SUPERADMINS"))
SCHOOL_BOT_TOKEN = os.getenv("SCHOOL_BOT_TOKEN", "").strip() or None
SCHOOL_BOT_USERNAME = os.getenv("SCHOOL_BOT_USERNAME", "").strip().lstrip("@") or None
SCHOOL_BOT_PAYMENTS_CHAT_ID = _parse_optional_int("SCHOOL_BOT_PAYMENTS_CHAT_ID")
MAX_BOT_TOKEN = os.getenv("SCHOOL_MAX_BOT_TOKEN", "").strip() or None
TEST_MODE: bool = os.getenv("TEST_MODE", "0").strip() == "1"
