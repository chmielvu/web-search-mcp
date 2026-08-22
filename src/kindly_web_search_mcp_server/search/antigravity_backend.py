"""Antigravity managed-agent backend for the ``gemini_search`` tool.

Second execution backend behind the Google Search grounding tier. Talks to
the Gemini Interactions API over plain REST (httpx) so no ``google-genai``
SDK upgrade is required, runs the ``antigravity-preview-05-2026`` managed
agent with ``google_search`` + ``url_context`` tools in a remote sandbox,
and maps the resulting interaction onto :class:`GeminiGroundingResult`.

Behavior:
- Always a SINGLE interaction (never the dual overview/deepdive mode).
- Background execution (``background=true``, requires stored interactions).
- Hard failures (no API key, HTTP error, timeout, failed/cancelled status)
  raise so the caller can fall back to the grounding tier.

Enable with ``GEMINI_SEARCH_BACKEND=antigravity``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any

import httpx

from ..settings import settings
from .gemini_search_tool import GeminiGroundingResult

logger = logging.getLogger(__name__)

INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
ANTIGRAVITY_AGENT_ID = "antigravity-preview-05-2026"
API_REVISION = "2026-05-20"

_TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "incomplete", "budget_exceeded"}
)
_BUDGET_STATUSES = frozenset({"incomplete", "budget_exceeded"})
_FAILURE_STATUSES = frozenset({"failed", "cancelled"})

_STRUCTURED_INSTRUCTION = (
    "\n\nRespond in valid JSON with this exact structure (no markdown fences):\n"
    '{"executive_summary": "brief summary", '
    '"key_findings": ["finding with [N] citation", ...], '
    '"sources": [{"url": "https://...", "title": "Source Title"}], '
    '"confidence": "high|medium|low", '
    '"uncertainties": null or ["gap description"]}'
)

_URL_RE = re.compile(r"https?://[^\s)\]>\"']+")


def _resolve_api_key() -> str:
    for candidate in (
        getattr(settings, "gemini_api_key", ""),
        os.environ.get("GEMINI_API_KEY", ""),
        getattr(settings, "gemini_second_api_key", ""),
        os.environ.get("GEMINI_SECOND_API_KEY", ""),
    ):
        if candidate:
            return candidate
    return ""


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
        "Api-Revision": API_REVISION,
    }


def _build_payload(
    query: str,
    system_instruction: str,
    structured_output: bool,
) -> dict[str, Any]:
    agent_config: dict[str, Any] = {
        "type": "antigravity",
        "model": settings.antigravity_model,
    }
    if settings.antigravity_max_total_tokens > 0:
        agent_config["max_total_tokens"] = settings.antigravity_max_total_tokens

    input_text = query + (_STRUCTURED_INSTRUCTION if structured_output else "")

    payload: dict[str, Any] = {
        "agent": ANTIGRAVITY_AGENT_ID,
        "input": [{"type": "text", "text": input_text}],
        "environment": "remote",
        "tools": [{"type": "google_search"}, {"type": "url_context"}],
        "background": True,
        "store": True,
        "agent_config": agent_config,
    }
    if system_instruction:
        payload["system_instruction"] = system_instruction
    return payload


def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = lines[1:] if lines[0].startswith("```") else lines
        lines = lines[:-1] if lines and lines[-1].startswith("```") else lines
        cleaned = "\n".join(lines).strip()
    return cleaned


def _parse_structured(answer_text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(_strip_code_fences(answer_text))
    except json.JSONDecodeError as exc:
        logger.debug("Antigravity structured output parse failed: %s", exc)
        return None
    return parsed if isinstance(parsed, dict) else None


def _dedupe(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        value = item.get(key)
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(item)
    return out


def _extract_citations(step_content: list[Any], into: list[dict[str, Any]]) -> None:
    for part in step_content:
        if not isinstance(part, dict):
            continue
        raw = (
            part.get("url_citations")
            or part.get("urlCitations")
            or part.get("annotations")
            or []
        )
        for cit in raw:
            if isinstance(cit, dict) and cit.get("url"):
                into.append({"url": str(cit["url"]), "title": cit.get("title")})


def _map_interaction_to_result(
    interaction: dict[str, Any],
    query: str,
    structured_output: bool,
) -> GeminiGroundingResult:
    status = str(interaction.get("status") or "completed")

    search_queries: list[str] = []
    fetched_urls: list[dict[str, Any]] = []  # successful url_context fetches
    citations: list[dict[str, Any]] = []
    last_step_texts: list[str] = []

    for step in interaction.get("steps") or []:
        if not isinstance(step, dict):
            continue
        stype = step.get("type")

        if stype == "google_search_call":
            args = step.get("arguments") or {}
            search_queries.extend(str(q) for q in (args.get("queries") or []))

        elif stype == "url_context_result":
            for item in step.get("result") or []:
                if isinstance(item, dict) and item.get("status") == "success" and item.get("url"):
                    fetched_urls.append({"url": str(item["url"]), "title": None})

        elif stype == "model_output":
            content = [p for p in (step.get("content") or []) if isinstance(p, dict)]
            texts = [str(p.get("text") or "") for p in content if p.get("type") == "text"]
            if texts:
                last_step_texts = texts
            _extract_citations(content, citations)

    answer_text = "\n".join(t for t in last_step_texts if t).strip()

    # Enrich fetched URLs with citation titles where available.
    title_by_url = {c["url"]: c.get("title") for c in citations if c.get("url")}
    sources = [
        {"url": s["url"], "title": title_by_url.get(s["url"])}
        for s in _dedupe(fetched_urls, "url")
    ]

    # Citation fallback chain: structured sources -> bare URLs found in answer.
    if not citations:
        structured_sources = None
        if structured_output:
            structured_sources = _parse_structured(answer_text)
        if structured_sources and structured_sources.get("sources"):
            citations = [
                {"url": s.get("url"), "title": s.get("title")}
                for s in structured_sources["sources"]
                if isinstance(s, dict) and s.get("url")
            ]
        else:
            citations = [
                {"url": m.group(0).rstrip(".,;)"), "title": None}
                for m in list(_URL_RE.finditer(answer_text))[:20]
            ]
    citations = _dedupe(citations, "url")

    structured_data: dict[str, Any] | None = None
    if structured_output and answer_text:
        structured_data = _parse_structured(answer_text)

    usage = interaction.get("usage") or {}
    prompt_tokens = int(usage.get("total_input_tokens") or 0)
    completion_tokens = int(usage.get("total_output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or 0)

    fallback_reason = None
    if status in _BUDGET_STATUSES:
        fallback_reason = f"antigravity_{status}"

    return GeminiGroundingResult(
        query=query,
        mode="single",
        answer=answer_text,
        structured_data=structured_data,
        sources=sources,
        search_queries=list(dict.fromkeys(search_queries)),
        model_used=f"antigravity/{settings.antigravity_model}",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        grounding_chunks_count=len(sources),
        web_search_queries_count=len(set(search_queries)),
        url_citations=citations,
        fallback_chain=["antigravity"],
        fallback_reason=fallback_reason,
    )


async def _post_json(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    url: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = await client.post(url, json=payload, headers=headers)
    if response.status_code >= 400:
        body = response.text[:300]
        raise RuntimeError(f"Antigravity HTTP {response.status_code} on {url}: {body}")
    return response.json()


async def _get_interaction(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    interaction_id: str,
) -> dict[str, Any]:
    response = await client.get(f"{INTERACTIONS_URL}/{interaction_id}", headers=headers)
    if response.status_code >= 400:
        body = response.text[:300]
        raise RuntimeError(f"Antigravity poll HTTP {response.status_code}: {body}")
    return response.json()


async def _cancel_best_effort(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    interaction_id: str,
) -> None:
    try:
        await client.post(
            f"{INTERACTIONS_URL}/{interaction_id}/cancel", json={}, headers=headers
        )
    except Exception as exc:  # noqa: BLE001 - cancel is best-effort cleanup
        logger.debug("Antigravity cancel failed for %s: %s", interaction_id, exc)


async def call_antigravity_grounding(
    query: str,
    system_prompt: str = "",
    structured_output: bool = True,
    span: Any = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> GeminiGroundingResult:
    """Run one Antigravity interaction and map it onto ``GeminiGroundingResult``.

    Raises on configuration/network failures and on failed/cancelled
    terminal statuses so callers can fall back to the grounding tier.
    """
    api_key = _resolve_api_key()
    if not api_key:
        raise RuntimeError(
            "Antigravity backend requires an API key: set ANTIGRAVITY_API_KEY "
            "(or GEMINI_API_KEY / GEMINI_SECOND_API_KEY)."
        )

    if span is not None:
        try:
            span.set_attribute("llm.model_name", f"antigravity/{settings.antigravity_model}")
            span.set_attribute("search.backend", "antigravity")
            span.set_attribute("search.query", query[:500])
        except Exception:  # noqa: BLE001 - telemetry must never break search
            pass

    headers = _headers(api_key)
    payload = _build_payload(query, system_prompt, structured_output)
    timeout_s = float(settings.antigravity_timeout_seconds)
    poll_interval = float(settings.antigravity_poll_interval_seconds)

    started = time.monotonic()
    try:
        async with httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(max(timeout_s, 30.0), connect=15.0),
        ) as client:
            created = await _post_json(client, headers, INTERACTIONS_URL, payload)
            interaction_id = str(created.get("id") or "")
            if not interaction_id:
                raise RuntimeError(f"Antigravity create returned no interaction id: {created}")

            deadline = started + timeout_s
            while True:
                interaction = await _get_interaction(client, headers, interaction_id)
                status = str(interaction.get("status") or "")

                if status in _TERMINAL_STATUSES:
                    break

                if time.monotonic() >= deadline:
                    await _cancel_best_effort(client, headers, interaction_id)
                    raise RuntimeError(
                        f"Antigravity interaction {interaction_id} timed out "
                        f"after {timeout_s:g}s (last status: {status or 'unknown'})."
                    )

                await asyncio.sleep(min(poll_interval, max(deadline - time.monotonic(), 0.0)))
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Antigravity connection error: {exc}") from exc

    status = str(interaction.get("status") or "")
    if status in _FAILURE_STATUSES:
        detail = interaction.get("error")
        if isinstance(detail, dict):
            detail = detail.get("message") or json.dumps(detail)[:300]
        raise RuntimeError(f"Antigravity interaction {status}: {detail or 'no error detail'}")

    result = _map_interaction_to_result(interaction, query, structured_output)

    duration_ms = (time.monotonic() - started) * 1000
    logger.info(
        "Antigravity backend complete: status=%s model=%s sources=%d queries=%d "
        "tokens=%d duration_ms=%.0f",
        status,
        result.model_used,
        len(result.sources),
        len(result.search_queries),
        result.total_tokens,
        duration_ms,
    )
    if span is not None:
        try:
            span.set_attribute("search.sources_count", len(result.sources))
            span.set_attribute("llm.token_count.total", result.total_tokens)
        except Exception:  # noqa: BLE001
            pass

    return result
