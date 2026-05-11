"""Diversity-based deduplication using embedding similarity."""

from __future__ import annotations

import logging
from urllib.parse import urlparse
import math

try:
    import numpy as np
except ModuleNotFoundError:  # pragma: no cover - fallback for constrained environments
    np = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _normalize_host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        return host[4:]
    return host or "__unknown_host__"


def maximal_marginal_relevance_rank(
    query_embedding: list[float] | np.ndarray,
    candidate_embeddings: list[list[float] | np.ndarray],
    urls: list[str],
    *,
    lambda_param: float = 0.7,
    max_per_host: int = 2,
    host_saturation_penalty: float = 0.2,
) -> list[int]:
    """Return deterministic true-MMR ranking with host saturation control.

    MMR objective per candidate i:
      λ * sim(q, d_i) - (1-λ) * max_{j in S} sim(d_i, d_j) - host_penalty(i)
    """
    n = len(candidate_embeddings)
    if n == 0:
        return []
    if len(urls) != n:
        return list(range(n))

    if np is not None:
        matrix = np.array([list(e) if isinstance(e, np.ndarray) else e for e in candidate_embeddings], dtype=float)
        query = np.array(list(query_embedding) if isinstance(query_embedding, np.ndarray) else query_embedding, dtype=float)
        if matrix.ndim != 2 or matrix.shape[0] != n:
            return list(range(n))
        if query.ndim != 1 or query.shape[0] != matrix.shape[1]:
            return list(range(n))

        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1
        normalized = matrix / norms
        similarity_matrix = normalized @ normalized.T
        query_norm = np.linalg.norm(query)
        if query_norm == 0:
            return list(range(n))
        query_similarities = normalized @ (query / query_norm)
    else:
        vectors = [list(e) for e in candidate_embeddings]
        query = list(query_embedding)
        if not vectors or len(query) != len(vectors[0]):
            return list(range(n))

        def cosine(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(y * y for y in b))
            if na == 0 or nb == 0:
                return 0.0
            return dot / (na * nb)

        similarity_matrix = [[cosine(vectors[i], vectors[j]) for j in range(n)] for i in range(n)]
        query_similarities = [cosine(query, vectors[i]) for i in range(n)]

    hosts = [_normalize_host(url) for url in urls]
    host_counts: dict[str, int] = {}
    selected: list[int] = []
    remaining: list[int] = list(range(n))

    while remaining:
        best_idx = remaining[0]
        best_objective = float("-inf")
        for idx in remaining:
            host_count = host_counts.get(hosts[idx], 0)
            host_penalty = host_saturation_penalty * host_count
            if host_count >= max_per_host:
                host_penalty += 1.0  # hard push-away after cap, still allows backfill if needed.

            redundancy = max((float(similarity_matrix[idx][s] if np is None else similarity_matrix[idx, s]) for s in selected), default=0.0)
            relevance = float(query_similarities[idx])
            objective = lambda_param * relevance - (1.0 - lambda_param) * redundancy - host_penalty
            if objective > best_objective:
                best_objective = objective
                best_idx = idx

        selected.append(best_idx)
        host_counts[hosts[best_idx]] = host_counts.get(hosts[best_idx], 0) + 1
        remaining.remove(best_idx)

    return selected


def compute_embedding_diversity(
    embeddings: list[list[float] | object],
    threshold: float = 0.85,
) -> list[int]:
    """
    Remove near-duplicate embeddings based on cosine similarity threshold.

    Iteratively removes embeddings that are too similar to already-kept ones.

    Args:
        embeddings: List of embedding vectors (lists or numpy arrays)
        threshold: Cosine similarity threshold for deduplication
                  (0.85 = remove if >85% similar)

    Returns:
        List of indices to keep, maintaining original order
    """
    if len(embeddings) <= 1:
        return list(range(len(embeddings)))

    # Convert to numpy array if needed
    if np is None:
        return list(range(len(embeddings)))

    if isinstance(embeddings[0], list):
        matrix = np.array(embeddings)
    else:
        matrix = np.array([list(e) if isinstance(e, np.ndarray) else e for e in embeddings])

    # Compute L2 norms for normalization
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1

    # Normalize vectors
    normalized = matrix / norms

    # Compute cosine similarity matrix
    similarity_matrix = normalized @ normalized.T

    # Iteratively remove duplicates
    kept_indices: list[int] = []
    suppressed: set[int] = set()

    for i in range(len(embeddings)):
        if i in suppressed:
            continue

        # Keep this one
        kept_indices.append(i)

        # Suppress similar ones that come after
        for j in range(i + 1, len(embeddings)):
            if j not in suppressed and similarity_matrix[i, j] > threshold:
                suppressed.add(j)
                logger.debug(
                    f"Suppressed duplicate: index {j} (similarity: {similarity_matrix[i, j]:.3f})"
                )

    logger.info(
        f"Diversity pruning: kept {len(kept_indices)}/{len(embeddings)} "
        f"(removed {len(suppressed)} duplicates, threshold={threshold})"
    )

    return kept_indices
