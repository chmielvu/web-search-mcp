"""Exa neural search. Docs: https://exa.ai/docs/reference/search"""
from __future__ import annotations

import asyncio
from typing import Any

from hsearch.models import SearchResult
from hsearch.providers.base import SearchProvider

SEARCH_ENDPOINT = "https://api.exa.ai/search"
ANSWER_ENDPOINT = "https://api.exa.ai/answer"
FIND_SIMILAR_ENDPOINT = "https://api.exa.ai/findSimilar"
# Exa Agent API (June 2026) — async, high-compute deep-research / list-building
# / enrichment agent. Docs: https://exa.ai/docs/reference/agent-api-guide
AGENT_ENDPOINT = "https://api.exa.ai/agent/runs"

# Cost/reasoning effort tiers accepted by the Agent API (AgentEffort enum).
_VALID_EFFORTS = {"minimal", "low", "medium", "high", "xhigh", "auto"}
# Terminal run statuses (lifecycle: queued → running → completed|failed|cancelled).
_AGENT_TERMINAL = {"completed", "failed", "cancelled", "canceled", "error"}

# Exa search categories, live-verified against the OpenAPI enum 2026-08-14:
#   company | publication | news | personal site | financial report | people
# July 2026 changelog: `publication` REPLACES `research paper`; `pdf`, `github`
# and `tweet` are deprecated. Exa still accepts arbitrary strings as loose
# "category hints", so the old names do not hard-fail — they just stop getting
# first-class vertical treatment, which is a silent quality regression. We
# normalize the retired names onto their supported successors so existing
# scripts and muscle memory keep working at full quality.
_CATEGORY_ALIASES = {
    "research paper": "publication",
    "research papers": "publication",
    "paper": "publication",
    "papers": "publication",
    "publication": "publication",
    "linkedin profile": "people",
}
# Deprecated with no first-class successor — passed through untouched as hints.
_DEPRECATED_CATEGORIES = {"pdf", "github", "tweet"}


def _normalize_category(value: Any) -> Any:
    """Map retired Exa category names onto their current equivalents."""
    if not isinstance(value, str):
        return value
    return _CATEGORY_ALIASES.get(value.strip().lower(), value)


# Exa search types whose server-side work exceeds the global default timeout
# (HSEARCH_TIMEOUT=15s). Live-measured 2026-08-14: `deep-reasoning` takes ~12s
# standalone and reliably timed out inside `--mode recall`, where six providers
# compete for connections — recall was silently losing its most expensive
# provider on every run. Give the slow tiers their own floor.
_SLOW_EXA_TYPES = {"deep-reasoning", "deep"}
_SLOW_EXA_TIMEOUT = 45.0

# Cap for `contents.context`. Unbounded is a footgun — live probe returned
# 168K chars (275K with text=True). 12K is roughly 3K tokens: enough grounding
# for a real answer, small enough to paste into a prompt.
_DEFAULT_CONTEXT_CHARS = 12000


def _search_timeout(payload: dict[str, Any]) -> float | None:
    """Per-call timeout floor for slow Exa search tiers.

    Returns None for the fast tiers so the client-level default (and any
    HSEARCH_TIMEOUT override) still applies. When the user has deliberately
    raised HSEARCH_TIMEOUT above our floor, respect theirs.
    """
    if payload.get("type") not in _SLOW_EXA_TYPES:
        return None
    from hsearch.config import timeout_seconds

    return max(_SLOW_EXA_TIMEOUT, timeout_seconds())



