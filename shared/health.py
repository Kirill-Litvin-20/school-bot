"""Tiny aiohttp /health endpoint shared by both bots.

The web server is opt-in: it only starts when an explicit port env var is set,
so local dev keeps using just polling. On the production server, set
`SCHOOL_HEALTH_PORT` per service unit to expose `/health`.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from aiohttp import web

logger = logging.getLogger(__name__)


class HealthState:
    """Single mutable object the bot pokes after each successful update.

    `last_update_ts` lets the endpoint report how long ago the bot processed
    something — useful for asking 'is it stuck?' from outside.
    """

    def __init__(self) -> None:
        self.started_at = time.monotonic()
        self.last_update_ts: float | None = None

    def touch(self) -> None:
        self.last_update_ts = time.monotonic()

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        return {
            "status": "ok",
            "uptime_seconds": int(now - self.started_at),
            "seconds_since_last_update": (
                int(now - self.last_update_ts) if self.last_update_ts else None
            ),
        }


async def _make_handler(state: HealthState):
    async def handler(_: web.Request) -> web.Response:
        return web.json_response(state.snapshot())

    return handler


async def start_health_server(
    *,
    app_name: str,
    port_env: str,
    default_port: int | None = None,
) -> tuple[web.AppRunner | None, HealthState]:
    """Start the /health server if `port_env` (or `default_port`) is set.

    Returns the aiohttp runner so the caller can `cleanup()` on shutdown, and
    the `HealthState` it should `touch()` on each handled update.
    """
    state = HealthState()
    raw_port = os.getenv(port_env)
    port = None
    if raw_port and raw_port.strip().isdigit():
        port = int(raw_port.strip())
    elif default_port is not None:
        port = default_port

    if port is None:
        logger.info("Health endpoint disabled for %s (set %s to enable).", app_name, port_env)
        return None, state

    handler = await _make_handler(state)
    app = web.Application()
    app.router.add_get("/health", handler)
    app.router.add_get("/", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    bind_host = os.getenv("SCHOOL_HEALTH_BIND", "127.0.0.1")
    site = web.TCPSite(runner, bind_host, port)
    await site.start()
    logger.info("Health endpoint for %s listening on %s:%s", app_name, bind_host, port)
    return runner, state
