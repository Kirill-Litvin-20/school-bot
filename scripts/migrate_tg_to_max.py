"""Migrate posts from @school_integral_ru to MAX channel via web scraping.

No API credentials needed — scrapes the public t.me/s/ page.

Run on server:
    cd /opt/school-system
    .venv/bin/python3 scripts/migrate_tg_to_max.py
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path
from html.parser import HTMLParser

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

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

import aiohttp
from shared.max_api import MaxApiClient

MAX_BOT_TOKEN = os.environ["SCHOOL_MAX_BOT_TOKEN"]
MAX_CHANNEL_ID = int(os.environ["SCHOOL_MAX_CHANNEL_ID"])
TG_CHANNEL = "school_integral_ru"
DELAY = 2.0

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}


class TgPageParser(HTMLParser):
    """Extract posts from t.me/s/channel HTML."""

    def __init__(self):
        super().__init__()
        self.posts: list[dict] = []  # {"text": str, "photo": str|None, "has_poll": bool, "msg_id": int|None}
        self._cur: dict | None = None
        self._in_text = False
        self._text_depth = 0
        self._in_photo = False
        self._tag_stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        cls = attrs_d.get("class", "")
        self._tag_stack.append(tag)

        if "tgme_widget_message_wrap" in cls:
            if self._cur is not None:
                self._flush()
            self._cur = {"text": "", "photo": None, "has_poll": False, "msg_id": None}

        if self._cur is not None:
            # Photo: background-image in style
            if "tgme_widget_message_photo_wrap" in cls:
                style = attrs_d.get("style", "")
                m = re.search(r"url\('([^']+)'\)", style)
                if m:
                    self._cur["photo"] = m.group(1)

            # Detect poll: media_not_supported_cont block
            if "media_not_supported_cont" in cls:
                self._cur["has_poll"] = True

            # Grab message id from data-post attribute
            if "tgme_widget_message" in cls and attrs_d.get("data-post"):
                m = re.search(r"/(\d+)$", attrs_d["data-post"])
                if m:
                    self._cur["msg_id"] = int(m.group(1))

            if "tgme_widget_message_text" in cls and "js-message_text" in cls:
                self._in_text = True
                self._text_depth = len(self._tag_stack)

        if self._in_text and tag == "br":
            if self._cur:
                self._cur["text"] += "\n"

    def handle_endtag(self, tag):
        if self._in_text and len(self._tag_stack) <= self._text_depth:
            self._in_text = False
        if self._tag_stack:
            self._tag_stack.pop()

    def handle_data(self, data):
        if self._in_text and self._cur is not None:
            self._cur["text"] += data

    def _flush(self):
        if self._cur is None:
            return
        text = self._cur["text"].strip()
        photo = self._cur.get("photo")
        if text or photo:
            self.posts.append({"text": text, "photo": photo})
        self._cur = None

    def close(self):
        self._flush()
        super().close()


async def fetch_page(session: aiohttp.ClientSession, before: int | None = None) -> str:
    url = f"https://t.me/s/{TG_CHANNEL}"
    if before:
        url += f"?before={before}"
    async with session.get(url, headers=HEADERS) as resp:
        resp.raise_for_status()
        return await resp.text()


def parse_posts(html: str) -> tuple[list[dict], int | None]:
    """Returns (posts, oldest_message_id_for_pagination)."""
    parser = TgPageParser()
    parser.feed(html)
    parser.close()
    posts = parser.posts

    # Find oldest message id for pagination ("before" param)
    ids = re.findall(r'data-post="[^/]+/(\d+)"', html)
    oldest_id = min(int(x) for x in ids) if ids else None

    return posts, oldest_id


async def collect_all_posts(session: aiohttp.ClientSession) -> list[dict]:
    all_posts: list[dict] = []
    before = None
    seen_ids: set[int] = set()

    while True:
        html = await fetch_page(session, before)
        posts, oldest_id = parse_posts(html)

        if not posts or oldest_id in seen_ids:
            break

        seen_ids.add(oldest_id)
        all_posts = posts + all_posts

        print(f"  fetched page (before={before}), got {len(posts)} posts, oldest_id={oldest_id}")

        if oldest_id is None or oldest_id <= 1:
            break

        before = oldest_id
        await asyncio.sleep(1)

    return all_posts


async def migrate() -> None:
    max_api = MaxApiClient(MAX_BOT_TOKEN)

    print(f"Scraping https://t.me/s/{TG_CHANNEL} ...")
    async with aiohttp.ClientSession() as session:
        all_posts = await collect_all_posts(session)

    print(f"\nTotal posts collected: {len(all_posts)}")
    print("Starting migration to MAX channel...\n")

    for i, post in enumerate(all_posts, 1):
        text = post["text"]
        photo = post["photo"]
        preview = text[:60].replace("\n", " ")
        print(f"[{i}/{len(all_posts)}] {'photo' if photo else 'text'}: {preview!r}")

        try:
            if photo:
                await max_api.send_photo_to_chat(MAX_CHANNEL_ID, photo, caption=text)
            elif text:
                await max_api.send_to_chat(MAX_CHANNEL_ID, text)
            else:
                print("  skip (empty)")
                continue
            print("  ✓ sent")
        except Exception as exc:
            print(f"  ✗ ERROR: {exc}")

        await asyncio.sleep(DELAY)

    print(f"\nDone!")


if __name__ == "__main__":
    asyncio.run(migrate())
