"""Thin async HTTP client for the MAX messenger Bot API.

Docs: https://dev.max.ru/docs-api
Base URL: https://botapi.max.ru
Auth: ?access_token=TOKEN query parameter on every request.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

_BASE = "https://botapi.max.ru"


def _kb(*rows: list[dict]) -> dict:
    """Build an inline_keyboard attachment from button rows."""
    return {
        "type": "inline_keyboard",
        "payload": {"buttons": list(rows)},
    }


def btn(text: str, payload: str) -> dict:
    return {"type": "callback", "text": text, "payload": payload}


def btn_url(text: str, url: str) -> dict:
    return {"type": "link", "text": text, "url": url}


def keyboard(*rows: list[dict]) -> list[dict]:
    """Return an attachments list with one inline_keyboard."""
    return [_kb(*rows)]


class MaxApiClient:
    def __init__(self, token: str) -> None:
        self._token = token
        self._headers = {"Authorization": token}

    # ── low-level ──────────────────────────────────────────────────────────

    async def _get(self, path: str, **params) -> dict:
        url = f"{_BASE}{path}"
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.get(url, params=params or None) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def _post(self, path: str, body: dict, **params) -> dict:
        url = f"{_BASE}{path}"
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.post(url, json=body, params=params or None) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def _patch(self, path: str, body: dict, **params) -> dict:
        url = f"{_BASE}{path}"
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.patch(url, json=body, params=params or None) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def _put(self, path: str, body: dict, **params) -> dict:
        url = f"{_BASE}{path}"
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.put(url, json=body, params=params or None) as resp:
                resp.raise_for_status()
                return await resp.json()

    # ── updates ────────────────────────────────────────────────────────────

    async def get_updates(self, offset: int = 0, timeout: int = 25) -> dict:
        return await self._get("/updates", offset=offset, timeout=timeout)

    # ── messaging ──────────────────────────────────────────────────────────

    async def send_message(
        self,
        user_id: int,
        text: str,
        attachments: list[dict] | None = None,
    ) -> dict:
        body: dict[str, Any] = {"text": text}
        if attachments:
            body["attachments"] = attachments
        return await self._post("/messages", body, user_id=user_id)

    async def send_to_chat(
        self,
        chat_id: int,
        text: str,
        attachments: list[dict] | None = None,
    ) -> dict:
        body: dict[str, Any] = {"text": text}
        if attachments:
            body["attachments"] = attachments
        return await self._post("/messages", body, chat_id=chat_id)

    async def send_photo_to_chat(
        self,
        chat_id: int,
        url: str,
        caption: str = "",
    ) -> dict:
        photo_att = {"type": "image", "payload": {"url": url}}
        body: dict[str, Any] = {"attachments": [photo_att]}
        if caption:
            body["text"] = caption
        return await self._post("/messages", body, chat_id=chat_id)

    async def send_photo_url(
        self,
        user_id: int,
        url: str,
        caption: str = "",
        attachments: list[dict] | None = None,
    ) -> dict:
        photo_att = {"type": "image", "payload": {"url": url}}
        atts = [photo_att] + (attachments or [])
        body: dict[str, Any] = {"attachments": atts}
        if caption:
            body["text"] = caption
        return await self._post("/messages", body, user_id=user_id)

    async def answer_callback(
        self,
        callback_id: str,
        notification: str = "",
    ) -> dict:
        body: dict[str, Any] = {"callback_id": callback_id}
        if notification:
            body["notification"] = notification
        try:
            return await self._post("/answers", body)
        except Exception as exc:
            logger.debug("answer_callback failed (non-critical): %s", exc)
            return {}

    # ── file download ───────────────────────────────────────────────────────

    async def download_bytes(self, url: str) -> bytes:
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                return await resp.read()

    async def edit_message(
        self,
        message_id: str,
        text: str,
        attachments: list[dict] | None = None,
    ) -> dict:
        body: dict[str, Any] = {"text": text}
        if attachments:
            body["attachments"] = attachments
        return await self._put("/messages", body, message_id=message_id)

    # ── bot info ───────────────────────────────────────────────────────────

    async def get_me(self) -> dict:
        return await self._get("/me")
