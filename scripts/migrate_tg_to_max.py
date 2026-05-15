"""Migrate posts from @school_integral_ru TG channel to MAX channel.

Run on server:
    cd /opt/school-system
    .venv/bin/python3 scripts/migrate_tg_to_max.py

First run: Pyrogram will ask for phone number + OTP (one time).
Session is saved to scripts/school_migrate_session.session.

Requires env vars (already in /opt/school-system/.env):
    TG_API_ID           — from https://my.telegram.org/apps  (add to .env)
    TG_API_HASH         — from https://my.telegram.org/apps  (add to .env)
    SCHOOL_MAX_BOT_TOKEN
    SCHOOL_MAX_CHANNEL_ID
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

# Load .env
env_path = ROOT_DIR / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v

from pyrogram import Client
from pyrogram.types import Message

from shared.max_api import MaxApiClient

TG_API_ID_STR = os.environ.get("TG_API_ID", "")
TG_API_HASH = os.environ.get("TG_API_HASH", "")
MAX_BOT_TOKEN = os.environ["SCHOOL_MAX_BOT_TOKEN"]
MAX_CHANNEL_ID = int(os.environ["SCHOOL_MAX_CHANNEL_ID"])
TG_CHANNEL = "school_integral_ru"

ASSETS_DIR = ROOT_DIR / "assets" / "migrate"
SERVER_BASE_URL = "http://151.243.176.132"
DELAY = 1.5  # seconds between posts

if not TG_API_ID_STR or not TG_API_HASH:
    print("ERROR: TG_API_ID and TG_API_HASH are required.")
    print("Get them at https://my.telegram.org/apps and add to .env:")
    print("  TG_API_ID=123456")
    print("  TG_API_HASH=abcdef...")
    sys.exit(1)

ASSETS_DIR.mkdir(parents=True, exist_ok=True)


async def migrate() -> None:
    max_api = MaxApiClient(MAX_BOT_TOKEN)

    app = Client(
        "school_migrate_session",
        api_id=int(TG_API_ID_STR),
        api_hash=TG_API_HASH,
        workdir=str(Path(__file__).parent),
    )

    print("Connecting to Telegram as user...")
    async with app:
        print(f"Fetching posts from @{TG_CHANNEL}...")
        messages: list[Message] = []
        async for msg in app.get_chat_history(TG_CHANNEL):
            if msg.text or msg.caption or msg.photo:
                messages.append(msg)

        messages = list(reversed(messages))  # oldest first
        total = len(messages)
        print(f"Found {total} posts. Starting migration...\n")

        for i, msg in enumerate(messages, 1):
            text = (msg.text or msg.caption or "").strip()
            msg_type = "photo" if msg.photo else "text"
            preview = text[:60].replace("\n", " ")
            print(f"[{i}/{total}] {msg_type}: {preview!r}")

            try:
                if msg.photo:
                    fname = f"{uuid.uuid4().hex}.jpg"
                    local_path = ASSETS_DIR / fname
                    await app.download_media(msg.photo, file_name=str(local_path))
                    photo_url = f"{SERVER_BASE_URL}/assets/migrate/{fname}"
                    await max_api.send_photo_to_chat(MAX_CHANNEL_ID, photo_url, caption=text)
                elif text:
                    await max_api.send_to_chat(MAX_CHANNEL_ID, text)
                else:
                    continue

                print(f"  ✓ sent")

            except Exception as exc:
                print(f"  ✗ ERROR: {exc}")

            await asyncio.sleep(DELAY)

    print(f"\nDone! {total} posts processed.")


if __name__ == "__main__":
    asyncio.run(migrate())
