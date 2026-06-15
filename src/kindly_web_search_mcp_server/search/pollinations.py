"""Pollinations AI web search client for Perplexity Sonar models."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

import httpx

from ..prompts.provider_perplexity import build_provider_perplexity_prompt
from ..llm.usage import extract_llm_usage
from ..settings import settings
from ..telemetry import create_llm_operation_span, set_span_error, set_span_success

logger = logging.getLogger(__name__)

# --- Constants ---
BASE_URL = "https://gen.pollinations.ai"
REQUEST_TIMEOUT = 30.0

# Shared httpx client per event loop to avoid creating a new client (and a new
# connection pool / TLS handshake) on every request.
_SHARED_CLIENTS: dict[asyncio.AbstractEventLoop, httpx.AsyncClient] = {}


def _get_shared_client(timeout: float) -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    client = _SHARED_CLIENTS.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=timeout)
        _SHARED_CLIENTS[loop] = client
    return client

# Removed 'fast'/'gemini-fast' per user request
MODEL_MAPPING = {
    "normal": "perplexity-fast",
    "deep": "perplexity-reasoning",
}

class PollinationsClient:
    """HTTP client for Pollinations AI web search API (Perplexity Sonar)."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        timeout: float = REQUEST_TIMEOUT,
        api_key: str | None = None,
    ) -> None:
        resolved_base_url = (
            settings.pollinations_base_url
            if base_url == BASE_URL
            else base_url
        )
        self.base_url = resolved_base_url.rstrip("/")
        self.timeout = timeout
        self.api_key = api_key or os.getenv("POLLINATIONS_API_KEY")

    def _get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _resolve_model(self, depth: str) -> str:
        """Resolve depth to model name. Defaults to perplexity-fast."""
        return MODEL_MAPPING.get(depth, "perplexity-fast")

    async def web_search(
        self,
        query: str,
        depth: str = "normal",
        research_goal: str | None = None,
    ) -> dict[str, Any]:
        """Perform web search using Pollinations AI (Perplexity Sonar).

        Args:
            query: Search query string
            depth: 'normal' (perplexity-fast) or 'deep' (perplexity-reasoning)
            research_goal: Optional context/goal from client to guide research focus

        Returns:
            dict with 'answer', 'sources', 'model', 'query'
        """
        if not query or not query.strip():
            raise ValueError("Query cannot be empty")
        if not self.api_key:
            raise ValueError("POLLINATIONS_API_KEY not configured")

        model = self._resolve_model(depth)
        with create_llm_operation_span(
            "web_search",
            system="pollinations",
            attributes={
                "gen_ai.request.model": model,
                "search.query": query[:500],
                "search.depth": depth,
                "search.research_goal": (research_goal or "")[:500],
            },
        ) as span:
            # Default research_goal if not provided
            goal = research_goal or "General information gathering"
            system_content, user_content = build_provider_perplexity_prompt(
                query=query.strip(),
                research_goal=goal,
                provider_name="perplexity",
            )
            if depth == "deep":
                user_content += (
                    "\n\nRequirements:\n"
                    "- Provide step-by-step analysis with reasoning for each conclusion.\n"
                    "- Include numbered citations [1], [2], etc. for each factual claim.\n"
                    "- If specific information cannot be found, state which aspects were unavailable.\n"
                    "- Distinguish between verified facts and analytical interpretations.\n"
                    "- Keep the research context in mind when prioritizing analysis depth.\n"
                )
            else:
                user_content += (
                    "\n\nRequirements:\n"
                    "- Provide factual information with numbered citations [1], [2], etc.\n"
                    "- If specific information cannot be found from reliable sources, state this clearly.\n"
                    "- Focus on verifiable facts from authoritative sources.\n"
                    "- Keep the research context in mind when prioritizing information.\n"
                )

            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_content},
                ],
            }

            url = f"{self.base_url}/v1/chat/completions"

            client = _get_shared_client(self.timeout)
            try:
                response = await client.post(
                    url, json=payload, headers=self._get_headers(), timeout=self.timeout
                )
                response.raise_for_status()
            except httpx.TimeoutException as exc:
                set_span_error(span, exc)
                raise httpx.HTTPError(
                    f"Request timed out after {self.timeout}s"
                ) from exc
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    set_span_error(span, exc)
                    raise httpx.HTTPError(
                        "Rate limited. Please try again later."
                    ) from exc
                set_span_error(span, exc)
                raise
            except Exception as exc:
                set_span_error(span, exc)
                raise

            data = response.json()
            answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = extract_llm_usage(data)

            # Extract citations from response
            sources = data.get("citations", [])
            if not sources and isinstance(answer, str):
                sources = re.findall(r'https?://[^\s<>"\']+', answer)
                sources = list(dict.fromkeys(sources))

            if usage:
                if usage.input_tokens is not None:
                    span.set_attribute("gen_ai.usage.prompt_tokens", usage.input_tokens)
                if usage.output_tokens is not None:
                    span.set_attribute("gen_ai.usage.completion_tokens", usage.output_tokens)
                if usage.total_tokens is not None:
                    span.set_attribute("gen_ai.usage.total_tokens", usage.total_tokens)
            span.set_attribute("search.source_count", len(sources))
            span.set_attribute("search.answer_chars", len(answer) if isinstance(answer, str) else 0)
            set_span_success(span, result_count=len(sources))
            return {
                "answer": answer,
                "sources": sources,
                "model": model,
                "model_used": data.get("model", model),
                "input_tokens": usage.input_tokens if usage else None,
                "output_tokens": usage.output_tokens if usage else None,
                "query": query,
            }


