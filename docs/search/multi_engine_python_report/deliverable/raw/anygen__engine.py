"""Programmatic search API — the core engine behind both CLI and library use.

Usage::

    from hsearch import search, search_sync, SearchResult

    # async
    resp = await search("Python async tutorial", mode="general", top=5)

    # sync
    resp = search_sync("Python async tutorial", mode="general", top=5)

    resp.results   # list[SearchResult]
    resp.answer    # str | None  (Tavily synthesized answer)
    resp.errors    # dict[str, str]
    resp.meta      # dict with query, mode, providers_queried, etc.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from hsearch.cache_policy import resolve_cache_ttl
from hsearch.cache import ResultCache
from hsearch.config import configured_providers
from hsearch.dedup import dedup_merge
from hsearch.extract import EXTRACT_PROVIDERS, extract_many
from hsearch.filters import Filters, apply as apply_filters
from hsearch.models import SearchResult
from hsearch.providers import ProviderAuthError, ProviderHTTPError, get_provider
from hsearch.router import providers_for_mode, fallback_providers


@dataclass
class SearchResponse:
    """Structured response from a search call."""

    results: list[SearchResult] = field(default_factory=list)
    answer: str | None = None
    context: str | None = None
    errors: dict[str, str] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.meta:
            out["meta"] = self.meta
        if self.answer:
            out["meta"] = {**out.get("meta", {}), "answer": self.answer}
        if self.context:
            out["meta"] = {**out.get("meta", {}), "context": self.context}
        out["results"] = [r.to_dict() for r in self.results]
        if self.errors:
            out["errors"] = self.errors
        return out


@dataclass
class ExtractResult:
    """Result from a URL content extraction."""

    url: str
    content: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _run_one(
    provider_name: str,
    query: str,
    count: int,
    use_cache: bool,
    cache: ResultCache | None,
    extra: dict,
    filters: Filters | None = None,
    cache_ttl_override: int | None = None,
) -> tuple[str, list[SearchResult] | None, str | None, dict]:
    if filters is not None:
        eff_query, eff_extra = apply_filters(provider_name, query, filters, extra)
    else:
        eff_query, eff_extra = query, dict(extra)
    extras_out: dict = {}
    cache_params = {
        k: v
        for k, v in {"count": count, **eff_extra}.items()
        if not str(k).startswith("_")
    }
    if use_cache and cache is not None:
        hit = cache.get(provider_name, eff_query, cache_params)
        if hit is not None:
            # Two on-disk shapes are supported:
            #   * legacy: a bare list of result dicts (pre-2026-08-14 entries)
            #   * current: {"results": [...], "extras": {...}}
            # The envelope exists because provider extras (Tavily's synthesized
            # `answer`, Exa's `context`, usage) were NOT cached, so any cache
            # HIT silently returned a 0-char answer while the cold call worked.
            # `--mode answer` has a 900s TTL, so most real invocations hit
            # cache and lost the answer -- HTTP 200, errors=None, no answer.
            if isinstance(hit, dict):
                cached_results = hit.get("results") or []
                cached_extras = hit.get("extras") or {}
            else:
                cached_results, cached_extras = hit, {}
            results = [SearchResult(**r) for r in cached_results]
            for key in ("answer", "context", "usage"):
                val = cached_extras.get(key)
                if val:
                    extras_out[key] = val
            extras_out["cached"] = True
            return provider_name, results, None, extras_out
    try:
        provider = get_provider(provider_name)
    except KeyError as e:
        return provider_name, None, str(e), extras_out
    try:
        async with provider:
            results = await provider.search(eff_query, count=count, **eff_extra)
            ans = getattr(provider, "_last_answer", None)
            if ans:
                extras_out["answer"] = ans
            ctx = getattr(provider, "_last_context", None)
            if isinstance(ctx, str) and ctx:
                extras_out["context"] = ctx
            usage = getattr(provider, "_last_usage", None)
            if isinstance(usage, dict) and usage:
                extras_out["usage"] = usage
            # Capture additional SERP features for richer engine-level data
            kg = getattr(provider, "_last_knowledge_graph", None)
            if isinstance(kg, dict):
                extras_out["knowledge_graph"] = kg
            paa = getattr(provider, "_last_people_also_ask", None)
            if isinstance(paa, list):
                extras_out["people_also_ask"] = paa
            related = getattr(provider, "_last_related_searches", None)
            if isinstance(related, list):
                extras_out["related_searches"] = related
    except ProviderAuthError as e:
        return provider_name, None, f"auth: {e}", extras_out
    except ProviderHTTPError as e:
        return provider_name, None, f"http: {e}", extras_out
    except Exception as e:  # noqa: BLE001
        return provider_name, None, f"{type(e).__name__}: {e}", extras_out

    if use_cache and cache is not None:
        cache.set(
            provider_name,
            eff_query,
            cache_params,
            {
                "results": [r.to_dict() | {"raw": {}} for r in results],
                # Persist the provider extras alongside the results so a cache
                # HIT reproduces the cold-call response. Without this the
                # synthesized answer/context vanished on every cached call.
                "extras": {
                    k: v for k, v in extras_out.items() if k in ("answer", "context", "usage")
                },
            },
            ttl=cache_ttl_override,
        )
    extras_out["cached"] = False
    return provider_name, results, None, extras_out


async def _run_many(
    providers: list[str],
    query: str,
    count: int,
    use_cache: bool,
    extra: dict,
    filters: Filters | None = None,
    cache_ttl_override: int | None = None,
    fanout_timeout: float | None = None,
    enable_fallback: bool = True,
) -> tuple[list[SearchResult], dict[str, str], dict[str, dict]]:
    cache: ResultCache | None = ResultCache() if use_cache else None
    try:
        coros = [
            _run_one(p, query, count, use_cache, cache, extra, filters, cache_ttl_override)
            for p in providers
        ]
        # Apply fanout timeout — return partial results if some providers are slow
        if fanout_timeout and fanout_timeout > 0:
            tasks = [asyncio.ensure_future(c) for c in coros]
            done, pending = await asyncio.wait(tasks, timeout=fanout_timeout)
            # Cancel timed-out tasks
            for t in pending:
                t.cancel()
            outcomes = []
            for t in tasks:
                if t in done:
                    outcomes.append(t.result())
                else:
                    # Find which provider this was
                    idx = tasks.index(t)
                    pname = providers[idx]
                    outcomes.append((pname, None, "timeout: provider did not respond in time", {}))
        else:
            outcomes = await asyncio.gather(*coros)

        # Provider fallback: retry failed providers with alternatives
        if enable_fallback:
            failed_providers = set()
            for name, results, err, _extras in outcomes:
                if err is not None or results is None:
                    failed_providers.add(name)
            already_queried = set(providers)
            fallback_coros = []
            fallback_names = []
            for failed in failed_providers:
                for fb in fallback_providers(failed):
                    if fb not in already_queried and fb not in fallback_names:
                        fallback_names.append(fb)
                        already_queried.add(fb)
                        fallback_coros.append(
                            _run_one(fb, query, count, use_cache, cache, extra, filters, cache_ttl_override)
                        )
            if fallback_coros:
                fallback_outcomes = await asyncio.gather(*fallback_coros)
                outcomes = list(outcomes) + list(fallback_outcomes)
    finally:
        if cache is not None:
            cache.close()

    merged: list[SearchResult] = []
    errors: dict[str, str] = {}
    extras_by_provider: dict[str, dict] = {}
    for name, results, err, extras in outcomes:
        if extras:
            extras_by_provider[name] = extras
        if err is not None or results is None:
            errors[name] = err or "no results"
            continue
        merged.extend(results)
    return merged, errors, extras_by_provider


def _resolve_providers(
    providers: list[str] | None = None,
    mode: str | None = None,
    all_providers: bool = False,
) -> list[str]:
    if all_providers:
        return configured_providers()
    if providers:
        return list(providers)
    return providers_for_mode(mode)


def _build_extra(mode: str | None = None, **kwargs: Any) -> dict[str, Any]:
    """Translate high-level options into provider kwargs."""
    extra: dict[str, Any] = {}
    mode_key = (mode or "").lower() or None

    if mode_key == "news":
        extra["topic"] = "news"
        extra["freshness"] = "pw"
    elif mode_key == "academic":
        # Exa July-2026: `publication` replaces the deprecated `research paper`
        # category and is backed by a 350M-publication index with structured
        # author/venue/citation metadata. See _normalize_category in exa.py.
        extra["category"] = "publication"
    elif mode_key == "realtime":
        extra["freshness"] = "pd"
    elif mode_key == "shopping":
        extra["search_type"] = "shopping"
    elif mode_key == "video":
        extra["search_type"] = "videos"
    elif mode_key == "images":
        extra["search_type"] = "images"
    elif mode_key == "places":
        extra["search_type"] = "places"
    elif mode_key == "answer":
        # Force-enable answer for --mode answer
        kwargs["answer"] = True
    elif mode_key == "deep":
        extra["type"] = "deep-reasoning"
        extra["summary"] = True
    elif mode_key == "fast":
        extra["type"] = "instant"
        extra["search_depth"] = "ultra-fast"
    elif mode_key == "company":
        # Exa Company Search (Jan 2026): fine-tuned entity retrieval.
        # type=auto + category=company routes to the company vertical.
        extra["type"] = "auto"
        extra["category"] = "company"
    elif mode_key == "finance":
        extra["topic"] = "finance"
        extra["search_depth"] = "advanced"
        extra["include_answer"] = "advanced"
    elif mode_key == "context":
        extra["search_kind"] = "context"
        extra["context_threshold_mode"] = "balanced"
    elif mode_key == "rag":
        # v1.0.0 — Exa contents.context: ONE pre-assembled, LLM-ready context
        # string across all results, in a single call. Distinct from
        # `--mode context` (Brave's LLM Context endpoint, which returns
        # per-result grounding snippets you still have to stitch yourself).
        extra["context"] = True
        extra["type"] = "auto"
    elif mode_key == "recall":
        extra["type"] = "deep-reasoning"
        extra["highlights"] = True
        extra["summary"] = True
        extra["search_depth"] = "advanced"
        extra["chunks_per_source"] = 3
        extra["auto_parameters"] = True
        extra["search_kind"] = "context"
        extra["context_threshold_mode"] = "lenient"
        extra["sources"] = ["web", "news"]
        extra["with_content"] = True
        extra["spellcheck"] = True
        extra["extra_snippets"] = True
        extra["moderation"] = True

    if kwargs.get("answer"):
        answer_depth = kwargs.get("answer_depth")
        if answer_depth in ("basic", "advanced"):
            extra["include_answer"] = answer_depth
        else:
            extra["include_answer"] = True
    if kwargs.get("summary"):
        extra["summary"] = True
    if kwargs.get("sources"):
        src = kwargs["sources"]
        extra["sources"] = [s.strip() for s in src.split(",")] if isinstance(src, str) else src
    if kwargs.get("location"):
        extra["location"] = kwargs["location"]
    if kwargs.get("goggles"):
        goggles = kwargs["goggles"]
        extra["goggles"] = [goggles] if isinstance(goggles, str) else list(goggles)
    if kwargs.get("serper_type"):
        extra["search_type"] = kwargs["serper_type"]
    if kwargs.get("page") is not None:
        extra["page"] = kwargs["page"]
    if kwargs.get("autocorrect") is not None:
        extra["autocorrect"] = kwargs["autocorrect"]
    if kwargs.get("livecrawl"):
        extra["livecrawl"] = kwargs["livecrawl"]
    if kwargs.get("auto"):
        extra["auto_parameters"] = True
    if kwargs.get("raw"):
        extra["include_raw_content"] = "markdown"
    if kwargs.get("days") is not None:
        extra["days"] = kwargs["days"]
    if kwargs.get("chunks_per_source") is not None:
        extra["chunks_per_source"] = kwargs["chunks_per_source"]
    if kwargs.get("additional_queries"):
        extra["additional_queries"] = list(kwargs["additional_queries"])
    if kwargs.get("max_age_hours") is not None:
        extra["max_age_hours"] = kwargs["max_age_hours"]
    if kwargs.get("highlights"):
        extra["highlights"] = True
    if kwargs.get("context_threshold"):
        extra["context_threshold_mode"] = kwargs["context_threshold"]
    if kwargs.get("retries") is not None:
        extra["_retries"] = kwargs["retries"]
    if kwargs.get("exact"):
        extra["exact_match"] = True
    if kwargs.get("depth"):
        extra["search_depth"] = kwargs["depth"]
    if kwargs.get("exa_type"):
        extra["type"] = kwargs["exa_type"]
    if kwargs.get("category"):
        # Exa category filter. Current enum (2026-08): company | publication |
        # news | people | personal site | financial report. Retired names are
        # normalized in providers/exa.py::_normalize_category.
        extra["category"] = kwargs["category"]
    if kwargs.get("context"):
        extra["context"] = True
    if kwargs.get("context_max_characters") is not None:
        extra["context_max_characters"] = kwargs["context_max_characters"]
    if kwargs.get("include_favicon"):
        extra["include_favicon"] = True
    if kwargs.get("include_usage"):
        extra["include_usage"] = True
    if kwargs.get("include_images"):
        extra["include_images"] = True
    if kwargs.get("include_image_descriptions"):
        extra["include_image_descriptions"] = True
    if kwargs.get("moderation"):
        extra["moderation"] = True
    if kwargs.get("livecrawl_timeout") is not None:
        extra["livecrawl_timeout"] = kwargs["livecrawl_timeout"]
    if kwargs.get("ignore_invalid_urls"):
        extra["ignore_invalid_urls"] = True
    if kwargs.get("firecrawl_scrape_timeout") is not None:
        extra["scrape_timeout"] = kwargs["firecrawl_scrape_timeout"]
    if kwargs.get("firecrawl_wait_for") is not None:
        extra["wait_for"] = kwargs["firecrawl_wait_for"]
    if kwargs.get("firecrawl_parsers"):
        extra["parsers"] = kwargs["firecrawl_parsers"]
    if kwargs.get("firecrawl_redact_pii") is not None:
        extra["redact_pii"] = kwargs["firecrawl_redact_pii"]
    if kwargs.get("firecrawl_store_in_cache") is not None:
        extra["store_in_cache"] = kwargs["firecrawl_store_in_cache"]
    if kwargs.get("firecrawl_lockdown") is not None:
        extra["lockdown"] = kwargs["firecrawl_lockdown"]
    if kwargs.get("firecrawl_zero_data_retention") is not None:
        extra["zero_data_retention"] = kwargs["firecrawl_zero_data_retention"]
    if kwargs.get("firecrawl_skip_tls_verification") is not None:
        extra["skip_tls_verification"] = kwargs["firecrawl_skip_tls_verification"]
    if kwargs.get("jina_engine"):
        extra["engine"] = kwargs["jina_engine"]
    if kwargs.get("jina_respond_with"):
        extra["respond_with"] = kwargs["jina_respond_with"]
    if kwargs.get("jina_target_selector"):
        extra["target_selector"] = kwargs["jina_target_selector"]
    if kwargs.get("jina_wait_for"):
        extra["wait_for_selector"] = kwargs["jina_wait_for"]
    if kwargs.get("jina_remove_selector"):
        extra["remove_selector"] = kwargs["jina_remove_selector"]
    if kwargs.get("jina_generated_alt"):
        extra["with_generated_alt"] = True
    if kwargs.get("safe_search"):
        extra["safe_search"] = True
    if kwargs.get("project_id"):
        extra["project_id"] = kwargs["project_id"]
    if kwargs.get("firecrawl_only_clean_content"):
        extra["only_clean_content"] = True
    if kwargs.get("firecrawl_max_age") is not None:
        extra["max_age"] = kwargs["firecrawl_max_age"]
    if kwargs.get("firecrawl_min_age") is not None:
        extra["min_age"] = kwargs["firecrawl_min_age"]
    if kwargs.get("firecrawl_block_ads") is not None:
        extra["block_ads"] = kwargs["firecrawl_block_ads"]
    if kwargs.get("firecrawl_proxy"):
        extra["proxy"] = kwargs["firecrawl_proxy"]
    if kwargs.get("firecrawl_question"):
        extra["question"] = kwargs["firecrawl_question"]
    if kwargs.get("highlights_query"):
        extra["highlights_query"] = kwargs["highlights_query"]
    if kwargs.get("exa_output_schema"):
        extra["output_schema"] = kwargs["exa_output_schema"]

    # --- Tavily answer/depth compatibility guard (2026-08-14 drift) ---------
    # Tavily returns ``answer: null`` for EVERY request with
    # ``search_depth="basic"`` (Tavily's default), regardless of the
    # ``include_answer`` value. Live-probed 2026-08-14 with a real key:
    #   basic      + include_answer=basic/advanced/True -> answer: null  (6/6)
    #   fast       + include_answer=advanced            -> 719 chars
    #   ultra-fast + include_answer=advanced            -> 557 chars
    #   advanced   + include_answer=advanced            -> 1006 chars
    # This silently broke `--mode answer` (asks for an answer but never set a
    # depth, so the provider default "basic" applied) while `--mode finance`
    # and `--mode recall` kept working because they pin depth="advanced".
    # Failure shape was a 0-char answer with HTTP 200 and errors=None.
    # Fix: whenever an answer is requested, ensure the depth is one that can
    # actually return one. Only "basic" is upgraded -- an explicit
    # fast/ultra-fast (e.g. --mode fast) is answer-capable and is preserved so
    # latency-first modes keep their timing characteristics.
    if extra.get("include_answer") and extra.get("search_depth", "basic") == "basic":
        extra["search_depth"] = "advanced"

    return extra


def _aggregate_answers(extras_by_provider: dict[str, dict]) -> str | None:
    """Aggregate answers from all providers, not just Tavily."""
    answers: list[str] = []
    for prov in ("tavily", "serper", "brave", "exa", "firecrawl", "jina"):
        ans = (extras_by_provider.get(prov) or {}).get("answer")
        if ans and isinstance(ans, str):
            answers.append(ans)
    if not answers:
        return None
    if len(answers) == 1:
        return answers[0]
    # Return the longest answer (typically the most detailed)
    return max(answers, key=len)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def search(
    query: str,
    *,
    providers: list[str] | None = None,
    mode: str | None = None,
    all_providers: bool = False,
    top: int = 10,
    no_cache: bool = False,
    cache_ttl: int | None = None,
    time: str | None = None,
    lang: str | None = None,
    region: str | None = None,
    sites: list[str] | None = None,
    exclude: list[str] | None = None,
    extract_top: int = 0,
    extract_provider: str = "jina",
    api_keys: dict[str, str] | None = None,
    fanout_timeout: float | None = None,
    **kwargs: Any,
) -> SearchResponse:
    """Run a search across one or more providers.

    Args:
        query: The search query string.
        providers: Explicit provider list (e.g. ["tavily", "brave"]).
        mode: Routing mode — default|news|academic|code|general|realtime|
              shopping|video|images|places|answer|deep|fast|recall.
        all_providers: Query every configured provider.
        top: Max results per provider.
        no_cache: Disable result cache.
        cache_ttl: Override cache TTL in seconds.
        time: Time filter — day|week|month|year or YYYY-MM-DD..YYYY-MM-DD.
        lang: ISO 639-1 language code.
        region: ISO 3166 country code.
        sites: Restrict to these domains.
        exclude: Exclude these domains.
        extract_top: Extract content from top N results.
        extract_provider: Provider for content extraction (jina|firecrawl).
        api_keys: Override API keys (e.g. {"tavily": "tvly-xxx"}).
        fanout_timeout: Max seconds to wait for all providers (partial results on timeout).
        **kwargs: Provider-specific options (answer, summary, raw, depth, etc.)

    Returns:
        SearchResponse with results, answer, errors, and meta.
    """
    import os

    if api_keys:
        from hsearch.config import PROVIDER_ENV

        for prov, key in api_keys.items():
            env_var = PROVIDER_ENV.get(prov)
            if env_var:
                os.environ[env_var] = key

    resolved_providers = _resolve_providers(providers, mode, all_providers)
    if not resolved_providers:
        return SearchResponse(
            errors={"_": "No providers configured. Set API keys via env vars or api_keys param."},
        )

    filters = Filters.from_cli(
        time=time, lang=lang, region=region, sites=sites, exclude=exclude,
    )
    extra = _build_extra(mode=mode, **kwargs)
    mode_key = (mode or "").lower() or None
    effective_cache_ttl = resolve_cache_ttl(mode_key, cache_ttl)

    results, errors, extras_by_provider = await _run_many(
        resolved_providers,
        query,
        top,
        use_cache=not no_cache,
        extra=extra,
        filters=filters,
        cache_ttl_override=effective_cache_ttl,
        fanout_timeout=fanout_timeout,
        enable_fallback=True,
    )

    merged = dedup_merge(results)
    # --top means total results wanted, not per-provider
    merged = merged[:max(top, 1)]

    if extract_top and extract_top > 0 and merged and extract_provider in EXTRACT_PROVIDERS:
        urls = [r.url for r in merged[:extract_top] if r.url]
        outcomes = await extract_many(urls, provider=extract_provider, concurrency=4)
        url_to_content = {u: c for (u, c, _e) in outcomes if c}
        for r in merged:
            if r.url in url_to_content:
                r.content = url_to_content[r.url]

    # Aggregate answers from all providers
    aggregated_answer = _aggregate_answers(extras_by_provider)

    # Pre-assembled LLM context (Exa contents.context). Take the longest —
    # same "most detailed wins" rule as _aggregate_answers.
    aggregated_context: str | None = None
    for prov_extras in extras_by_provider.values():
        c = prov_extras.get("context")
        if isinstance(c, str) and c and len(c) > len(aggregated_context or ""):
            aggregated_context = c

    cache_status = {
        p: extras_by_provider.get(p, {}).get("cached")
        for p in resolved_providers
        if "cached" in extras_by_provider.get(p, {})
    }

    # Collect related searches from all providers
    all_related: list[str] = []
    for prov_extras in extras_by_provider.values():
        rs = prov_extras.get("related_searches")
        if isinstance(rs, list):
            for q in rs:
                if q and q not in all_related:
                    all_related.append(q)

    meta: dict[str, Any] = {
        "query": query,
        "mode": mode_key or "default",
        "providers_queried": resolved_providers,
        "total_results": len(merged),
        "cached": cache_status,
        "cache_ttl_seconds": effective_cache_ttl,
    }
    # Surface aggregated answer in meta so CLI --format json consumers can read
    # it via `jq .meta.answer`. SearchResponse.answer remains the primary SDK
    # accessor — this is purely additive for CLI/JSON parity.
    if aggregated_answer:
        meta["answer"] = aggregated_answer
    if aggregated_context:
        meta["context"] = aggregated_context
    if all_related:
        meta["related_searches"] = all_related[:10]

    usage_by_provider = {
        p: extras_by_provider[p]["usage"]
        for p in resolved_providers
        if isinstance(extras_by_provider.get(p, {}).get("usage"), dict)
    }
    if usage_by_provider:
        meta["usage"] = usage_by_provider
    if extract_top and extract_top > 0:
        meta["extract_top"] = extract_top
        meta["extract_provider"] = extract_provider

    # Track which providers contributed via fallback
    all_providers_in_results = set()
    for name in extras_by_provider:
        if name not in resolved_providers and extras_by_provider[name].get("cached") is not None:
            all_providers_in_results.add(name)
    if all_providers_in_results:
        meta["fallback_providers"] = sorted(all_providers_in_results)

    return SearchResponse(
        results=merged,
        answer=aggregated_answer,
        context=aggregated_context,
        errors=errors,
        meta=meta,
    )


def search_sync(
    query: str,
    **kwargs: Any,
) -> SearchResponse:
    """Synchronous wrapper around :func:`search`."""
    return asyncio.run(search(query, **kwargs))


async def extract_urls(
    urls: list[str],
    provider: str = "jina",
    concurrency: int = 4,
    **options: Any,
) -> list[ExtractResult]:
    """Extract clean text/markdown content from URLs.

    Args:
        urls: URLs to extract content from.
        provider: Extraction provider (jina, firecrawl, or tavily).
        concurrency: Max parallel requests.
        **options: Provider-specific extras. Tavily honors ``query`` (rerank
            chunks by relevance), ``extract_depth`` (basic|advanced), and
            ``format`` (markdown|text).

    Returns:
        List of ExtractResult with url, content, and error fields.
    """
    outcomes = await extract_many(urls, provider=provider, concurrency=concurrency, **options)
    return [ExtractResult(url=u, content=c, error=e) for u, c, e in outcomes]


def extract_urls_sync(
    urls: list[str],
    provider: str = "jina",
    concurrency: int = 4,
    **options: Any,
) -> list[ExtractResult]:
    """Synchronous wrapper around :func:`extract_urls`."""
    return asyncio.run(extract_urls(urls, provider=provider, concurrency=concurrency, **options))


# ---------------------------------------------------------------------------
# Answer (Exa /answer)
# ---------------------------------------------------------------------------


@dataclass
class AnswerResponse:
    """Response from Exa /answer endpoint."""

    answer: str | None = None
    citations: list[SearchResult] = field(default_factory=list)
    cost: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.answer is not None:
            out["answer"] = self.answer
        if self.citations:
            out["citations"] = [r.to_dict() for r in self.citations]
        if self.cost:
            out["cost"] = self.cost
        if self.error:
            out["error"] = self.error
        return out


async def answer(
    query: str,
    *,
    text: bool = False,
    output_schema: dict[str, Any] | None = None,
) -> AnswerResponse:
    """Get an LLM-generated answer with citations from Exa.

    Args:
        query: The question to answer.
        text: Include full text in citation results.
        output_schema: JSON Schema for structured output.

    Returns:
        AnswerResponse with answer, citations, and cost.
    """
    try:
        provider = get_provider("exa")
    except KeyError as e:
        return AnswerResponse(error=str(e))
    try:
        async with provider:
            from hsearch.providers.exa import ExaProvider

            if not isinstance(provider, ExaProvider):
                return AnswerResponse(error="exa provider not available")
            data = await provider.answer(query, text=text, output_schema=output_schema)
    except Exception as e:
        return AnswerResponse(error=f"{type(e).__name__}: {e}")

    answer_val = data.get("answer")
    if isinstance(answer_val, dict):
        import json as _json

        answer_val = _json.dumps(answer_val, ensure_ascii=False, indent=2)
    citations: list[SearchResult] = []
    for c in data.get("citations") or []:
        if not isinstance(c, dict):
            continue
        citations.append(
            SearchResult(
                url=c.get("url", ""),
                title=c.get("title") or c.get("url", ""),
                snippet=(c.get("text") or "")[:500],
                provider="exa",
                published=c.get("publishedDate"),
                content=c.get("text") if isinstance(c.get("text"), str) else None,
                author=c.get("author") if isinstance(c.get("author"), str) else None,
                favicon=c.get("favicon") if isinstance(c.get("favicon"), str) else None,
                image=c.get("image") if isinstance(c.get("image"), str) else None,
                raw=c,
            )
        )
    return AnswerResponse(
        answer=answer_val if isinstance(answer_val, str) else None,
        citations=citations,
        cost=data.get("costDollars") if isinstance(data.get("costDollars"), dict) else None,
    )


def answer_sync(query: str, **kwargs: Any) -> AnswerResponse:
    """Synchronous wrapper around :func:`answer`."""
    return asyncio.run(answer(query, **kwargs))


# ---------------------------------------------------------------------------
# Ground (Jina g.jina.ai)
# ---------------------------------------------------------------------------


@dataclass
class GroundingResponse:
    """Response from Jina Grounding API."""

    factuality: float | None = None
    result: bool | None = None
    reasoning: str | None = None
    references: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.factuality is not None:
            out["factuality"] = self.factuality
        if self.result is not None:
            out["result"] = self.result
        if self.reasoning:
            out["reasoning"] = self.reasoning
        if self.references:
            out["references"] = self.references
        if self.error:
            out["error"] = self.error
        return out


async def ground(
    statement: str,
    *,
    no_cache: bool = False,
) -> GroundingResponse:
    """Fact-check a statement using Jina's Grounding API.

    Args:
        statement: The claim to verify.
        no_cache: Bypass Jina cache.

    Returns:
        GroundingResponse with factuality score, result, reasoning, and references.
    """
    try:
        provider = get_provider("jina")
    except KeyError as e:
        return GroundingResponse(error=str(e))
    try:
        async with provider:
            from hsearch.providers.jina import JinaProvider

            if not isinstance(provider, JinaProvider):
                return GroundingResponse(error="jina provider not available")
            data = await provider.ground(statement, no_cache=no_cache)
    except Exception as e:
        return GroundingResponse(error=f"{type(e).__name__}: {e}")

    d = data.get("data") or data
    return GroundingResponse(
        factuality=d.get("factuality") if isinstance(d.get("factuality"), (int, float)) else None,
        result=d.get("result") if isinstance(d.get("result"), bool) else None,
        reasoning=d.get("reasoning") if isinstance(d.get("reasoning"), str) else None,
        references=d.get("references") if isinstance(d.get("references"), list) else [],
    )


def ground_sync(statement: str, **kwargs: Any) -> GroundingResponse:
    """Synchronous wrapper around :func:`ground`."""
    return asyncio.run(ground(statement, **kwargs))


# ---------------------------------------------------------------------------
# Research (Tavily /research — async deep-research agent)
# ---------------------------------------------------------------------------


@dataclass
class ResearchResponse:
    """Response from Tavily Research API."""

    content: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    status: str | None = None
    request_id: str | None = None
    response_time: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.content is not None:
            out["content"] = self.content
        if self.sources:
            out["sources"] = self.sources
        if self.status:
            out["status"] = self.status
        if self.request_id:
            out["request_id"] = self.request_id
        if self.response_time is not None:
            out["response_time"] = self.response_time
        if self.error:
            out["error"] = self.error
        return out


async def research(
    input_text: str,
    *,
    model: str = "mini",
    citation_format: str = "numbered",
    output_schema: dict[str, Any] | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    poll_interval: float = 5.0,
    timeout: float = 600.0,
) -> ResearchResponse:
    """Run a Tavily deep-research task and wait for the final report.

    Args:
        input_text: The research question / instruction.
        model: "mini" (fast, narrow questions) | "pro" (deep) | "auto".
        citation_format: numbered | mla | apa | chicago.
        output_schema: optional JSON Schema for structured research output.
        include_domains / exclude_domains: domain filters.
        poll_interval: seconds between status polls.
        timeout: overall deadline in seconds.

    Returns:
        ResearchResponse with the synthesized report content + sources.
    """
    try:
        provider = get_provider("tavily")
    except KeyError as e:
        return ResearchResponse(error=str(e))
    try:
        async with provider:
            from hsearch.providers.tavily import TavilyProvider

            if not isinstance(provider, TavilyProvider):
                return ResearchResponse(error="tavily provider not available")
            data = await provider.research(
                input_text,
                model=model,
                citation_format=citation_format,
                output_schema=output_schema,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
                poll_interval=poll_interval,
                timeout=timeout,
            )
    except TimeoutError as e:
        return ResearchResponse(error=str(e))
    except Exception as e:
        return ResearchResponse(error=f"{type(e).__name__}: {e}")

    status = data.get("status")
    if (status or "").lower() in ("failed", "error", "cancelled"):
        return ResearchResponse(
            status=status,
            request_id=data.get("request_id"),
            error=str(data.get("error") or data.get("detail") or f"research {status}"),
        )
    content = data.get("content")
    if isinstance(content, dict):
        import json as _json

        content = _json.dumps(content, ensure_ascii=False, indent=2)
    sources = data.get("sources")
    return ResearchResponse(
        content=content if isinstance(content, str) else None,
        sources=sources if isinstance(sources, list) else [],
        status=status,
        request_id=data.get("request_id"),
        response_time=data.get("response_time")
        if isinstance(data.get("response_time"), (int, float))
        else None,
    )


def research_sync(input_text: str, **kwargs: Any) -> ResearchResponse:
    """Synchronous wrapper around :func:`research`."""
    return asyncio.run(research(input_text, **kwargs))


async def research_streaming(input_text: str, **kwargs: Any):
    """Stream a Tavily deep-research report, yielding text deltas as they arrive.

    Async generator. Use when you want first-token latency instead of waiting
    for the whole report (a mini run takes 15-25s end-to-end).
    """
    try:
        provider = get_provider("tavily")
    except KeyError as e:
        raise RuntimeError(str(e)) from e
    async with provider:
        from hsearch.providers.tavily import TavilyProvider

        if not isinstance(provider, TavilyProvider):
            raise RuntimeError("tavily provider not available")
        async for piece in provider.research_stream(input_text, **kwargs):
            yield piece


async def account_usage() -> dict[str, Any]:
    """Fetch remaining quota / usage from every provider that exposes it.

    Live-verified 2026-08-14 — only two of the six publish a usage endpoint:
      - Tavily    GET /usage                     → plan + per-capability counts
      - Firecrawl GET /v2/team/credit-usage       → remainingCredits + period
    Brave / Serper / Exa / Jina have no public per-key usage API (Exa reports
    per-call `costDollars` in each search response instead, which is surfaced
    via `--include-usage`, not here).

    Never raises: unreachable providers land in the per-provider `error` field
    so a single dead endpoint can't break the whole report.
    """
    out: dict[str, Any] = {}

    async def _tavily() -> None:
        try:
            provider = get_provider("tavily")
        except KeyError as e:
            out["tavily"] = {"error": str(e)}
            return
        if not provider.is_configured():
            out["tavily"] = {"error": "not configured"}
            return
        try:
            async with provider:
                resp = await provider._request(  # noqa: SLF001 — internal by design
                    "GET", "https://api.tavily.com/usage",
                    headers={"Authorization": f"Bearer {provider.api_key}"},
                )
                data = resp.json()
            key_u = data.get("key") or {}
            acct = data.get("account") or {}
            out["tavily"] = {
                "plan": acct.get("current_plan"),
                "plan_usage": acct.get("plan_usage"),
                "plan_limit": acct.get("plan_limit"),
                "key_usage": key_u.get("usage"),
                "by_capability": {
                    k.replace("_usage", ""): v
                    for k, v in key_u.items()
                    if k.endswith("_usage")
                },
            }
        except Exception as e:  # noqa: BLE001
            out["tavily"] = {"error": f"{type(e).__name__}: {e}"}

    async def _firecrawl() -> None:
        try:
            provider = get_provider("firecrawl")
        except KeyError as e:
            out["firecrawl"] = {"error": str(e)}
            return
        if not provider.is_configured():
            out["firecrawl"] = {"error": "not configured"}
            return
        try:
            async with provider:
                resp = await provider._request(  # noqa: SLF001
                    "GET", "https://api.firecrawl.dev/v2/team/credit-usage",
                    headers={"Authorization": f"Bearer {provider.api_key}"},
                )
                data = (resp.json() or {}).get("data") or {}
            out["firecrawl"] = {
                "remaining_credits": data.get("remainingCredits"),
                "plan_credits": data.get("planCredits"),
                "period_start": data.get("billingPeriodStart"),
                "period_end": data.get("billingPeriodEnd"),
            }
        except Exception as e:  # noqa: BLE001
            out["firecrawl"] = {"error": f"{type(e).__name__}: {e}"}

    await asyncio.gather(_tavily(), _firecrawl())
    return out


def account_usage_sync() -> dict[str, Any]:
    """Synchronous wrapper around :func:`account_usage`."""
    return asyncio.run(account_usage())


# ---------------------------------------------------------------------------
# Site traversal (Tavily /map + /crawl) — v0.9.0
# ---------------------------------------------------------------------------


@dataclass
class TraversalResponse:
    """Response from Tavily /map or /crawl.

    ``pages`` is normalized across both endpoints: /map yields URL-only entries
    (``content`` is None), /crawl yields URL + extracted content.
    """

    base_url: str | None = None
    pages: list[dict[str, Any]] = field(default_factory=list)
    request_id: str | None = None
    response_time: float | None = None
    kind: str | None = None  # "map" | "crawl"
    error: str | None = None

    @property
    def urls(self) -> list[str]:
        return [p["url"] for p in self.pages if p.get("url")]

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "base_url": self.base_url,
            "count": len(self.pages),
            "pages": self.pages,
        }
        if self.request_id:
            out["request_id"] = self.request_id
        if self.response_time is not None:
            out["response_time"] = self.response_time
        if self.error:
            out["error"] = self.error
        return out


def _normalize_traversal(data: dict[str, Any], kind: str) -> TraversalResponse:
    """Normalize /map (list[str]) and /crawl (list[dict]) into one shape."""
    raw = data.get("results") or []
    pages: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            pages.append({"url": item, "content": None})
        elif isinstance(item, dict):
            url = item.get("url") or ""
            if not url:
                continue
            content = item.get("raw_content") or item.get("content")
            entry: dict[str, Any] = {
                "url": url,
                "content": content if isinstance(content, str) else None,
            }
            if item.get("favicon"):
                entry["favicon"] = item["favicon"]
            if item.get("images"):
                entry["images"] = item["images"]
            pages.append(entry)
    rt = data.get("response_time")
    return TraversalResponse(
        base_url=data.get("base_url"),
        pages=pages,
        request_id=data.get("request_id"),
        response_time=rt if isinstance(rt, (int, float)) else None,
        kind=kind,
    )


async def _traverse(url: str, kind: str, **kwargs: Any) -> TraversalResponse:
    try:
        provider = get_provider("tavily")
    except KeyError as e:
        return TraversalResponse(kind=kind, error=str(e))
    try:
        async with provider:
            from hsearch.providers.tavily import TavilyProvider

            if not isinstance(provider, TavilyProvider):
                return TraversalResponse(kind=kind, error="tavily provider not available")
            fn = provider.map_site if kind == "map" else provider.crawl_site
            data = await fn(url, **kwargs)
    except (ProviderAuthError, ProviderHTTPError) as e:
        return TraversalResponse(kind=kind, error=str(e))
    except Exception as e:
        return TraversalResponse(kind=kind, error=f"{type(e).__name__}: {e}")
    return _normalize_traversal(data, kind)


async def map_site(url: str, **kwargs: Any) -> TraversalResponse:
    """Discover a site's URL inventory via Tavily /map (fast, no extraction)."""
    return await _traverse(url, "map", **kwargs)