class ExaProvider(SearchProvider):
    name = "exa"
    requires_env = ["EXA_API_KEY"]

    # Last response's top-level `context` string (from contents.context).
    # Mirrored into SearchResponse.context / meta.context by the engine, the
    # same pattern TavilyProvider._last_answer uses for `--answer`.
    _last_context: str | None = None

    async def _search(self, query: str, count: int = 10, **kwargs: Any) -> list[SearchResult]:
        payload: dict[str, Any] = {
            "query": query,
            "numResults": max(1, min(count, 100)),
            "type": kwargs.get("type", "auto"),
        }
        if kwargs.get("additional_queries"):
            payload["additionalQueries"] = kwargs["additional_queries"]
        if kwargs.get("system_prompt"):
            payload["systemPrompt"] = kwargs["system_prompt"]
        if kwargs.get("user_location"):
            payload["userLocation"] = kwargs["user_location"]
        if kwargs.get("moderation"):
            payload["moderation"] = True
        if kwargs.get("output_schema"):
            payload["outputSchema"] = kwargs["output_schema"]

        contents: dict[str, Any] = {}

        if kwargs.get("highlights", False):
            if kwargs.get("highlights_query") or kwargs.get("highlights_max_characters"):
                h: dict[str, Any] = {}
                if kwargs.get("highlights_query"):
                    h["query"] = kwargs["highlights_query"]
                if kwargs.get("highlights_max_characters") is not None:
                    try:
                        h["maxCharacters"] = int(kwargs["highlights_max_characters"])
                    except (TypeError, ValueError):
                        pass
                contents["highlights"] = h or True
            else:
                contents["highlights"] = True

        if kwargs.get("with_content") or kwargs.get("text"):
            text_opts: dict[str, Any] = {}
            max_chars = kwargs.get("text_max_characters", 1000)
            try:
                text_opts["maxCharacters"] = int(max_chars)
            except (TypeError, ValueError):
                pass
            if kwargs.get("text_verbosity"):
                text_opts["verbosity"] = kwargs["text_verbosity"]
            if kwargs.get("include_html_tags"):
                text_opts["includeHtmlTags"] = True
            if kwargs.get("include_sections"):
                text_opts["includeSections"] = kwargs["include_sections"]
            if kwargs.get("exclude_sections"):
                text_opts["excludeSections"] = kwargs["exclude_sections"]
            contents["text"] = text_opts or True

        if kwargs.get("summary_query"):
            contents["summary"] = {"query": kwargs["summary_query"]}
        elif kwargs.get("summary"):
            contents["summary"] = {} if kwargs["summary"] is True else kwargs["summary"]

        # `contents.context` (v1.0.0) — Exa assembles ONE pre-formatted
        # LLM-ready context string across all results, returned at the TOP
        # LEVEL of the response (not per-result).
        #
        # 🚨 Placement matters and cost us a release: the 2026-06-25 drift
        # review saw a *top-level* `context` request param marked deprecated
        # ("use highlights or text instead") and dropped the whole feature.
        # Live-probed 2026-08-14: top-level `context: true` is silently
        # IGNORED (no `context` key in the response), while
        # `contents.context` WORKS and returns the string. Two different
        # params with the same name — one retired, one current.
        #
        # ALWAYS bound it: unbounded returned 168,235 chars (275,969 with
        # text=True) in the live probe, which would blow any context window
        # and is a token-cost trap. Default to a sane cap.
        if kwargs.get("context"):
            max_chars = kwargs.get("context_max_characters")
            if max_chars is None:
                max_chars = _DEFAULT_CONTEXT_CHARS
            try:
                contents["context"] = {"maxCharacters": int(max_chars)}
            except (TypeError, ValueError):
                contents["context"] = {"maxCharacters": _DEFAULT_CONTEXT_CHARS}

        if kwargs.get("max_age_hours") is not None:
            try:
                contents["maxAgeHours"] = int(kwargs["max_age_hours"])
            except (TypeError, ValueError):
                pass
        elif kwargs.get("livecrawl"):
            legacy = str(kwargs["livecrawl"]).lower()
            if legacy in {"always", "preferred"}:
                contents["maxAgeHours"] = 0
            elif legacy == "never":
                contents["maxAgeHours"] = -1
        if kwargs.get("livecrawl_timeout") is not None:
            try:
                contents["livecrawlTimeout"] = int(kwargs["livecrawl_timeout"])
            except (TypeError, ValueError):
                pass
        if kwargs.get("subpages") is not None:
            try:
                contents["subpages"] = int(kwargs["subpages"])
            except (TypeError, ValueError):
                pass
        if kwargs.get("subpage_target"):
            contents["subpageTarget"] = kwargs["subpage_target"]

        extras: dict[str, Any] = {}
        if kwargs.get("extras"):
            extras = kwargs["extras"] if isinstance(kwargs["extras"], dict) else {}
        if kwargs.get("extras_links") is not None:
            try:
                extras["links"] = int(kwargs["extras_links"])
            except (TypeError, ValueError):
                pass
        if kwargs.get("extras_image_links") is not None:
            try:
                extras["imageLinks"] = int(kwargs["extras_image_links"])
            except (TypeError, ValueError):
                pass
        if extras:
            contents["extras"] = extras
        if contents:
            payload["contents"] = contents

        if kwargs.get("category"):
            payload["category"] = _normalize_category(kwargs["category"])
        if kwargs.get("include_domains"):
            payload["includeDomains"] = kwargs["include_domains"]
        if kwargs.get("exclude_domains"):
            payload["excludeDomains"] = kwargs["exclude_domains"]
        if kwargs.get("start_published_date"):
            payload["startPublishedDate"] = kwargs["start_published_date"]
        if kwargs.get("end_published_date"):
            payload["endPublishedDate"] = kwargs["end_published_date"]
        # startCrawlDate / endCrawlDate removed from Exa API on 2026-05-01.

        headers = {
            "x-api-key": self.api_key or "",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        resp = await self._request(
            "POST", SEARCH_ENDPOINT, headers=headers, json=payload,
            timeout=_search_timeout(payload),
        )
        data = resp.json()

        # Top-level pre-assembled LLM context (only present when
        # contents.context was requested).
        ctx = data.get("context")
        self._last_context = ctx if isinstance(ctx, str) and ctx else None

        out: list[SearchResult] = []
        for r in (data.get("results") or [])[:count]:
            text = r.get("text") or ""
            highlights = r.get("highlights") or []
            snippet = (highlights[0] if highlights else text)[:500]
            summary_val = r.get("summary")
            if isinstance(summary_val, dict):
                summary_val = summary_val.get("text") or summary_val.get("summary")
            out.append(
                SearchResult(
                    url=r.get("url", ""),
                    title=r.get("title") or r.get("url", ""),
                    snippet=snippet,
                    provider=self.name,
                    score=float(r.get("score") or 0.0),
                    published=r.get("publishedDate"),
                    content=text if isinstance(text, str) and text else None,
                    summary=summary_val if isinstance(summary_val, str) else None,
                    favicon=r.get("favicon") if isinstance(r.get("favicon"), str) else None,
                    author=r.get("author") if isinstance(r.get("author"), str) else None,
                    image=r.get("image") if isinstance(r.get("image"), str) else None,
                    raw=r,
                )
            )
        return out

    # ------------------------------------------------------------------
    # Agent API (async, high-compute deep-research / list-building agent).
    # Docs: https://exa.ai/docs/reference/agent-api-guide
    # ------------------------------------------------------------------

    def _agent_headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key or "",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def agent_create(self, query: str, **kwargs: Any) -> dict[str, Any]:
        """POST /agent/runs — create an async Agent run. Returns the run object.

        Body params (validated): query (required), effort (AgentEffort enum),
        outputSchema (JSON Schema → structured output), input (data rows /
        exclusions), previousRunId (continue from a completed run), dataSources
        (Connect partners, beta).
        """
        if not self.is_configured():
            from hsearch.providers.base import ProviderAuthError

            raise ProviderAuthError(f"{self.name}: missing env {','.join(self.requires_env)}")
        payload: dict[str, Any] = {"query": query}
        effort = kwargs.get("effort")
        if effort in _VALID_EFFORTS:
            payload["effort"] = effort
        if kwargs.get("output_schema"):
            payload["outputSchema"] = kwargs["output_schema"]
        if kwargs.get("input_data") is not None or kwargs.get("input_exclusion") is not None:
            inp: dict[str, Any] = {}
            if kwargs.get("input_data") is not None:
                inp["data"] = kwargs["input_data"]
            if kwargs.get("input_exclusion") is not None:
                inp["exclusion"] = kwargs["input_exclusion"]
            if inp:
                payload["input"] = inp
        if kwargs.get("previous_run_id"):
            payload["previousRunId"] = kwargs["previous_run_id"]
        if kwargs.get("data_sources"):
            payload["dataSources"] = kwargs["data_sources"]
        resp = await self._request(
            "POST", AGENT_ENDPOINT, headers=self._agent_headers(), json=payload
        )
        return resp.json()

    async def agent_get(self, run_id: str) -> dict[str, Any]:
        """GET /agent/runs/{id} — retrieve an Agent run's status/output."""
        if not self.is_configured():
            from hsearch.providers.base import ProviderAuthError

            raise ProviderAuthError(f"{self.name}: missing env {','.join(self.requires_env)}")
        resp = await self._request(
            "GET", f"{AGENT_ENDPOINT}/{run_id}", headers=self._agent_headers()
        )
        return resp.json()

    async def agent_run(
        self,
        query: str,
        *,
        poll_interval: float = 3.0,
        timeout: float = 600.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create an Agent run and poll until it reaches a terminal status.

        Returns the final GET /agent/runs/{id} payload (contains ``output`` with
        ``text``/``structured``/``grounding`` + ``costDollars``). Raises
        TimeoutError when the deadline passes.
        """
        created = await self.agent_create(query, **kwargs)
        run_id = created.get("id")
        status = (created.get("status") or "").lower()
        # If the create call already returned a terminal status, return as-is.
        if not run_id or status in _AGENT_TERMINAL:
            return created
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        last: dict[str, Any] = created
        while loop.time() < deadline:
            await asyncio.sleep(poll_interval)
            last = await self.agent_get(str(run_id))
            status = (last.get("status") or "").lower()
            if status in _AGENT_TERMINAL:
                return last
        raise TimeoutError(
            f"exa agent run {run_id} still '{last.get('status')}' after {timeout:.0f}s"
        )

    async def answer(self, query: str, **kwargs: Any) -> dict[str, Any]:
        """Call Exa /answer endpoint — returns LLM-generated answer with citations."""
        if not self.is_configured():
            from hsearch.providers.base import ProviderAuthError
            raise ProviderAuthError(f"{self.name}: missing env {','.join(self.requires_env)}")
        payload: dict[str, Any] = {"query": query}
        if kwargs.get("text"):
            payload["text"] = True
        if kwargs.get("output_schema"):
            payload["outputSchema"] = kwargs["output_schema"]
        headers = {
            "x-api-key": self.api_key or "",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        resp = await self._request("POST", ANSWER_ENDPOINT, headers=headers, json=payload)
        return resp.json()

    async def find_similar(self, url: str, count: int = 10, **kwargs: Any) -> list[SearchResult]:
        """Call Exa /findSimilar — returns pages semantically similar to the given URL."""
        if not self.is_configured():
            from hsearch.providers.base import ProviderAuthError
            raise ProviderAuthError(f"{self.name}: missing env {','.join(self.requires_env)}")
        payload: dict[str, Any] = {
            "url": url,
            "numResults": max(1, min(count, 100)),
        }
        contents: dict[str, Any] = {}
        if kwargs.get("with_content") or kwargs.get("text"):
            text_opts: dict[str, Any] = {}
            max_chars = kwargs.get("text_max_characters", 1000)
            try:
                text_opts["maxCharacters"] = int(max_chars)
            except (TypeError, ValueError):
                pass
            contents["text"] = text_opts or True
        if kwargs.get("highlights"):
            contents["highlights"] = True
        if kwargs.get("summary"):
            contents["summary"] = True
        if contents:
            payload["contents"] = contents
        if kwargs.get("include_domains"):
            payload["includeDomains"] = kwargs["include_domains"]
        if kwargs.get("exclude_domains"):
            payload["excludeDomains"] = kwargs["exclude_domains"]
        if kwargs.get("start_published_date"):
            payload["startPublishedDate"] = kwargs["start_published_date"]
        if kwargs.get("end_published_date"):
            payload["endPublishedDate"] = kwargs["end_published_date"]
        if kwargs.get("category"):
            payload["category"] = _normalize_category(kwargs["category"])
        headers = {
            "x-api-key": self.api_key or "",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        resp = await self._request("POST", FIND_SIMILAR_ENDPOINT, headers=headers, json=payload)
        data = resp.json()
        out: list[SearchResult] = []
        for r in (data.get("results") or [])[:count]:
            text = r.get("text") or ""
            highlights = r.get("highlights") or []
            snippet = (highlights[0] if highlights else text)[:500]
            summary_val = r.get("summary")
            if isinstance(summary_val, dict):
                summary_val = summary_val.get("text") or summary_val.get("summary")
            out.append(
                SearchResult(
                    url=r.get("url", ""),
                    title=r.get("title") or r.get("url", ""),
                    snippet=snippet,
                    provider=self.name,
                    score=float(r.get("score") or 0.0),
                    published=r.get("publishedDate"),
                    content=text if isinstance(text, str) and text else None,
                    summary=summary_val if isinstance(summary_val, str) else None,
                    favicon=r.get("favicon") if isinstance(r.get("favicon"), str) else None,
                    author=r.get("author") if isinstance(r.get("author"), str) else None,
                    image=r.get("image") if isinstance(r.get("image"), str) else None,
                    raw=r,
                )
            )
        return out
