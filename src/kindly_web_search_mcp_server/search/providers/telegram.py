"""Telegram search provider via Telethon MTProto.

Tier 1: searchGlobal — searches channels the account has joined (free, unlimited).
Tier 2: searchPosts — searches ALL public channels (limited daily budget, Premium only).

Telethon's iter_messages(None, search=query) triggers messages.searchGlobal internally.
searchPosts is called via raw Telethon functions for global public channel search.
"""

from __future__ import annotations

from .base import ProviderRequestError

import logging
from typing import Any

import httpx

from ...models import WebSearchResult
from ...settings import settings

logger = logging.getLogger(__name__)


class TelegramSearchError(ProviderRequestError):
    pass


def _message_link(msg: Any) -> str:
    """Build a t.me link from a Telethon message object."""
    chat = getattr(msg, "chat", None)
    if chat and hasattr(chat, "username") and chat.username:
        return f"https://t.me/{chat.username}/{msg.id}"
    if chat and hasattr(chat, "id") and chat.id:
        chat_id = str(chat.id).replace("-100", "")
        return f"https://t.me/c/{chat_id}/{msg.id}"
    return ""


def _chat_title(msg: Any) -> str:
    """Extract chat title from a message."""
    chat = getattr(msg, "chat", None)
    if chat and hasattr(chat, "title"):
        return chat.title or "Telegram"
    return "Telegram"


async def search_telegram(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
) -> list[WebSearchResult]:
    """Search Telegram channels via Telethon MTProto.

    Tier 1: searchGlobal across joined channels (free, unlimited).
    Tier 2: searchPosts across all public channels (limited daily budget).

    Args:
        query: Normalized search query string.
        num_results: Maximum number of results to return.
        http_client: Unused (required by provider contract).

    Returns:
        List of WebSearchResult objects (empty on failure).
    """
    if not query.strip() or num_results < 1:
        return []

    from .telegram_client import get_telethon_client, TelegramConfigError
    from telethon.errors import FloodWaitError

    try:
        client = await get_telethon_client()
    except TelegramConfigError:
        logger.debug("Telegram not configured, skipping")
        return []

    results: list[WebSearchResult] = []

    # Tier 1: searchGlobal — joined channels (free, unlimited)
    try:
        async for msg in client.iter_messages(None, search=query, limit=num_results):
            if not msg or not msg.text:
                continue
            results.append(
                WebSearchResult(
                    title=_chat_title(msg),
                    link=_message_link(msg),
                    snippet=msg.text[:200],
                    published_date=msg.date.isoformat() if msg.date else None,
                )
            )
    except FloodWaitError as e:
        logger.warning("Telegram searchGlobal flood wait: %ds", e.seconds)
    except Exception as exc:
        logger.warning("Telegram searchGlobal failed: %s: %s", type(exc).__name__, exc)

    # Tier 2: searchPosts — all public channels (limited budget)
    if len(results) < num_results:
        try:
            public_results = await _search_public_posts(client, query, num_results - len(results))
            results.extend(public_results)
        except FloodWaitError as e:
            logger.info("Telegram searchPosts flood wait: %ds, skipping", e.seconds)
        except Exception as exc:
            logger.debug("Telegram searchPosts failed: %s: %s", type(exc).__name__, exc)

    return results[:num_results]


async def _search_public_posts(client: Any, query: str, limit: int) -> list[WebSearchResult]:
    """Search ALL public channels via channels.searchPosts.

    Costs 1 daily free slot (~10/day for Premium). Checks budget first.
    Pagination within the same search is free after the first page.
    """
    from telethon import functions, types

    budget = settings.telegram_public_search_daily_budget

    # Check remaining budget
    flood = await client(functions.channels.CheckSearchPostsFloodRequest(query=query))

    if not flood.query_is_free and flood.remains <= 0:
        logger.info(
            "Telegram searchPosts: no free slots (%d/%d), skipping",
            flood.remains,
            flood.total_daily,
        )
        return []

    if not flood.query_is_free and flood.remains <= (flood.total_daily - budget):
        # Below our reserved budget threshold
        logger.info(
            "Telegram searchPosts: below budget reserve (%d/%d remaining), skipping",
            flood.remains,
            flood.total_daily,
        )
        return []

    result = await client(
        functions.channels.SearchPostsRequest(
            query=query,
            offset_rate=0,
            offset_peer=types.InputPeerEmpty(),
            offset_id=0,
            limit=limit,
        )
    )

    # Build chat lookup
    chat_map = {c.id: c for c in result.chats}

    results = []
    for msg in result.messages:
        if not msg.text:
            continue
        # Resolve chat from peer_id
        peer = msg.peer_id
        chat_id = getattr(peer, "channel_id", None) or getattr(peer, "chat_id", None)
        chat = chat_map.get(chat_id) if chat_id else None

        title = chat.title if chat and hasattr(chat, "title") else "Public Channel"
        username = chat.username if chat and hasattr(chat, "username") else None
        link = f"https://t.me/{username}/{msg.id}" if username else ""

        results.append(
            WebSearchResult(
                title=title,
                link=link,
                snippet=msg.text[:200],
                published_date=msg.date.isoformat() if msg.date else None,
            )
        )

    if hasattr(result, "search_flood") and result.search_flood:
        sf = result.search_flood
        logger.info(
            "Telegram searchPosts: %d/%d slots remaining",
            sf.remains,
            sf.total_daily,
        )

    return results