async def crawl_site(url: str, **kwargs: Any) -> TraversalResponse:
    """Traverse a site and extract each page via Tavily /crawl."""
    return await _traverse(url, "crawl", **kwargs)


def map_site_sync(url: str, **kwargs: Any) -> TraversalResponse:
    """Synchronous wrapper around :func:`map_site`."""
    return asyncio.run(map_site(url, **kwargs))


def crawl_site_sync(url: str, **kwargs: Any) -> TraversalResponse:
    """Synchronous wrapper around :func:`crawl_site`."""
    return asyncio.run(crawl_site(url, **kwargs))


# ---------------------------------------------------------------------------
# Agent (Exa /agent/runs — async deep-research / list-building / enrichment)
# ---------------------------------------------------------------------------


@dataclass
class AgentResponse:
    """Response from the Exa Agent API (async high-compute research agent)."""

    text: str | None = None
    structured: Any | None = None
    grounding: list[Any] = field(default_factory=list)
    status: str | None = None
    run_id: str | None = None
    cost: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.text is not None:
            out["text"] = self.text
        if self.structured is not None:
            out["structured"] = self.structured
        if self.grounding:
            out["grounding"] = self.grounding
        if self.status:
            out["status"] = self.status
        if self.run_id:
            out["run_id"] = self.run_id
        if self.cost:
            out["cost"] = self.cost
        if self.usage:
            out["usage"] = self.usage
        if self.error:
            out["error"] = self.error
        return out


