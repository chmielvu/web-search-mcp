from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContentWindow:
    offset: int
    length: int
    returned_chars: int
    total_chars: int
    has_more: bool
    next_offset: int | None


@dataclass(frozen=True)
class WindowedContent:
    content: str
    window: ContentWindow


def slice_content(content: str, *, offset: int, length: int) -> WindowedContent:
    safe_offset = max(0, offset)
    safe_length = max(1, length)

    total = len(content)
    if safe_offset >= total:
        window = ContentWindow(
            offset=safe_offset,
            length=safe_length,
            returned_chars=0,
            total_chars=total,
            has_more=False,
            next_offset=None,
        )
        return WindowedContent(content="", window=window)

    sliced = content[safe_offset : safe_offset + safe_length]
    returned = len(sliced)
    next_offset = safe_offset + returned
    has_more = next_offset < total

    window = ContentWindow(
        offset=safe_offset,
        length=safe_length,
        returned_chars=returned,
        total_chars=total,
        has_more=has_more,
        next_offset=next_offset if has_more else None,
    )
    return WindowedContent(content=sliced, window=window)

