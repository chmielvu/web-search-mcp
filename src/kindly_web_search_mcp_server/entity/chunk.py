"""Chunking with global offset preservation for long content.

Reuses boundary-finding logic from content/windowing.py so that chunks
prefer paragraph and sentence boundaries (consistent truncation behavior).
"""

from __future__ import annotations

from ..content.windowing import _find_boundary_index


def chunk_text(
    text: str, *, chunk_size: int = 1000, overlap: int = 150
) -> list[tuple[int, str]]:
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
    step = max(1, safe_chunk - safe_overlap)

    chunks: list[tuple[int, str]] = []
    pos = 0
    n = len(text)

    while pos < n:
        target_end = min(n, pos + safe_chunk)
        if target_end < n:
            cut, _ = _find_boundary_index(text, pos, target_end)
            if cut <= pos:
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

        # Step forward by fixed step (chunk - overlap), allowing next chunk
        # to start at a sentence/para friendly point but guaranteeing progress.
        pos += step
        # If we landed inside a previous tiny boundary cut, still guarantee advance
        if pos <= chunks[-1][0]:
            pos = chunks[-1][0] + max(1, len(chunks[-1][1]) - safe_overlap)

    return chunks
