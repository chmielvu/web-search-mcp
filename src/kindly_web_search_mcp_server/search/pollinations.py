"""Pollinations AI web search client for Perplexity Sonar models."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# --- Constants ---
BASE_URL = "https://gen.pollinations.ai"
REQUEST_TIMEOUT = 30.0

# Removed 'fast'/'gemini-fast' per user request
MODEL_MAPPING = {
    "normal": "perplexity-fast",
    "deep": "perplexity-reasoning",
}

# System prompt ONLY controls response style/tone, NOT search behavior.
# Perplexity's real-time search component does NOT attend to system prompts.
# All factual query guidance must be in user prompts.
SYSTEM_PROMPT = (
    "You are a concise, precise research assistant. "
    "Provide factual answers with numbered citations. "
    "Keep responses professional and succinct."
)

# User prompt templates - these control search behavior
# Template for Sonar (normal/fast queries)
USER_PROMPT_TEMPLATE_NORMAL = """
{query}

Research context: {research_goal}

Requirements:
- Provide factual information with numbered citations [1], [2], etc.
- If specific information cannot be found from reliable sources, state this clearly.
- Focus on verifiable facts from authoritative sources.
- Keep the research context in mind when prioritizing information.
"""

# Template for Sonar Reasoning Pro (deep/analytical queries)
USER_PROMPT_TEMPLATE_REASONING = """
{query}

Research context: {research_goal}

Requirements:
- Provide step-by-step analysis with reasoning for each conclusion.
- Include numbered citations [1], [2], etc. for each factual claim.
- If specific information cannot be found, state which aspects were unavailable.
- Distinguish between verified facts and analytical interpretations.
- Keep the research context in mind when prioritizing analysis depth.
"""


class PollinationsClient:
    """HTTP client for Pollinations AI web search API (Perplexity Sonar)."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        timeout: float = REQUEST_TIMEOUT,
        api_key: str | None = None,
    ) -> None:
        self.base_url = os.getenv("POLLINATIONS_BASE_URL", base_url).rstrip("/")
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

        # Default research_goal if not provided
        goal = research_goal or "General information gathering"

        # Select appropriate user prompt template based on model type
        if depth == "deep":
            user_content = USER_PROMPT_TEMPLATE_REASONING.format(
                query=query.strip(), research_goal=goal
            )
        else:
            user_content = USER_PROMPT_TEMPLATE_NORMAL.format(
                query=query.strip(), research_goal=goal
            )

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        }

        url = f"{self.base_url}/v1/chat/completions"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    url, json=payload, headers=self._get_headers()
                )
                response.raise_for_status()
            except httpx.TimeoutException as exc:
                raise httpx.HTTPError(
                    f"Request timed out after {self.timeout}s"
                ) from exc
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    raise httpx.HTTPError(
                        "Rate limited. Please try again later."
                    ) from exc
                raise

        data = response.json()
        answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")

        # Extract citations from response
        sources = data.get("citations", [])
        if not sources and isinstance(answer, str):
            sources = re.findall(r'https?://[^\s<>"\']+', answer)
            sources = list(dict.fromkeys(sources))

        return {"answer": answer, "sources": sources, "model": model, "query": query}


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

    payload = {
        "model": GEMINI_SEARCH_MODEL,
        "messages": [{"role": "user", "content": query}],
    }

    url = f"{client.base_url}/v1/chat/completions"

    async with httpx.AsyncClient(timeout=GEMINI_SEARCH_TIMEOUT) as http:
        try:
            response = await http.post(url, json=payload, headers=client._get_headers())
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise httpx.HTTPError(
                f"gemini-search timed out after {GEMINI_SEARCH_TIMEOUT}s"
            ) from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise httpx.HTTPError("Rate limited. Please try again later.") from exc
            raise

    data = response.json()

    # Extract groundingMetadata from choices
    choices = data.get("choices", [])
    if not choices:
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

    return {
        "groundingMetadata": {
            "webSearchQueries": grounding_metadata.get("webSearchQueries", []),
            "groundingChunks": normalized_chunks[:num_results],
            "groundingSupports": normalized_supports,
        },
        "model": data.get("model", GEMINI_SEARCH_MODEL),
        "provider": data.get("provider", "vertex-ai"),
        "usage": data.get("usage", {}),
        "query": query,
    }