async def agent(
    query: str,
    *,
    effort: str = "auto",
    output_schema: dict[str, Any] | None = None,
    input_data: list[Any] | None = None,
    input_exclusion: list[Any] | None = None,
    previous_run_id: str | None = None,
    data_sources: list[Any] | None = None,
    poll_interval: float = 3.0,
    timeout: float = 600.0,
) -> AgentResponse:
    """Run an Exa Agent task (async deep research / list building / enrichment).

    Args:
        query: Natural-language research / list-building instruction.
        effort: Cost/reasoning tier — minimal|low|medium|high|xhigh|auto.
        output_schema: optional JSON Schema → schema-validated structured output.
        input_data: rows to process/enrich (each a dict of fields).
        input_exclusion: records/entities to avoid.
        previous_run_id: continue from a completed run (e.g. "find 10 more").
        data_sources: Exa Connect partner data sources (beta).
        poll_interval: seconds between status polls.
        timeout: overall deadline in seconds.

    Returns:
        AgentResponse with the final text, structured output, grounding, and cost.
    """
    try:
        provider = get_provider("exa")
    except KeyError as e:
        return AgentResponse(error=str(e))
    try:
        async with provider:
            from hsearch.providers.exa import ExaProvider

            if not isinstance(provider, ExaProvider):
                return AgentResponse(error="exa provider not available")
            data = await provider.agent_run(
                query,
                effort=effort,
                output_schema=output_schema,
                input_data=input_data,
                input_exclusion=input_exclusion,
                previous_run_id=previous_run_id,
                data_sources=data_sources,
                poll_interval=poll_interval,
                timeout=timeout,
            )
    except TimeoutError as e:
        return AgentResponse(error=str(e))
    except Exception as e:
        return AgentResponse(error=f"{type(e).__name__}: {e}")

    status = data.get("status")
    if (status or "").lower() in ("failed", "error", "cancelled", "canceled"):
        return AgentResponse(
            status=status,
            run_id=data.get("id"),
            error=str(data.get("stopReason") or data.get("error") or f"agent run {status}"),
        )
    output = data.get("output") or {}
    text = output.get("text") if isinstance(output, dict) else None
    structured = output.get("structured") if isinstance(output, dict) else None
    grounding = output.get("grounding") if isinstance(output, dict) else None
    return AgentResponse(
        text=text if isinstance(text, str) and text else None,
        structured=structured,
        grounding=grounding if isinstance(grounding, list) else [],
        status=status,
        run_id=data.get("id"),
        cost=data.get("costDollars") if isinstance(data.get("costDollars"), dict) else None,
        usage=data.get("usage") if isinstance(data.get("usage"), dict) else None,
    )


