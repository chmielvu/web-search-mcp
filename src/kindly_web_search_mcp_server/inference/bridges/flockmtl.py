"""FlockMTL DuckDB bridge for SQL-native LLM evaluations.

The SQL-native fallback registry points at NanoGPT (OpenAI-compatible,
subscription endpoint). The Hugging Face router was retired from judge
inference on 2026-08-22; see ``analytics/judges.py`` for the two-stage
production chain that only falls back to FlockMTL ``llm_complete`` when
both stages exhaust.
"""

from __future__ import annotations

import logging
from typing import Any

from ...settings import settings

logger = logging.getLogger(__name__)


def ensure_flockmtl_secret_from_catalog(connection: Any) -> None:
    """Synchronize DuckDB FlockMTL secret DDL lifecycle using judge-chain creds.

    Registers `__default_openai` against NanoGPT so the dormant FlockMTL
    ``llm_complete`` last resort stays callable when both production chain
    stages fail. Kept name-compatible for existing callers.
    """
    api_key = settings.nano_gpt_api_key
    base_url = settings.judge_nanogpt_base_url

    if not api_key:
        logger.debug(
            "NANOGPT_API_KEY not set in environment — FlockMTL llm_complete "
            "calls will fail. Set NANOGPT_API_KEY before running SQL-native "
            "judge evaluations."
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
