from __future__ import annotations

import asyncio
import json
import logging

from pydantic import ValidationError

try:
    from gradio_client import Client
except ImportError:  # pragma: no cover - dependency availability varies by environment
    Client = None

from ..settings import settings
from .normalize import normalize_query
from .query_policy import QueryRouting

logger = logging.getLogger(__name__)

_POLICY_SYSTEM_PROMPT = """
You classify coding-related web-search queries for a search orchestrator.

Return JSON only with this schema:
{
  "intent": "factual|navigational|troubleshooting|comparative|multi_hop|freshness_sensitive",
  "policy": {
    "mode": "bypass|light_rewrite|multi_rewrite|decompose",
    "reason": "short explanation",
    "must_keep_terms": ["exact literals to preserve"],
    "include_original": true
  }
}

Rules:
- Use `bypass` if the query contains exact IDs, quoted errors, URLs, repo/path operators, or other precision-sensitive literals.
- Use `light_rewrite` for ordinary troubleshooting or focused factual lookups.
- Use `multi_rewrite` for broader comparative, freshness-sensitive, or exploratory queries.
- Use `decompose` only when the query clearly contains multiple independent sub-questions.
- `must_keep_terms` should include exact strings that must survive rewriting.
- Never invent versions, package names, APIs, repo names, or issue numbers.
- Keep `reason` brief and concrete.
""".strip()


def _coerce_json_payload(raw_response: object) -> dict[str, object]:
    if isinstance(raw_response, dict):
        return raw_response
    if isinstance(raw_response, str):
        return json.loads(raw_response)
    raise ValueError(f"Unsupported HF Space response type: {type(raw_response).__name__}")


def _predict_policy_sync(query: str) -> QueryRouting:
    if Client is None:
        raise RuntimeError("gradio_client is not installed")
    client = Client(settings.query_policy_hf_space_url)
    raw_response = client.predict(
        message=normalize_query(query),
        system_message=_POLICY_SYSTEM_PROMPT,
        max_tokens=float(settings.query_policy_max_tokens),
        temperature=float(settings.query_policy_temperature),
        top_p=float(settings.query_policy_top_p),
        api_name="/_ui_chat",
    )
    payload = _coerce_json_payload(raw_response)
    return QueryRouting.model_validate(payload)


async def predict_query_routing_with_hf_space(query: str) -> QueryRouting | None:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_predict_policy_sync, query),
            timeout=settings.query_policy_timeout_seconds,
        )
    except (TimeoutError, ValidationError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("HF query policy prediction failed: %s", exc)
        return None
    except Exception as exc:
        logger.warning("HF query policy backend error: %s", exc)
        return None
