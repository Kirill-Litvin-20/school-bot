import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def _is_running_under_systemd() -> bool:
    # systemd injects INVOCATION_ID into every service's environment.
    return bool(os.getenv("INVOCATION_ID"))


def _should_write_log_file() -> bool:
    explicit = os.getenv("SCHOOL_LOG_TO_FILE")
    if explicit is not None:
        return explicit.strip().lower() in {"1", "true", "yes", "on"}
    # Default: skip the local file when running under systemd because journald
    # already captures stdout/stderr (and applies its own retention).
    return not _is_running_under_systemd()


def setup_logging(app_name: str, log_level: str = "INFO", log_dir: str = "logs") -> Path | None:
    level = getattr(logging, log_level.upper(), logging.INFO)

    # journald already prefixes every line with a timestamp and unit name, so a
    # leaner formatter avoids duplication when the same logs are tailed both
    # via `journalctl` and from the on-disk file.
    if _is_running_under_systemd():
        formatter = logging.Formatter(
            f"[{app_name}] %(levelname)s | %(name)s | %(message)s"
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    log_file: Path | None = None
    if _should_write_log_file():
        logs_path = Path(log_dir).resolve()
        logs_path.mkdir(parents=True, exist_ok=True)
        log_file = logs_path / f"{app_name}.log"
        file_handler = RotatingFileHandler(
            filename=log_file,
            maxBytes=2 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    logging.getLogger("aiogram").setLevel(level)
    logging.getLogger("aiohttp").setLevel(max(level, logging.WARNING))

    return log_file


def get_log_settings(default_level: str = "INFO", default_dir: str = "logs") -> tuple[str, str]:
    return (
        os.getenv("SCHOOL_LOG_LEVEL", default_level),
        os.getenv("SCHOOL_LOG_DIR", default_dir),
    )
