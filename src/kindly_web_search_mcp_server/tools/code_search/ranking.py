"""Evidence-first code-hit ranking, regex verification, and compaction."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from urllib.parse import urldefrag, urlsplit, urlunsplit

from .models import CodeSearchHit
from .query import QueryPlan

_RRF_K = 60
_NOISY_PATH_PARTS = {
    "build",
    "dist",
    "generated",
    "minified",
    "node_modules",
    "third_party",
    "vendor",
}
_NOISY_SUFFIXES = {".lock", ".min.js", ".min.css", ".map"}


def canonical_url(url: str) -> str:
    parts = urlsplit(urldefrag(url)[0])
    return urlunsplit(
        (parts.scheme.casefold(), parts.netloc.casefold(), parts.path.rstrip("/"), "", "")
    )


def _identity(hit: CodeSearchHit) -> tuple[str, str]:
    if hit.repository and hit.path:
        return "file", f"{hit.repository.casefold()}:{hit.path.casefold()}"
    return "url", canonical_url(hit.url)


def _merge_hits(hits: Iterable[CodeSearchHit]) -> list[CodeSearchHit]:
    merged: dict[tuple[str, str], CodeSearchHit] = {}
    providers: dict[tuple[str, str], set[str]] = defaultdict(set)
    variants: dict[tuple[str, str], set[str]] = defaultdict(set)
    channel_ranks: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    for hit in hits:
        key = _identity(hit)
        existing = merged.get(key)
        existing_providers = hit.source_metadata.get("providers", [])
        if isinstance(existing_providers, list):
            providers[key].update(str(item) for item in existing_providers)
        providers[key].add(hit.provider)
        if hit.query_variant:
            variants[key].add(hit.query_variant)
        existing_variants = hit.source_metadata.get("query_variants", [])
        if isinstance(existing_variants, list):
            variants[key].update(str(item) for item in existing_variants)
        channel = f"{hit.provider}:{hit.query_variant or 'primary'}"
        channel_ranks[key][channel] = min(
            channel_ranks[key].get(channel, 10_000), hit.search_rank or 10_000
        )
        existing_ranks = hit.source_metadata.get("channel_ranks", {})
        if isinstance(existing_ranks, dict):
            for name, rank in existing_ranks.items():
                if isinstance(rank, int):
                    channel_ranks[key][str(name)] = min(
                        channel_ranks[key].get(str(name), 10_000), rank
                    )
        if existing is None:
            merged[key] = hit.model_copy(deep=True)
            continue
        existing.score_components.update(
            {f"{hit.provider}:{name}": value for name, value in hit.score_components.items()}
        )
        if existing.line_start is None:
            existing.line_start = hit.line_start
            existing.line_end = hit.line_end
        if not existing.commit_oid and hit.commit_oid:
            existing.commit_oid = hit.commit_oid
        if not existing.sha and hit.sha:
            existing.sha = hit.sha
        existing.symbols.extend(symbol for symbol in hit.symbols if symbol not in existing.symbols)
        if not existing.evidence_role and hit.evidence_role:
            existing.evidence_role = hit.evidence_role
        existing.reasons.extend(reason for reason in hit.reasons if reason not in existing.reasons)
        for name, value in hit.source_metadata.items():
            existing.source_metadata.setdefault(name, value)
    for key, hit in merged.items():
        hit.source_metadata["providers"] = sorted(providers[key])
        hit.source_metadata["query_variants"] = sorted(variants[key])
        hit.source_metadata["channel_ranks"] = channel_ranks[key]
    return list(merged.values())


def _freshness(value: object) -> float:
    if not isinstance(value, str) or not value:
        return 0.5
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        age_days = max(0.0, (datetime.now(UTC) - parsed).total_seconds() / 86400)
        return max(0.0, 1.0 - age_days / 365.0)
    except ValueError:
        return 0.5


def _noise(hit: CodeSearchHit) -> float:
    path = (hit.path or "").casefold()
    repository = (hit.repository or "").casefold()
    parts = {part for part in re.split(r"[/\\]", path) if part}
    score = 0.0
    if parts & _NOISY_PATH_PARTS:
        score += 1.0
    if any(path.endswith(suffix) for suffix in _NOISY_SUFFIXES):
        score += 1.0
    if re.search(r"(^|[-_/])(fuzz|payloads?|wordlists?)([-_/]|$)", repository):
        score += 1.0
    return min(1.0, score)


def rank_candidates(
    plan: QueryPlan, hits: Iterable[CodeSearchHit], *, max_results: int | None = None
) -> list[CodeSearchHit]:
    """Merge candidate hits across providers and prioritize for hydration using RRF."""
    merged = _merge_hits(hits)
    for position, hit in enumerate(merged, 1):
        providers = hit.source_metadata.get("providers", [hit.provider])
        if not isinstance(providers, list):
            providers = [hit.provider]
        variants = hit.source_metadata.get("query_variants", [])
        if not isinstance(variants, list):
            variants = []
        raw_channel_ranks = hit.source_metadata.get("channel_ranks", {})
        if not isinstance(raw_channel_ranks, dict):
            raw_channel_ranks = {}
        rank = hit.search_rank or position
        rrf = sum(
            1.0 / (_RRF_K + max(1, int(channel_rank)))
            for channel_rank in raw_channel_ranks.values()
            if isinstance(channel_rank, int)
        ) or 1.0 / (_RRF_K + max(1, rank))

        provider_agreement = min(1.0, len({str(item) for item in providers}) / 3.0)
        variant_agreement = min(
            1.0, len({str(item) for item in variants}) / max(1, len(plan.variants))
        )
        popularity = min(
            1.0, math.log1p(float(hit.source_metadata.get("stars") or 0)) / math.log1p(100_000)
        )
        freshness = _freshness(hit.published_date or hit.source_metadata.get("pushed_at"))
        noise = _noise(hit)

        score = (
            4.0 * rrf
            + 0.14 * variant_agreement
            + 0.10 * provider_agreement
            + 0.08 * popularity
            + 0.025 * freshness
            - 0.12 * noise
        )
        hit.score = score
        hit.score_components.update(
            {
                "rrf": rrf,
                "variant_agreement": variant_agreement,
                "provider_agreement": provider_agreement,
                "popularity": popularity,
                "freshness": freshness,
                "noise": noise,
            }
        )
        reasons: list[str] = list(dict.fromkeys(hit.reasons))
        if provider_agreement:
            reasons.append(f"provider agreement: {len({str(item) for item in providers})}")
        if variant_agreement:
            reasons.append("matched multiple deterministic variants")
        if noise:
            reasons.append("noisy/generated path penalty")
        hit.reasons = list(dict.fromkeys(reasons))

    merged.sort(key=lambda item: (-(item.score or 0.0), item.search_rank or 10_000, item.url))
    if max_results is not None:
        return merged[:max_results]
    return merged
