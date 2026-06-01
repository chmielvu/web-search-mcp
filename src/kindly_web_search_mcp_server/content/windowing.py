from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ContentWindow:
    offset: int
    length: int
    returned_chars: int
    total_chars: int
    has_more: bool
    next_offset: int | None
    continuation_notice: str | None = None


@dataclass(frozen=True)
class WindowedContent:
    content: str
    window: ContentWindow


def _find_boundary_index(content: str, start: int, end: int) -> tuple[int, str | None]:
    segment = content[start:end]
    paragraph_matches = [match.start() for match in re.finditer(r"\n{2,}", segment)]
    if paragraph_matches:
        return start + paragraph_matches[-1], "paragraph"

    sentence_matches = [
        match.start() for match in re.finditer(r"(?<=[.!?])\s+", segment)
    ]
    if sentence_matches:
        return start + sentence_matches[-1], "sentence"

    return end, None


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
            continuation_notice=None,
        )
        return WindowedContent(content="", window=window)

    raw_end = min(total, safe_offset + safe_length)
    if raw_end >= total:
        cut_end = total
        cut_reason = None
    else:
        cut_end, cut_reason = _find_boundary_index(content, safe_offset, raw_end)
        if cut_end <= safe_offset:
            cut_end = raw_end
            cut_reason = None

    sliced = content[safe_offset:cut_end]
    returned = len(sliced)
    next_offset = safe_offset + returned
    has_more = next_offset < total
    notice = None
    if has_more:
        boundary_text = cut_reason or "hard"
        notice = (
            f"Truncated at {returned} of {total} characters on a {boundary_text} boundary. "
            f"Continue at offset {next_offset}."
        )

    window = ContentWindow(
        offset=safe_offset,
        length=safe_length,
        returned_chars=returned,
        total_chars=total,
        has_more=has_more,
        next_offset=next_offset if has_more else None,
        continuation_notice=notice,
    )
    return WindowedContent(content=sliced, window=window)
