"""Maximal Marginal Relevance (MMR) diversity.

After semantic re-rank, greedily select results balancing relevance vs
diversity — prevents the top-k from being dominated by one domain (the
observed Wikipedia/IBM-clone problem) or near-identical articles.
"""

from __future__ import annotations

from urllib.parse import urlparse

from .config import MMR_LAMBDA
from .models import Result


def _domain(url: str) -> str:
    try:
        # removeprefix, not lstrip: lstrip("www.") strips any leading run of the
        # characters {w, .} — e.g. "worldwide.com" → "orldwide.com".
        return (urlparse(url).netloc or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def mmr_select(candidates: list[Result], embeddings, limit: int,
               lam: float = MMR_LAMBDA) -> list[Result]:
    """Greedy MMR over `candidates` (already relevance-scored). Returns `limit`
    results. `embeddings` is an optional (n x d) ndarray aligned with candidates."""
    n = len(candidates)
    if n <= limit:
        return list(candidates)

    # normalize relevance scores to [0,1] for a stable λ
    scores = [max(0.0, r.score) for r in candidates]
    mx = max(scores) if scores else 0.0
    norm = [(s / mx) if mx > 0 else 0.0 for s in scores]

    # relevance floor: never select a candidate with <10% of the top score
    # (protects against degenerate query-expansion results polluting the set)
    floor = 0.1

    # similarity = domain-equality (0/1) blended with embedding cosine
    emb = embeddings if embeddings is not None and len(embeddings) == n else None
    cos = None
    if emb is not None:
        cos = emb @ emb.T  # normalized vectors → cosine matrix

    # Precompute domains once — urlparse/removeprefix in the inner loop below
    # would re-parse the same URL on every (candidate, selected) pair.
    domains = [_domain(r.url) for r in candidates]

    selected_idx: list[int] = []
    remaining = set(range(n))

    while remaining and len(selected_idx) < limit:
        best_i = -1
        best_score = -1e18
        for i in remaining:
            rel = norm[i]
            if rel < floor:
                continue  # below relevance floor — skip even if "diverse"
            div = 0.0
            for j in selected_idx:
                sim = 1.0 if domains[i] == domains[j] else 0.0
                if cos is not None:
                    sim = max(sim, float(cos[i][j]))
                div = max(div, sim)
            mmr = lam * rel - (1.0 - lam) * div
            if mmr > best_score:
                best_score = mmr
                best_i = i
        if best_i < 0:
            break
        selected_idx.append(best_i)
        remaining.remove(best_i)

    if not selected_idx:
        # everything fell below the relevance floor (or all scores were 0) —
        # never return [] while candidates exist; fall back to top-scored
        # (bug-sweep discovery 2026-08-26)
        return candidates[:limit]
    return [candidates[i] for i in selected_idx]
