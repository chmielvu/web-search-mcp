"""Chunking with global offset preservation for long content.

Reuses boundary-finding logic from content/windowing.py so that chunks
prefer paragraph and sentence boundaries (consistent truncation behavior).
"""

from __future__ import annotations

from ..content.windowing import _find_boundary_index


def chunk_text(text: str, *, chunk_size: int = 1000, overlap: int = 150) -> list[tuple[int, str]]:
    """Split text into overlapping chunks.

    Returns list of (global_start_offset, chunk_text) tuples.
    Chunks respect paragraph/sentence boundaries when possible using the
    shared _find_boundary_index logic.

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
            cut, _ = _find_boundary_index(text, pos, target_end)
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
