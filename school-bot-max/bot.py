"""MAX bot entry point.

Long-polling loop over MAX Bot API. Dispatches updates to handlers.py.
"""

import asyncio
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import MAX_BOT_TOKEN
from handlers import (
    handle_bot_started,
    handle_callback,
    handle_file,
    handle_photo,
    handle_text,
)
from shared.database import init_db
from shared.logging_setup import get_log_settings, setup_logging
from shared.max_api import MaxApiClient


async def process_update(api: MaxApiClient, update: dict) -> None:
    update_type = update.get("update_type", "")

    try:
        if update_type == "bot_started":
            user = update.get("user", {})
            await handle_bot_started(
                api=api,
                user_id=user.get("user_id"),
                name=user.get("name", ""),
                username=user.get("username"),
            )

        elif update_type == "message_created":
            msg = update.get("message", {})
            sender = msg.get("sender", {})
            user_id = sender.get("user_id")
            username = sender.get("username")
            name = sender.get("name", "")
            body = msg.get("body", {}) or {}
            text = (body.get("text") or "").strip()
            attachments = msg.get("attachments") or []

            photo_att = next((a for a in attachments if a.get("type") == "photo"), None)
            file_att = next((a for a in attachments if a.get("type") == "file"), None)

            if photo_att:
                photo_url = (photo_att.get("payload") or {}).get("url", "")
                await handle_photo(api, user_id, username, name, photo_url, text or None)
            elif file_att:
                payload = file_att.get("payload") or {}
                await handle_file(
                    api,
                    user_id,
                    username,
                    name,
                    payload.get("url", ""),
                    payload.get("filename", ""),
                    text or None,
                )
            elif text:
                await handle_text(api, user_id, username, name, text)

        elif update_type == "message_callback":
            cb = update.get("callback", {})
            user = cb.get("user", {})
            await handle_callback(
                api=api,
                callback_id=cb.get("callback_id", ""),
                user_id=user.get("user_id"),
                username=user.get("username"),
                name=user.get("name", ""),
                payload=cb.get("payload", ""),
            )

    except Exception:
        logging.getLogger(__name__).exception(
            "Unhandled error processing update_type=%s", update_type
        )


async def polling_loop(api: MaxApiClient) -> None:
    logger = logging.getLogger(__name__)
    marker = 0
    logger.info("MAX bot polling started")

    while True:
        try:
            result = await api.get_updates(offset=marker, timeout=25)
            updates = result.get("updates") or []
            if updates:
                marker = result.get("marker", marker)
                for update in updates:
                    await process_update(api, update)
        except asyncio.CancelledError:
            logger.info("Polling cancelled, shutting down")
            break
        except Exception as exc:
            logger.warning("Polling error, retry in 5s: %s", exc)
            await asyncio.sleep(5)


async def main() -> None:
    log_level, log_dir = get_log_settings()
    log_file = setup_logging("max_bot", log_level=log_level, log_dir=log_dir)
    logging.getLogger(__name__).info("Starting MAX bot, log: %s", log_file)

    init_db()

    api = MaxApiClient(MAX_BOT_TOKEN)
    try:
        me = await api.get_me()
        logging.getLogger(__name__).info("MAX bot identity: %s", me)
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not fetch bot info: %s", exc)

    await polling_loop(api)


if __name__ == "__main__":
    asyncio.run(main())
