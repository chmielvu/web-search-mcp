"""FlockMTL DuckDB bridge for SQL-native LLM evaluations."""

from __future__ import annotations

import logging
from typing import Any

from ..chain import get_chain

logger = logging.getLogger(__name__)


def ensure_flockmtl_secret_from_catalog(connection: Any) -> None:
    """Synchronize DuckDB FlockMTL secret DDL lifecycle using catalog credentials."""
    chain = get_chain("worker_llm")
    hf_spec = None
    for spec in chain.models:
        if spec.provider == "huggingface":
            hf_spec = spec
            break

    if not hf_spec:
        logger.warning("No Hugging Face spec found in catalog for FlockMTL secret")
        return

    api_key = hf_spec.api_key
    base_url = "https://router.huggingface.co/v1"

    if not api_key:
        logger.debug(
            "HF_TOKEN not set in environment — FlockMTL llm_complete calls "
            "will fail. Set HF_TOKEN before running SQL-native judge evaluations."
        )
        return

    try:
        connection.execute("DROP SECRET IF EXISTS __default_openai")
    except Exception:
        pass

    safe_key = api_key.replace("'", "''")
    safe_url = base_url.replace("'", "''")
    try:
        connection.execute(
            f"CREATE SECRET __default_openai (TYPE OPENAI, API_KEY '{safe_key}', BASE_URL '{safe_url}')"
        )
    except Exception as exc:
        logger.warning("Failed to register FlockMTL secret from catalog: %s", exc)
