"""Evidence-first code-hit ranking, regex verification, and compaction."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable
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
_DATA_SUFFIXES = {".csv", ".list", ".tsv"}
_CODE_SUFFIXES = {
    ".py",
    ".ts",
    ".js",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".cpp",
    ".cc",
    ".h",
    ".hpp",
    ".rb",
    ".php",
    ".cs",
    ".swift",
    ".kt",
    ".scala",
    ".m",
    ".sh",
}
_GENERIC_QUERY_TERMS = {
    "code",
    "discover",
    "example",
    "examples",
    "existing",
    "find",
    "implement",
    "implementation",
    "implementations",
    "implementing",
    "mass",
    "open",
    "project",
    "projects",
    "repo",
    "repos",
    "repository",
    "repositories",
    "search",
    "source",
}
_RESULT_KIND_SIGNALS: dict[str, float] = {
    "code_match": 0.05,
    "semantic_page": -0.03,
    "documentation": -0.08,
    "repository": -0.10,
}


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
        if not existing.fragments and hit.fragments:
            existing.fragments = list(hit.fragments)
        else:
            existing_text = {fragment.text for fragment in existing.fragments}
            existing.fragments.extend(
                fragment for fragment in hit.fragments if fragment.text not in existing_text
            )
        if not existing.hydrated_source and hit.hydrated_source:
            existing.hydrated_source = hit.hydrated_source
            existing.hydrated_source_truncated = hit.hydrated_source_truncated
        existing.score_components.update(
            {f"{hit.provider}:{name}": value for name, value in hit.score_components.items()}
        )
        if existing.line_start is None:
            existing.line_start = hit.line_start
            existing.line_end = hit.line_end
        if not existing.snippet and hit.snippet:
            existing.snippet = hit.snippet
        if not existing.commit_oid and hit.commit_oid:
            existing.commit_oid = hit.commit_oid
        if not existing.sha and hit.sha:
            existing.sha = hit.sha
        existing.match_spans.extend(
            span for span in hit.match_spans if span not in existing.match_spans
        )
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
            parsed = parsed.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86400)
        return max(0.0, 1.0 - age_days / 365.0)
    except ValueError:
        return 0.5


def _noise(hit: CodeSearchHit) -> float:
    path = (hit.path or "").casefold()
    repository = (hit.repository or "").casefold()
    parts = set(part for part in re.split(r"[/\\]", path) if part)
    score = 0.0
    if parts & _NOISY_PATH_PARTS:
        score += 1.0
    if any(path.endswith(suffix) for suffix in _NOISY_SUFFIXES):
        score += 1.0
    if re.search(r"(^|[-_/])(fuzz|payloads?|wordlists?)([-_/]|$)", repository):
        score += 1.0
    return min(1.0, score)


def _text(hit: CodeSearchHit) -> str:
    return "\n".join(
        item
        for item in (
            hit.path or "",
            hit.title or "",
            hit.snippet or "",
            *(fragment.text for fragment in hit.fragments),
            hit.hydrated_source or "",
        )
        if item
    ).casefold()


_AST_ROLE_SIGNALS: dict[str, float] = {
    "definition": 0.16,
    "callsite": 0.05,
    "import": -0.18,
    "structure": 0.0,
}


def _ast_evidence_role(hit: CodeSearchHit) -> tuple[str, float] | None:
    payload = hit.source_metadata.get("ast_classification")
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return None
    evidence = payload.get("evidence")
    if not isinstance(evidence, list):
        return ("code", 0.0)
    roles = [
        str(item.get("role"))
        for item in evidence
        if isinstance(item, dict) and str(item.get("role")) in _AST_ROLE_SIGNALS
    ]
    if not roles:
        return ("code", 0.0)
    role = max(
        dict.fromkeys(roles),
        key=lambda value: (_AST_ROLE_SIGNALS[value], -roles.index(value)),
    )
    return role, _AST_ROLE_SIGNALS[role]


def _evidence_role(hit: CodeSearchHit, text: str) -> tuple[str, float]:
    path = (hit.path or "").casefold()
    url = (hit.url or "").casefold()
    if not path and any(marker in url for marker in ("/pull/", "/issues/", "/discussions/")):
        return "discussion", -0.05
    if hit.result_kind == "semantic_page":
        return "semantic_context", 0.0
    if hit.result_kind == "documentation":
        return "documentation", -0.20
    if hit.result_kind == "repository":
        return "repository", 0.0
    evidence = hit.hydrated_source or hit.snippet or ""
    query_terms = [
        item.casefold()
        for item in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", hit.query_variant or "")
        if len(item) >= 3
    ]
    for line in evidence.splitlines():
        lowered_line = line.casefold().strip()
        if (
            query_terms
            and any(term in lowered_line for term in query_terms)
            and re.match(
                r"^(import\b|from\b.+\bimport\b|use\b|#include\b|const\b.+require\()",
                lowered_line,
            )
        ):
            return "import", -0.18
    if re.search(
        r"\.gen\.[a-z0-9]+$|(^|/)(generated|vendor|pods)(/|$)|/internal/github\.com/", path
    ):
        return "generated", -0.18
    if re.search(
        r"(^|/)(changelog|news|readme|docs?|documentation|llms?|prompts?)(/|\.|$)|\.(md|mdx|rst|txt)$",
        path,
    ):
        return "documentation", -0.20
    if any(path.endswith(suffix) for suffix in _DATA_SUFFIXES):
        return "data", -0.22
    if re.search(r"(^|/)(tests?|specs?|fixtures?|benchmarks?)(/|$)|[_\.-](test|spec)\.", path):
        return "test", -0.10
    ast_role = _ast_evidence_role(hit)
    if ast_role is not None:
        return ast_role
    if re.search(
        r"\b(class|def|func|function|interface|struct|trait|enum)\s+[A-Za-z_]"
        r"|\bexport\s+(?:async\s+)?(?:function|class|const)\b",
        text,
    ):
        return "definition", 0.16
    if re.search(r"\b(await|return|new)\b|\w+\s*\([^\n]{0,120}\)", text):
        return "callsite", 0.05
    return "code", 0.0


def rank_hits(
    plan: QueryPlan, hits: Iterable[CodeSearchHit], *, max_results: int | None = None
) -> list[CodeSearchHit]:
    """Merge duplicates and apply the fixed evidence-first RRF formula."""

    merged = _merge_hits(hits)
    for position, hit in enumerate(merged, 1):
        providers = hit.source_metadata.get("providers", [hit.provider])
        if not isinstance(providers, list):
            providers = [hit.provider]
        variants = hit.source_metadata.get("query_variants", [])
        if not isinstance(variants, list):
            variants = []
        text = _text(hit)
        ranking_anchors = [
            anchor for anchor in plan.anchor_terms if anchor.casefold() not in _GENERIC_QUERY_TERMS
        ] or list(plan.anchor_terms)
        exact = sum(1 for anchor in ranking_anchors if anchor.casefold() in text)
        exact_signal = min(1.0, exact / max(1, len(ranking_anchors)))
        provider_agreement = min(1.0, len(set(str(item) for item in providers)) / 3.0)
        variant_agreement = min(
            1.0, len(set(str(item) for item in variants)) / max(1, len(plan.variants))
        )
        popularity = min(
            1.0, math.log1p(float(hit.source_metadata.get("stars") or 0)) / math.log1p(100_000)
        )
        freshness = _freshness(hit.published_date or hit.source_metadata.get("pushed_at"))
        noise = _noise(hit)
        raw_channel_ranks = hit.source_metadata.get("channel_ranks", {})
        if not isinstance(raw_channel_ranks, dict):
            raw_channel_ranks = {}
        rank = hit.search_rank or position
        rrf = sum(
            1.0 / (_RRF_K + max(1, int(channel_rank)))
            for channel_rank in raw_channel_ranks.values()
            if isinstance(channel_rank, int)
        ) or 1.0 / (_RRF_K + max(1, rank))
        symbol_signal = min(1.0, len(hit.symbols) / 2.0)
        span_signal = min(1.0, len(hit.match_spans) / 3.0)
        source_signal = 1.0 if hit.hydrated_source else 0.0
        scoped_signal = 1.0 if hit.source_metadata.get("repository_scoped") else 0.0
        result_kind_signal = _RESULT_KIND_SIGNALS.get(hit.result_kind, 0.0)
        evidence_role, role_signal = _evidence_role(hit, text)
        hit.evidence_role = hit.evidence_role or evidence_role
        score = (
            4.0 * rrf
            + 0.22 * exact_signal
            + 0.14 * variant_agreement
            + 0.10 * provider_agreement
            + 0.08 * symbol_signal
            + 0.05 * span_signal
            + 0.12 * source_signal
            + 0.10 * scoped_signal
            + (
                0.08
                if hit.path and any(hit.path.casefold().endswith(ext) for ext in _CODE_SUFFIXES)
                else 0.0
            )
            + result_kind_signal
            + role_signal
            + 0.08 * popularity
            + 0.025 * freshness
            - 0.12 * noise
        )
        hit.score = score
        hit.score_components.update(
            {
                "rrf": rrf,
                "exact_anchor": exact_signal,
                "variant_agreement": variant_agreement,
                "provider_agreement": provider_agreement,
                "symbol_evidence": symbol_signal,
                "match_span_evidence": span_signal,
                "source_evidence": source_signal,
                "repository_scoped": scoped_signal,
                "result_kind": result_kind_signal,
                "evidence_role": role_signal,
                "popularity": popularity,
                "freshness": freshness,
                "noise": noise,
            }
        )
        reasons: list[str] = list(dict.fromkeys(hit.reasons))
        if exact_signal:
            reasons.append("exact anchor in path or evidence")
        if provider_agreement:
            reasons.append(f"provider agreement: {len(set(str(item) for item in providers))}")
        if variant_agreement:
            reasons.append("matched multiple deterministic variants")
        if noise:
            reasons.append("noisy/generated path penalty")
        if symbol_signal:
            reasons.append("symbol evidence")
        if source_signal:
            reasons.append("hydrated source window")
        if scoped_signal:
            reasons.append("repository-scoped evidence")
        if evidence_role == "definition":
            reasons.append("implementation or declaration evidence")
        elif evidence_role in {
            "data",
            "documentation",
            "test",
            "import",
            "generated",
            "semantic_context",
            "repository",
        }:
            reasons.append(f"{evidence_role} evidence down-ranked")
        hit.reasons = list(dict.fromkeys(reasons))
    merged.sort(key=lambda item: (-(item.score or 0.0), item.search_rank or 10_000, item.url))
    if plan.mode == "code":
        return merged if max_results is None else merged[:max_results]
    diverse: list[CodeSearchHit] = []
    deferred: list[CodeSearchHit] = []
    seen_repositories: set[str] = set()
    for hit in merged:
        repository = (hit.repository or hit.url).casefold()
        if repository in seen_repositories:
            deferred.append(hit)
            continue
        seen_repositories.add(repository)
        diverse.append(hit)
        if max_results is not None and len(diverse) >= max_results:
            return diverse
    diverse.extend(deferred if max_results is None else deferred[: max_results - len(diverse)])
    return diverse


def verify_regex_hits(
    hits: Iterable[CodeSearchHit], pattern: re.Pattern[str] | None
) -> list[CodeSearchHit]:
    """Apply local regex verification to hydrated/source fragments only."""

    if pattern is None:
        return list(hits)
    verified: list[CodeSearchHit] = []
    for hit in hits:
        evidence = "\n".join(
            item
            for item in (
                hit.hydrated_source,
                hit.snippet,
                *(fragment.text for fragment in hit.fragments),
            )
            if item
        )
        if evidence and pattern.search(evidence):
            verified.append(hit)
    return verified