# Singleton client (lazy init)
_POLLINATIONS_CLIENT: PollinationsClient | None = None


def get_pollinations_client() -> PollinationsClient:
    """Get or create the Pollinations client singleton."""
    global _POLLINATIONS_CLIENT
    if _POLLINATIONS_CLIENT is None:
        _POLLINATIONS_CLIENT = PollinationsClient()
    return _POLLINATIONS_CLIENT


# ============================================================================
# gemini-search Provider Integration
# ============================================================================

GEMINI_SEARCH_MODEL = "gemini-search"
GEMINI_SEARCH_TIMEOUT = 30.0


async def gemini_grounding_search(
    query: str,
    num_results: int = 10,
) -> dict[str, Any]:
    """Search using Pollinations gemini-search model.

    Returns the full API response with groundingMetadata intact:
    - choices[0].groundingMetadata.webSearchQueries
    - choices[0].groundingMetadata.groundingChunks (PRIMARY focus)
    - choices[0].groundingMetadata.groundingSupports

    Args:
        query: Search query
        num_results: Approximate number of grounding chunks to expect

    Returns:
        dict with groundingMetadata, model, and raw response fields
    """
    client = get_pollinations_client()
    if not client.api_key:
        raise ValueError("POLLINATIONS_API_KEY not configured")
    with create_llm_operation_span(
        "grounding_search",
        system="pollinations",
        attributes={
            "gen_ai.request.model": GEMINI_SEARCH_MODEL,
            "search.query": query[:500],
            "search.num_results_requested": num_results,
        },
    ) as span:
        payload = {
            "model": GEMINI_SEARCH_MODEL,
            "messages": [{"role": "user", "content": query}],
        }

        url = f"{client.base_url}/v1/chat/completions"

        http = _get_shared_client(GEMINI_SEARCH_TIMEOUT)
        try:
            response = await http.post(
                url,
                json=payload,
                headers=client._get_headers(),
                timeout=GEMINI_SEARCH_TIMEOUT,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            set_span_error(span, exc)
            raise httpx.HTTPError(
                f"gemini-search timed out after {GEMINI_SEARCH_TIMEOUT}s"
            ) from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                set_span_error(span, exc)
                raise httpx.HTTPError("Rate limited. Please try again later.") from exc
            set_span_error(span, exc)
            raise
        except Exception as exc:
            set_span_error(span, exc)
            raise

        data = response.json()
        usage = extract_llm_usage(data)

        # Extract groundingMetadata from choices
        choices = data.get("choices", [])
        if not choices:
            span.set_attribute("grounding.chunk_count", 0)
            set_span_success(span, result_count=0)
            return {"groundingMetadata": {}, "model": GEMINI_SEARCH_MODEL, "query": query}

        choice = choices[0]
        grounding_metadata = choice.get("groundingMetadata", {})

        # Normalize groundingChunks structure
        grounding_chunks = grounding_metadata.get("groundingChunks", [])
        normalized_chunks = []
        for chunk in grounding_chunks:
            web = chunk.get("web", {})
            if web.get("uri") and web.get("title"):
                normalized_chunks.append(
                    {
                        "uri": web.get("uri"),
                        "title": web.get("title"),
                        "domain": web.get("domain"),
                    }
                )

        # Normalize groundingSupports for snippet extraction
        grounding_supports = grounding_metadata.get("groundingSupports", [])
        normalized_supports = []
        for support in grounding_supports:
            segment = support.get("segment", {})
            normalized_supports.append(
                {
                    "text": segment.get("text", ""),
                    "start_index": segment.get("startIndex"),
                    "end_index": segment.get("endIndex"),
                    "chunk_indices": support.get("groundingChunkIndices", []),
                }
            )

        span.set_attribute("grounding.chunk_count", len(normalized_chunks[:num_results]))
        span.set_attribute("grounding.support_count", len(normalized_supports))
        if usage:
            if usage.input_tokens is not None:
                span.set_attribute("gen_ai.usage.prompt_tokens", usage.input_tokens)
            if usage.output_tokens is not None:
                span.set_attribute("gen_ai.usage.completion_tokens", usage.output_tokens)
            if usage.total_tokens is not None:
                span.set_attribute("gen_ai.usage.total_tokens", usage.total_tokens)
        set_span_success(span, result_count=len(normalized_chunks[:num_results]))
        return {
            "groundingMetadata": {
                "webSearchQueries": grounding_metadata.get("webSearchQueries", []),
                "groundingChunks": normalized_chunks[:num_results],
                "groundingSupports": normalized_supports,
            },
            "model": data.get("model", GEMINI_SEARCH_MODEL),
            "model_used": data.get("model", GEMINI_SEARCH_MODEL),
            "provider": data.get("provider", "vertex-ai"),
            "usage": data.get("usage", {}),
            "input_tokens": usage.input_tokens if usage else None,
            "output_tokens": usage.output_tokens if usage else None,
            "query": query,
        }
