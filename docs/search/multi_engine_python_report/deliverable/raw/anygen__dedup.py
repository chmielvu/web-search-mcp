"""URL canonicalization + multi-provider result merging with RRF re-ranking."""
from __future__ import annotations

from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, unquote

from hsearch.models import SearchResult

# Tracking parameters to strip during canonicalization.
_STRIP_PREFIXES = ("utm_",)
_STRIP_EXACT = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "igshid",
    "yclid",
    "msclkid",
    "spm",
    "_hsenc",
    "_hsmi",
}

_TRAILING_INDEX = {"/index.html", "/index.htm", "/index.php", "/default.html"}


def canonicalize_url(url: str) -> str:
    """Normalize URL for dedup: lower host, strip tracking params, sort query,
    normalize encoding, strip fragments, trailing slash, and trailing index files."""
    if not url:
        return url
    try:
        p = urlparse(url.strip())
    except ValueError:
        return url
    scheme = (p.scheme or "https").lower()
    if scheme == "http":
        scheme = "https"
    netloc = p.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    # Normalize percent-encoding: decode unreserved chars
    path = unquote(p.path)
    # Strip trailing index files
    for idx in _TRAILING_INDEX:
        if path.endswith(idx):
            path = path[: -len(idx)] or "/"
            break
    path = path.rstrip("/") or "/"
    # Filter and sort query params for canonical key
    kept = sorted(
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=False)
        if not (k.lower().startswith(_STRIP_PREFIXES) or k.lower() in _STRIP_EXACT)
    )
    query = urlencode(kept, doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))


# RRF constant — standard value from the original RRF paper (Cormack et al.)
_RRF_K = 60


def dedup_merge(results: list[SearchResult]) -> list[SearchResult]:
    """Merge duplicates by canonical URL and re-rank using Reciprocal Rank Fusion.

    RRF score = Σ 1/(k + rank_i) across all providers that returned this result.
    This is scale-invariant and handles different provider score ranges gracefully.
    Multi-source hits naturally get higher scores because they contribute more terms.
    """
    # Phase 1: Group by provider to establish per-provider rankings
    by_provider: dict[str, list[SearchResult]] = {}
    for r in results:
        prov = r.provider or "unknown"
        by_provider.setdefault(prov, []).append(r)

    # Phase 2: Bucket by canonical URL, preserving first-seen order
    bucket: dict[str, SearchResult] = {}
    order: list[str] = []
    rrf_scores: dict[str, float] = {}

    # Compute per-provider ranks (1-based) using original provider ordering
    provider_ranks: dict[str, dict[str, int]] = {}  # canonical_url -> {provider -> rank}
    for prov, prov_results in by_provider.items():
        for rank, r in enumerate(prov_results, 1):
            key = canonicalize_url(r.url)
            if not key:
                continue
            provider_ranks.setdefault(key, {})[prov] = rank

    for r in results:
        key = canonicalize_url(r.url)
        if not key:
            continue
        if key not in bucket:
            r.sources = list(dict.fromkeys(r.sources or [r.provider]))
            bucket[key] = r
            order.append(key)
        else:
            existing = bucket[key]
            for src in r.sources or [r.provider]:
                if src and src not in existing.sources:
                    existing.sources.append(src)
            if len(r.snippet) > len(existing.snippet):
                existing.snippet = r.snippet
            if r.title and (not existing.title or len(r.title) > len(existing.title)):
                existing.title = r.title
            if r.published and not existing.published:
                existing.published = r.published
            if r.content and (not existing.content or len(r.content) > len(existing.content)):
                existing.content = r.content
            if r.summary and not existing.summary:
                existing.summary = r.summary
            if r.favicon and not existing.favicon:
                existing.favicon = r.favicon
            if r.author and not existing.author:
                existing.author = r.author
            if r.image and not existing.image:
                existing.image = r.image

    # Phase 3: Compute RRF scores
    for key in order:
        ranks = provider_ranks.get(key, {})
        rrf = sum(1.0 / (_RRF_K + rank) for rank in ranks.values())
        # Richness bonus (small, to break ties — not dominant like before)
        r = bucket[key]
        richness = sum([
            0.002 if r.content else 0,
            0.001 if r.summary else 0,
            0.0005 if r.published else 0,
        ])
        rrf_scores[key] = rrf + richness

    # Phase 4: Sort by RRF score descending, then by source count for ties
    merged = [bucket[k] for k in order]
    for r in merged:
        key = canonicalize_url(r.url)
        r.score = rrf_scores.get(key, 0.0)
    merged.sort(key=lambda x: (-(x.score or 0.0), -len(x.sources)))
    return merged
