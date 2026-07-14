"""Telegram content resolver for t.me/ URLs.

Parses Telegram message/channel/group URLs and fetches content
as LLM-ready markdown via Telethon MTProto.

Tier 1 specialized resolver in the content fetch pipeline.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)


class TelegramContentError(RuntimeError):
    """Invalid or unsupported Telegram URL."""

    pass


@dataclass(frozen=True)
class TelegramTarget:
    """Parsed Telegram URL target."""

    username: str | None
    channel_id: int | None
    msg_id: int | None
    comment_thread_id: int | None  # reply_to msg_id


_T_ME_PATH_RE = re.compile(r"^/(?:c/(\d+)(?:/(\d+))?|(\w+)(?:/(\d+))?)$")


def parse_telegram_url(url: str) -> TelegramTarget:
    """Parse a t.me or telegram.me URL.

    Supported formats:
      t.me/username/12345              → username, msg_id=12345
      t.me/c/1234567890/12345         → channel_id=1234567890, msg_id=12345
      t.me/username                    → username only (recent messages)
      t.me/username/12345?thread=67890 → username, msg_id=12345, comment_thread=67890

    Raises TelegramContentError if URL is not a recognized Telegram URL.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in ("t.me", "telegram.me"):
        raise TelegramContentError(f"Not a Telegram URL: {host}")

    path = (parsed.path or "").rstrip("/")
    m = _T_ME_PATH_RE.match(path)
    if not m:
        raise TelegramContentError(f"Unrecognized Telegram path: {path}")

    channel_id_str, c_msg_id, username, msg_id_str = m.group(1), m.group(2), m.group(3), m.group(4)

    channel_id = int(channel_id_str) if channel_id_str else None
    if channel_id:
        # /c/CHANNEL_ID[/MSG_ID] — c_msg_id is the message ID
        msg_id = int(c_msg_id) if c_msg_id else None
    elif msg_id_str:
        # /username/MSG_ID
        msg_id = int(msg_id_str)
    else:
        # /username
        msg_id = None

    # Parse ?thread= for comment threads
    qs = parse_qs(parsed.query)
    thread_str = qs.get("thread", [None])[0]
    comment_thread_id = int(thread_str) if thread_str else None

    return TelegramTarget(
        username=username if not channel_id else None,
        channel_id=channel_id,
        msg_id=msg_id,
        comment_thread_id=comment_thread_id,
    )


async def fetch_telegram_markdown(url: str) -> str:
    """Fetch Telegram content as LLM-ready markdown.

    Uses Telethon to resolve the entity and fetch messages.
    Returns markdown with sender info, timestamps, view counts, and reply threading.
    """
    from ...search.telegram_client import get_telethon_client
    from telethon import functions

    target = parse_telegram_url(url)
    client = await get_telethon_client()

    # Resolve entity
    if target.channel_id:
        entity = await client.get_entity(target.channel_id)
    else:
        entity = await client.get_entity(target.username)

    if target.msg_id:
        # Fetch specific message
        messages = await client.get_messages(entity, ids=[target.msg_id])
        if not messages or not messages[0]:
            raise TelegramContentError(f"Message {target.msg_id} not found")

        msg = messages[0]

        if target.comment_thread_id:
            # Fetch comment thread
            replies = [
                m
                async for m in client.iter_messages(
                    entity, reply_to=target.comment_thread_id, limit=100
                )
            ]
            return _render_comment_thread(entity, msg, replies)

        return _render_single_message(entity, msg)

    # No specific message — fetch recent messages
    messages = await client.get_messages(entity, limit=50)
    full = await client(functions.channels.GetFullChannelRequest(entity))
    return _render_channel_overview(entity, full, messages)


def _render_single_message(entity, msg) -> str:
    """Render one message as markdown."""
    lines = []
    title = getattr(entity, "title", None) or getattr(entity, "username", "Unknown")
    lines.append(f"# {title}")
    lines.append(f"**Message #{msg.id}** — {msg.date.isoformat() if msg.date else 'unknown date'}")
    if msg.sender_id:
        lines.append(f"**Sender ID:** {msg.sender_id}")
    if msg.views:
        lines.append(f"**Views:** {msg.views}")
    if msg.forwards:
        lines.append(f"**Forwards:** {msg.forwards}")
    lines.append("")
    lines.append(msg.text or "*(no text content)*")
    return "\n".join(lines)


def _render_comment_thread(entity, msg, replies) -> str:
    """Render a message and its reply thread."""
    lines = []
    title = getattr(entity, "title", None) or "Unknown"
    lines.append(f"# {title} — Comment Thread")
    lines.append("")
    lines.append("## Original Post")
    lines.append(_render_single_message(entity, msg))
    lines.append("")
    lines.append(f"## {len(replies)} Replies")
    for reply in replies:
        sender = f"User {reply.sender_id}" if reply.sender_id else "Unknown"
        date = reply.date.isoformat() if reply.date else ""
        lines.append(f"- **{sender}** ({date}): {reply.text or '*(no text)*'}")
    return "\n".join(lines)


def _render_channel_overview(entity, full, messages) -> str:
    """Render channel metadata and recent messages."""
    lines = []
    title = getattr(entity, "title", None) or "Unknown Channel"
    lines.append(f"# {title}")

    chat = full.full_chat
    if hasattr(chat, "participants_count") and chat.participants_count:
        lines.append(f"**Members:** {chat.participants_count}")
    if hasattr(chat, "about") and chat.about:
        lines.append(f"**Description:** {chat.about}")
    lines.append("")
    lines.append(f"## Recent Messages ({len(messages)})")
    lines.append("")

    for msg in messages:
        if not msg or not msg.text:
            continue
        date = msg.date.isoformat()[:10] if msg.date else ""
        sender = f"User {msg.sender_id}" if msg.sender_id else ""
        views = f" | {msg.views} views" if msg.views else ""
        lines.append(f"- **{sender}** ({date}{views}): {msg.text[:200]}")

    return "\n".join(lines)
