"""GCP Cloud Run (TEI or custom FastAPI) reranker client.

Supports private Cloud Run services using Google IAM ID tokens for auth
(by default) or static bearer token. Designed as drop-in replacement for
Voyage/Jina providers in the rerank pipeline.

Payload sent to /rerank:
  {"query": str, "texts": list[str], "top_n": optional}

Response expected (flexible parse):
  - list[ {"index": int, "score": float | "relevance_score": float } ]
  - or {"results": [...] } or {"data": [...] }

Compatible with:
- Hugging Face TEI /rerank (returns list or results with "score")
- Custom FastAPI example returning Jina-like {"results": [{"index":, "relevance_score":}] }

Env / settings:
  RERANK_GCP_CLOUDRUN_URL (required)
  RERANK_GCP_MODEL (for logging/telemetry)
  RERANK_GCP_TIMEOUT
  (optional) static token via RERANK_GCP_AUTH_TOKEN or passed api_key
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from ..retry import retry_with_backoff
from ..settings import settings

logger = logging.getLogger(__name__)

_GCP_RERANK_CLIENT: httpx.AsyncClient | None = None


def _get_gcp_rerank_client(timeout: float = 30.0) -> httpx.AsyncClient:
    global _GCP_RERANK_CLIENT
    if _GCP_RERANK_CLIENT is None or _GCP_RERANK_CLIENT.is_closed:
        _GCP_RERANK_CLIENT = httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
    return _GCP_RERANK_CLIENT


def _get_identity_token(audience: str) -> str | None:
    """Fetch Google ID token for Cloud Run audience using ADC.

    Works with:
    - gcloud auth application-default login (dev)
    - GOOGLE_APPLICATION_CREDENTIALS=sa-key.json (prod SA with run.invoker)
    - Running inside GCP (metadata server)

    Returns None on failure (caller may fall back to no-auth or static token).
    """
    try:
        # Lazy import so optional dep
        import google.auth  # noqa: F401  (imported for name binding in subsequent try; optional dep)
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2 import id_token as google_id_token
    except ImportError:
        logger.debug("google-auth not installed; cannot auto-fetch ID token for GCP reranker")
        return None

    try:
        request = GoogleRequest()
        # fetch_id_token handles ADC and produces audience-bound ID token
        token = google_id_token.fetch_id_token(request, audience)
        return token
    except Exception as exc:
        logger.warning("Failed to fetch GCP ID token for reranker audience=%s: %s", audience, exc)
        return None


def _normalize_documents(documents: list[str | dict]) -> list[str]:
    """Convert incoming documents (str or dict like Jina) to plain texts for TEI/custom."""
    texts: list[str] = []
    for d in documents:
        if isinstance(d, str):
            texts.append(d)
        elif isinstance(d, dict):
            # Support Jina-style or simple
            if "text" in d:
                texts.append(str(d["text"]))
            else:
                title = d.get("title", "")
                snippet = d.get("snippet", "") or d.get("text", "")
                url = d.get("url", "") or d.get("link", "")
                parts = []
                if title:
                    parts.append(f"Title: {title}")
                if url:
                    parts.append(f"URL: {url}")
                if snippet:
                    parts.append(f"Snippet: {snippet}")
                texts.append("\n".join(parts) if parts else str(d))
        else:
            texts.append(str(d))
    return texts


def _parse_rerank_results(data: Any, document_count: int) -> list[tuple[int, float]]:
    """Parse TEI/custom response flexibly into (index, score) list.

    Handles:
      - direct list of result dicts
      - {"results": [...]}
      - {"data": [...]}
    Looks for "relevance_score", "score", or "relevance".
    """
    if isinstance(data, dict):
        results = data.get("results") or data.get("data") or []
    elif isinstance(data, list):
        results = data
    else:
        results = []

    if not isinstance(results, list):
        raise ValueError("GCP rerank response missing results list")

    ranked: list[tuple[int, float]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        score = (
            item.get("relevance_score")
            or item.get("score")
            or item.get("relevance")
            or item.get("similarity")
        )
        if not isinstance(index, int) or not (0 <= index < document_count):
            # Some servers may return 0-based in order; be lenient but validate range later
            try:
                index = int(index)  # type: ignore[arg-type]
            except Exception:
                continue
        if not isinstance(score, (int, float)):
            continue
        if 0 <= index < document_count:
            ranked.append((index, float(score)))

    if not ranked and document_count > 0:
        raise ValueError("GCP rerank returned no valid ranked documents")

    return ranked


async def _do_rerank_post(
    *,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    http_client: httpx.AsyncClient | None,
    timeout: float,
) -> list[tuple[int, float]]:
    """Internal post + parse (for retry wrapper)."""
    client = http_client or _get_gcp_rerank_client(timeout)
    full_url = f"{url.rstrip('/')}/rerank"
    resp = await client.post(full_url, json=payload, headers=headers)
    resp.raise_for_status()
    doc_count = len(payload.get("texts", [])) or len(payload.get("documents", []))
    return _parse_rerank_results(resp.json(), doc_count)


async def gcp_cloudrun_rerank(
    query: str,
    documents: list[str | dict],
    *,
    url: str | None = None,
    api_key: str | None = None,  # treated as static bearer if provided
    model: str | None = None,
    top_n: int | None = None,
    timeout: float = 30.0,
    http_client: httpx.AsyncClient | None = None,
) -> list[tuple[int, float]]:
    """Rerank using a private GCP Cloud Run TEI or custom /rerank service.

    Authentication:
    - If api_key provided or RERANK_GCP_AUTH_TOKEN set: use as Bearer token.
    - Else: attempt Google ID token for the service URL (audience).
    - If no token obtainable and service is --allow-unauthenticated, request succeeds unauthed.
    """
    if not documents:
        return []

    resolved_url = (
        url or settings.rerank_gcp_cloudrun_url or os.environ.get("RERANK_GCP_CLOUDRUN_URL", "")
    )
    if not resolved_url.strip():
        raise ValueError(
            "RERANK_GCP_CLOUDRUN_URL (or equivalent) is required for gcp_cloudrun reranker"
        )

    resolved_timeout = timeout or getattr(settings, "rerank_gcp_timeout", 30.0)

    texts = _normalize_documents(documents)

    payload: dict[str, Any] = {
        "query": query,
        "texts": texts,
    }
    if top_n is not None:
        payload["top_n"] = top_n
    # Some custom servers may expect "documents" or "passages"; we send "texts" (TEI standard).
    # TEI ignores extra; custom can be written to accept either.

    headers: dict[str, str] = {
        "Content-Type": "application/json",
    }

    # Determine auth
    static_token = api_key or os.environ.get("RERANK_GCP_AUTH_TOKEN", "")
    if static_token.strip():
        headers["Authorization"] = f"Bearer {static_token.strip()}"
    else:
        # Auto ID token for private Cloud Run
        token = _get_identity_token(resolved_url)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            logger.debug(
                "No static token and no GCP ID token obtained; attempting unauthenticated call (service must allow unauth or be reachable)"
            )

    # Wrap in retry for transient
    async def _execute() -> list[tuple[int, float]]:
        return await _do_rerank_post(
            url=resolved_url,
            payload=payload,
            headers=headers,
            http_client=http_client,
            timeout=resolved_timeout,
        )

    try:
        return await retry_with_backoff(
            _execute,
            max_retries=2,
            initial_delay_ms=300,
            max_delay_ms=2000,
            provider_name="gcp_cloudrun_rerank",
        )
    except Exception:
        # Let caller (core) handle fallback
        raise
