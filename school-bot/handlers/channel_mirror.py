"""Mirror posts from TG channel @school_integral_ru to MAX channel."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.types import Message

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(ROOT_DIR))

from config import BOT_TOKEN, MAX_BOT_TOKEN, MAX_CHANNEL_ID, TG_CHANNEL_USERNAME
from shared.max_api import MaxApiClient

logger = logging.getLogger(__name__)

router = Router()


def _make_max_client() -> MaxApiClient | None:
    if not MAX_BOT_TOKEN or not MAX_CHANNEL_ID:
        return None
    return MaxApiClient(MAX_BOT_TOKEN)


async def _post_to_max(text: str, photo_url: str | None = None) -> None:
    api = _make_max_client()
    if api is None:
        return
    try:
        if photo_url:
            await api.send_photo_to_chat(MAX_CHANNEL_ID, photo_url, caption=text)
        else:
            await api.send_to_chat(MAX_CHANNEL_ID, text)
    except Exception as exc:
        logger.warning("MAX channel mirror failed: %s", exc)


@router.channel_post(F.chat.username == TG_CHANNEL_USERNAME)
async def mirror_channel_post(message: Message, bot: Bot) -> None:
    caption = (message.caption or "").strip()
    text = (message.text or "").strip()

    if message.photo:
        largest = message.photo[-1]
        try:
            file = await bot.get_file(largest.file_id)
            photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
        except Exception as exc:
            logger.warning("Could not get photo URL for mirroring: %s", exc)
            photo_url = None
        await _post_to_max(caption, photo_url)

    elif message.video:
        body = caption or text
        if body:
            await _post_to_max(body)

    elif text:
        await _post_to_max(text)
