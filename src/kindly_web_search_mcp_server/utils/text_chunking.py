"""Text slicing and chunking with paragraph/sentence boundary preservation.

Content-window slicing for paginated fetch output and overlapping chunking
for long-text extraction share one boundary finder so both cut at the same
kinds of natural boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "ContentWindow",
    "WindowedContent",
    "chunk_text",
    "find_boundary_index",
    "slice_content",
]


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


def find_boundary_index(content: str, start: int, end: int) -> tuple[int, str | None]:
    """Return the last natural boundary in ``content[start:end]``.

    Returns ``(absolute_cut_index, boundary_kind)`` where kind is
    ``"paragraph"``, ``"sentence"``, or ``None`` (cut falls at ``end``).
    """
    segment = content[start:end]
    paragraph_matches = [match.start() for match in re.finditer(r"\n{2,}", segment)]
    if paragraph_matches:
        return start + paragraph_matches[-1], "paragraph"

    sentence_matches = [match.start() for match in re.finditer(r"(?<=[.!?])\s+", segment)]
    if sentence_matches:
        return start + sentence_matches[-1], "sentence"

    return end, None


def slice_content(content: str, *, offset: int, length: int) -> WindowedContent:
    """Slice ``content`` for paginated output, cutting at natural boundaries."""
    safe_offset = max(0, offset)
    # 0 or negative length means unlimited - return full content from offset (no truncation)
    if length <= 0:
        total = len(content)
        if safe_offset >= total:
            window = ContentWindow(
                offset=safe_offset,
                length=length,
                returned_chars=0,
                total_chars=total,
                has_more=False,
                next_offset=None,
            )
            return WindowedContent(content="", window=window)
        sliced = content[safe_offset:]
        window = ContentWindow(
            offset=safe_offset,
            length=length,
            returned_chars=len(sliced),
            total_chars=total,
            has_more=False,
            next_offset=None,
        )
        return WindowedContent(content=sliced, window=window)
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

    raw_end = min(total, safe_offset + safe_length)
    if raw_end >= total:
        cut_end = total
    else:
        cut_end, _ = find_boundary_index(content, safe_offset, raw_end)

    sliced = content[safe_offset:cut_end]
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


def chunk_text(text: str, *, chunk_size: int = 1000, overlap: int = 150) -> list[tuple[int, str]]:
    """Split text into overlapping chunks.

    Returns list of (global_start_offset, chunk_text) tuples.
    Chunks respect paragraph/sentence boundaries when possible using the
    shared find_boundary_index logic.

    The overlap ensures entities crossing chunk edges are captured in at least
    one full context window; dedup happens in postprocess_entities.
    """
    if not text:
        return []

    safe_chunk = max(50, int(chunk_size))
    safe_overlap = max(0, min(int(overlap), safe_chunk // 2))

    chunks: list[tuple[int, str]] = []
    pos = 0
    n = len(text)

    while pos < n:
        target_end = min(n, pos + safe_chunk)
        if target_end < n:
            cut, _ = find_boundary_index(text, pos, target_end)
            nominal_step = max(1, safe_chunk - safe_overlap)
            if cut <= pos or cut < pos + nominal_step:
                cut = target_end
            end = cut
        else:
            end = target_end

        chunk = text[pos:end]
        if not chunk:
            break
        chunks.append((pos, chunk))

        if end >= n:
            break

        # Continue from the actual end minus overlap. Early boundary cuts are
        # rejected above so this cannot skip source text.
        pos = max(chunks[-1][0] + 1, end - safe_overlap)
    return chunks
