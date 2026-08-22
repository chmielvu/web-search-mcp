"""Cause-aware guidance strings for web_search middleware (no FastMCP imports)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def format_shaping_guidance(shaping: Sequence[Mapping[str, Any]]) -> str | None:
    """Echo applied query shaping for agent learning."""
    if not shaping:
        return None
    parts: list[str] = []
    for item in shaping:
        provider = str(item.get("provider") or "?")
        shaped = str(item.get("shaped") or "")
        rules = item.get("rules") or []
        if isinstance(rules, (list, tuple)):
            rule_s = ",".join(str(r) for r in rules if r)
        else:
            rule_s = str(rules)
        if shaped and rule_s:
            parts.append(f"{provider}→{shaped!r} ({rule_s})")
        elif shaped:
            parts.append(f"{provider}→{shaped!r}")
        else:
            parts.append(provider)
    if not parts:
        return None
    return "Query shaped for providers: " + "; ".join(parts) + "."


def web_search_empty_guidance(
    *,
    intent: str | None,
    providers_used: Sequence[str],
    query: str,
    shaping: Sequence[Mapping[str, Any]],
) -> tuple[str, list[str]]:
    """Guidance when web_search returns zero results."""
    del providers_used  # reserved for future provider-specific empty reasons
    del query
    intent_s = (intent or "").strip()
    tools: list[str] = []
    coding = intent_s == "ai_coding_and_infrastructure"
    social = intent_s == "social_media"

    if coding:
        msg = (
            "Zero results. Specialized code providers may need simpler symbol/repo terms. "
            "Retry web_search with rewrite=true and a short identifier; "
            "or gemini_search for a grounded overview."
        )
        tools.append("gemini_search")
    elif social:
        msg = (
            "Zero results. Social/discussion queries often work better with fewer operators "
            "and plain keywords. Retry web_search with rewrite=true, or try gemini_search."
        )
        tools.append("gemini_search")
    else:
        msg = "Zero results. Broaden: remove specific terms, set rewrite=true."

    shape_msg = format_shaping_guidance(shaping)
    if shape_msg:
        msg = f"{msg} {shape_msg}"
    return msg, tools


def web_search_specialized_gap_guidance(
    *,
    intent: str | None,
    providers_used: Sequence[str],
    results: Sequence[Mapping[str, Any]],
) -> tuple[str, list[str]]:
    """Hints when results exist but specialized code hosts are missing."""
    intent_s = (intent or "").strip()
    providers = {str(p).casefold() for p in providers_used}
    specialized_code = {"github", "sourcegraph", "gitlab"}
    has_specialized = bool(providers & specialized_code)

    urls = [str(r.get("link") or "") for r in results]
    titles = " ".join(str(r.get("title") or "") for r in results).casefold()
    queryish = titles

    looks_coding = intent_s == "ai_coding_and_infrastructure" or any(
        tok in queryish for tok in ("github.com", "repo:", ".py", "sourcegraph", "pull request")
    )
    # Also check result URLs for coding domains already present
    url_blob = " ".join(urls).casefold()
    if "github.com" in url_blob or "gitlab.com" in url_blob:
        has_specialized = True

    tools: list[str] = []
    if looks_coding and not has_specialized:
        msg = (
            "No specialized code hosts in top results. "
            "Call fetch on the best URLs, or narrow query with an explicit owner/repo."
        )
        tools.append("fetch")
        return msg, tools
    return "", tools
