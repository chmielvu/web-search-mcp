"""DuckDB persistence helpers for the judge pipeline.

Owns the short-lived analytics DuckDB connection (``_connect``), the
FlockMTL extension + secret bootstrap (``_ensure_loaded``), and the
two ``llm_judgments`` writers (``_insert_judgment``,
``_store_judgment_row``). The default rubric version stamped on every
judgment row lives here.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import duckdb

from ..writers.connection import _LOCK, _db_path, ensure_flockmtl_loaded

logger = logging.getLogger(__name__)

# Default rubric version stamped on every judgment row. A future prompt
# bump would set this to 'v2' (and rename the FlockMTL prompt) so v1 and
# v2 coexist in the catalog and trend comparisons stay filterable.
_DEFAULT_RUBRIC_VERSION = "v1"


def _connect(db_path: str | None = None) -> duckdb.DuckDBPyConnection:
    """Open a short-lived analytics DuckDB connection (read-write)."""
    return duckdb.connect(str(_db_path(db_path)), read_only=False)


def _ensure_loaded(connection: duckdb.DuckDBPyConnection) -> bool:
    """Ensure the FlockMTL extension + secret are usable on this connection.

    The `__default_openai` secret is connection-local (CREATE SECRET
    without PERSISTENT is per-connection), so we re-register on every
    connection that runs `llm_complete`. The CREATE SECRET DDL must
    serialize with the user-DB writer: a concurrent catalog write
    (e.g. `compute_search_quality` still committing on the write
    executor) raises TransactionContextError. We serialize under the
    analytics `_LOCK` and retry briefly on catalog-write conflict.
    """
    if not ensure_flockmtl_loaded(connection):
        return False
    try:
        # Lazy import to avoid circular dependency at module import time.
        from ..writers.connection import _LOCK, _ensure_flockmtl_secret

        for _attempt in range(5):
            with _LOCK:
                try:
                    _ensure_flockmtl_secret(connection)
                    return True
                except duckdb.TransactionException as exc:
                    # Catalog write-write conflict with the writer
                    # thread; brief retry after the writer commits.
                    logger.debug("flockmtl secret catalog conflict, retrying: %s", exc)
                    time.sleep(0.05)
        logger.warning("flockmtl secret registration exhausted retries")
        return False
    except Exception:
        logger.exception("flockmtl secret registration failed")
        return False


def _insert_judgment(
    connection: duckdb.DuckDBPyConnection,
    *,
    run_key: str,
    judgment_kind: str,
    judgment_target: str | None,
    prompt_name: str,
    model_name: str,
    verdict: str | None,
    duration_ms: float,
    status: str,
    facet: str | None = None,
    reasoning: str | None = None,
    rubric_version: str = _DEFAULT_RUBRIC_VERSION,
    confidence: int | None = None,
    context_shown: str | None = None,
    error_message: str | None = None,
    payload_json: str | None = None,
) -> None:
    """Persist one judgment row to `llm_judgments` (extended schema)."""
    # Serialize inserts: parallel facet workers each hold their own
    # connection; DuckDB file DB still needs a process-wide write lock.
    with _LOCK:
        connection.execute(
            """
            INSERT INTO llm_judgments (
                run_key, judgment_kind, judgment_target, prompt_name, model_name,
                verdict, duration_ms, status, error_message, payload_json,
                facet, reasoning, rubric_version, confidence, context_shown
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_key,
                judgment_kind,
                judgment_target,
                prompt_name,
                model_name,
                verdict if verdict is not None else "",
                round(duration_ms * 1000.0, 2),
                status,
                error_message,
                payload_json,
                facet,
                reasoning if reasoning is not None else "",
                rubric_version,
                confidence,
                context_shown,
            ],
        )


def _store_judgment_row(
    connection: duckdb.DuckDBPyConnection,
    *,
    run_key: str,
    judgment_kind: str,
    judgment_target: str | None,
    prompt_name: str,
    model_name: str,
    raw: str | None,
    duration: float,
    parsed: dict | None,
    context_columns: list[dict[str, object]],
    build_verdict: Any,
    build_reasoning: Any,
) -> None:
    """Store one judgment row (success inserts parsed values; error persists raw truncated text).

    `parsed is None` (parse failure or missing `[RESULT]`) -> status='error',
    raw text truncated to 200 chars into the `verdict` column, empty
    `reasoning`. Errors are auditable per facet.
    """
    context_shown = json.dumps(
        [{"name": c.get("name"), "data": str(c.get("data"))[:200]} for c in context_columns]
    )
    if parsed is not None:
        try:
            verdict_str = build_verdict(parsed) or ""
            reasoning_str = build_reasoning(parsed) or ""
        except Exception as exc:
            logger.warning(
                "build_verdict/build_reasoning raised for kind=%s target=%s: %s",
                judgment_kind,
                judgment_target,
                exc,
            )
            verdict_str = (raw or "")[:200]
            reasoning_str = ""
            confidence_int: int | None = None
            status = "error"
            error_message = f"build raised: {exc}"
        else:
            conf = parsed.get("confidence")
            confidence_int = conf if isinstance(conf, int) else None
            status = "success"
            error_message = None
            payload_json = json.dumps(
                {
                    "facet": judgment_kind,
                    "schema_version": _DEFAULT_RUBRIC_VERSION,
                    "parsed": parsed,
                },
                ensure_ascii=False,
                default=str,
            )
    else:
        verdict_str = (raw or "")[:200]
        reasoning_str = ""
        confidence_int = None
        status = "error"
        error_message = "no [RESULT] parse" if raw else "no llm output"
        payload_json = json.dumps(
            {
                "facet": judgment_kind,
                "schema_version": _DEFAULT_RUBRIC_VERSION,
                "error": error_message,
                "raw_preview": (raw or "")[:500],
            },
            ensure_ascii=False,
        )
    if parsed is not None and status == "error":
        payload_json = json.dumps(
            {
                "facet": judgment_kind,
                "schema_version": _DEFAULT_RUBRIC_VERSION,
                "error": error_message,
                "raw_preview": (raw or "")[:500],
            },
            ensure_ascii=False,
        )
    _insert_judgment(
        connection,
        run_key=run_key,
        judgment_kind=judgment_kind,
        judgment_target=judgment_target,
        prompt_name=prompt_name,
        model_name=model_name,
        verdict=verdict_str,
        duration_ms=duration,
        status=status,
        facet=judgment_kind,
        reasoning=reasoning_str,
        rubric_version=_DEFAULT_RUBRIC_VERSION,
        confidence=confidence_int,
        context_shown=context_shown,
        error_message=error_message,
        payload_json=payload_json,
    )