def agent_sync(query: str, **kwargs: Any) -> AgentResponse:
    """Synchronous wrapper around :func:`agent`."""
    return asyncio.run(agent(query, **kwargs))


# ---------------------------------------------------------------------------
# Find Similar (Exa /findSimilar)
# ---------------------------------------------------------------------------


async def find_similar(
    url: str,
    *,
    top: int = 10,
    text: bool = False,
    highlights: bool = False,
    summary: bool = False,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    category: str | None = None,
) -> SearchResponse:
    """Find pages semantically similar to a given URL using Exa.

    Args:
        url: The reference URL.
        top: Max results.
        text: Include full text content.
        highlights: Include highlight excerpts.
        summary: Include LLM summaries.
        include_domains: Only include these domains.
        exclude_domains: Exclude these domains.
        category: Exa category filter.

    Returns:
        SearchResponse with similar pages.
    """
    try:
        provider = get_provider("exa")
    except KeyError as e:
        return SearchResponse(errors={"exa": str(e)})
    try:
        async with provider:
            from hsearch.providers.exa import ExaProvider

            if not isinstance(provider, ExaProvider):
                return SearchResponse(errors={"exa": "exa provider not available"})
            kwargs: dict[str, Any] = {}
            if text:
                kwargs["with_content"] = True
            if highlights:
                kwargs["highlights"] = True
            if summary:
                kwargs["summary"] = True
            if include_domains:
                kwargs["include_domains"] = include_domains
            if exclude_domains:
                kwargs["exclude_domains"] = exclude_domains
            if category:
                kwargs["category"] = category
            results = await provider.find_similar(url, count=top, **kwargs)
    except Exception as e:
        return SearchResponse(errors={"exa": f"{type(e).__name__}: {e}"})

    return SearchResponse(
        results=results[:top],
        meta={
            "url": url,
            "provider": "exa",
            "total_results": len(results),
        },
    )


def find_similar_sync(url: str, **kwargs: Any) -> SearchResponse:
    """Synchronous wrapper around :func:`find_similar`."""
    return asyncio.run(find_similar(url, **kwargs))
