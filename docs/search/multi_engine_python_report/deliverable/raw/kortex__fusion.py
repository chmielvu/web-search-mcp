"""Reciprocal Rank Fusion (RRF) across sources.

score(doc) = sum over sources s returning doc of  w_s / (K + rank_s(doc))

`w_s` is the per-source reliability weight (rolling success rate) when
`WEIGHTED_RRF` is enabled — flaky sources contribute less. RRF is order-only
and robust to sources returning wildly different result counts and score
distributions — the right primitive for fusing metasearch, neural search,
and social/vertical CLIs.
"""

from __future__ import annotations

from . import stats
from .config import RRF_K, WEIGHTED_RRF
from .models import Result


def rrf_fuse(ranked_lists: list[list[Result]], k: int = RRF_K,
             weighted: bool = WEIGHTED_RRF) -> list[Result]:
    """Fuse multiple ranked lists into one, returning results sorted by RRF.

    Each list is expected to be already-ranked (best first). Results are
    deduped by identity and the first-seen instance is kept. Reliability
    weights are fetched once per distinct source (not per result).
    """
    best: dict[str, Result] = {}
    scores: dict[str, float] = {}

    weights: dict[str, float] = {}
    if weighted:
        sources = {r.source for lst in ranked_lists for r in lst if r.source}
        weights = {name: stats.reliability(name) for name in sources}

    for results in ranked_lists:
        for rank, r in enumerate(results):
            key = r.identity()
            if not key:
                continue
            w = weights.get(r.source, 1.0) if weighted else 1.0
            scores[key] = scores.get(key, 0.0) + w / (k + rank + 1)
            if key not in best:
                best[key] = r

    ordered = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)
    out: list[Result] = []
    for key in ordered:
        r = best[key]
        r.score = round(scores[key], 6)
        r.meta["score_raw"] = round(scores[key], 6)  # pre-re-rank fusion score
        out.append(r)
    return out
