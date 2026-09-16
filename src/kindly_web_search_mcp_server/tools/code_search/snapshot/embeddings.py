"""HF embedding helpers used by the snapshot semantic search fallback."""

from __future__ import annotations

from .models import LOGGER

__all__ = ["_cosine_similarity", "_hf_batch_code_embeddings", "_hf_code_embedding"]


async def _hf_code_embedding(text: str, *, max_chars: int = 2000) -> list[float] | None:
    """Embed via the shared ml/ client (fastembed-snowflake, Arctic, 384-dim).

    Returns None when the snippet is empty or the service is unreachable —
    semantic search then degrades to zero hits (same fail-open as before).
    """
    snippet = text[:max_chars]
    if not snippet.strip():
        return None
    try:
        from ....ml import embed_query

        return await embed_query(snippet, timeout=20.0)
    except Exception as exc:
        LOGGER.debug("ml embedding failed: %s", exc)
        return None


async def _hf_batch_code_embeddings(
    texts: list[str], *, max_chars: int = 2000
) -> list[list[float] | None]:
    """Batch embeddings through the shared ml/ client, 64 per request (service cap)."""
    truncated = [t[:max_chars] for t in texts]
    results: list[list[float] | None] = [None] * len(truncated)
    batch_cap = 64
    for start in range(0, len(truncated), batch_cap):
        batch = truncated[start : start + batch_cap]
        try:
            from ....ml import embed_texts

            vectors = await embed_texts(batch, timeout=30.0)
            for i, vec in enumerate(vectors):
                results[start + i] = vec
        except Exception as exc:
            LOGGER.debug("ml batch embedding failed: %s", exc)
    return results


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    try:
        import math

        dot = sum(x * y for x, y in zip(a, b, strict=False))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
    except Exception:
        return 0.0
