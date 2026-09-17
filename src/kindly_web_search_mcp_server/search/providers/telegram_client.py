"""Telethon client singleton for Telegram search provider.

Lazy-initialized, shared across content resolver and search provider.
Uses StringSession for server deployments (no .session file needed).
receive_updates=False — no event handlers, search-only mode.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import cast

from telethon import TelegramClient
from telethon.sessions import StringSession

from ...settings import settings

logger = logging.getLogger(__name__)

_client: TelegramClient | None = None
_client_lock: asyncio.Lock | None = None


def _get_client_lock() -> asyncio.Lock:
    global _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    return _client_lock


async def get_telethon_client() -> TelegramClient:
    """Return a connected Telethon client (lazy singleton)."""
    global _client
    if _client is not None and _client.is_connected():
        return _client

    async with _get_client_lock():
        if _client is not None and _client.is_connected():
            return _client

        if not settings.telegram_api_id or not settings.telegram_api_hash:
            raise TelegramConfigError(
                "TELEGRAM_API_ID and TELEGRAM_API_HASH must be set. "
                "Get them at https://my.telegram.org/apps"
            )

        session = (
            StringSession(settings.telegram_session_string)
            if settings.telegram_session_string
            else StringSession("")
        )

        _client = TelegramClient(
            session,
            int(settings.telegram_api_id),
            settings.telegram_api_hash,
            flood_sleep_threshold=settings.telegram_flood_sleep_threshold,
            receive_updates=False,
        )
        await cast("Awaitable[TelegramClient]", _client.start())
        logger.info("Telethon client connected (api_id=%s)", settings.telegram_api_id)
        return _client


async def disconnect_telethon_client() -> None:
    """Disconnect the singleton client (for graceful shutdown)."""
    global _client
    if _client is not None:
        await _client.disconnect()
        _client = None


class TelegramConfigError(RuntimeError):
    """Missing or invalid Telegram configuration."""
