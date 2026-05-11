"""Diversity-based deduplication using embedding similarity."""

from __future__ import annotations

import logging
import numpy as np

logger = logging.getLogger(__name__)


def compute_embedding_diversity(
    embeddings: list[list[float] | np.ndarray],
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