"""FlockMTL-backed semantic judgment orchestrator.

Runs FlockMTL prompts on each completed search run and persists
verdicts to the `llm_judgments` table. This is the "judge every search
automatically" path the user asked for — no per-row LLM calls from
views, no surprise API costs on dashboard refresh.

Per-search judge pass fires six facet-decomposed judgments, all through
the SAME two-stage chain (Gemini/Gemma stage 1 -> NanoGPT/DeepSeek-thinking
stage 2); the `judge_quality` / `judge_fast` alias is provenance-only.

  a. `judge_run_overview`     -- 1 call/run; holistic good/mixed/bad
                                + analysis + recommendations
  b. `judge_intent_coherence` -- 1 call/run; intent matches query+goal
  c. `judge_rewrite_coverage` -- 1 call/run; if rewrite_enabled &
                                non-empty rewrites; counts distinct facets
  d. `judge_rerank_improvement` -- 1 call per rerank_stages row;
                                positional only (no reranker scores)
  e. `judge_result_quality`   -- 1 call per final_results row (≤ 15/run);
                                snippet-only, NO reranker scores
  f. `judge_failure_cause`    -- 1 call/run if status != 'success' OR
                                final_count == 0; few-shot triage

Each call to `judge_search_run(run_key)` opens its own short-lived
DuckDB connection (LOADs FlockMTL on it, registers the
`__default_openai` secret), runs the prompts, INSERTs the verdicts,
and closes. Failures are caught and persisted as `status='error'` rows
so the orchestrator never crashes the calling search pipeline.

Cost guard: skips entirely if `settings.flockmtl_enabled` is False.

For search-pipeline callers, use `schedule_judge_search_run(run_key)` --
fire-and-forget on a thread pool, never blocks the search response.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import weakref
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from concurrent.futures.thread import _worker  # type: ignore[attr-defined]
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

import duckdb

from ..settings import settings
from .writers.connection import _LOCK, _db_path, ensure_flockmtl_loaded

logger = logging.getLogger(__name__)
# Per-facet JSON Schemas — single source of truth.
#
# Each schema is used in THREE places and they MUST stay in sync:
#   1. As `response_format.json_schema.schema` in the NanoGPT structured call
#      (`_run_prompt` schema-mode below). The model is forced to
#      produce JSON that matches this exact shape (strict=true).
#   2. Inlined into the prompt template's `### Output Format` footer as
#      a JSON example, so models that ignore the response_format still
#      emit structurally identical text on the prose fallback path.
#   3. Referenced by `_parse_result` to populate `verdict`,
#      `confidence`, `reasoning`/`analysis`, etc. consistently across
#      both paths. Without this, the prose fallback drops the analysis
#      into the empty `reasoning` column on the `llm_judgments` row.
#
# Adding a facet here = adding a `judge_*` prompt in
# `writers/connection.py::_FLOCKMTL_PROMPTS` + an entry in
# `judge_search_run`'s facet dispatch. All three MUST stay consistent.
_FACET_SCHEMAS: dict[str, dict[str, object]] = {
    "judge_run_overview": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["good", "mixed", "bad"]},
            "analysis": {"type": "string", "minLength": 1},
            "recommendations": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 0,
            },
            "confidence": {"type": "integer", "minimum": 1, "maximum": 4},
        },
        "required": ["verdict", "analysis", "recommendations", "confidence"],
        "additionalProperties": False,
    },
    "judge_intent_coherence": {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["coherent", "partially_coherent", "incoherent"],
            },
            "confidence": {"type": "integer", "minimum": 1, "maximum": 4},
            "reasoning": {"type": "string", "minLength": 1},
        },
        "required": ["verdict", "confidence", "reasoning"],
        "additionalProperties": False,
    },
    "judge_rewrite_coverage": {
        "type": "object",
        "properties": {
            "covered_count": {"type": "integer", "minimum": 0, "maximum": 5},
            "redundant": {"type": "boolean"},
            "missing_facets": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 0,
            },
            "confidence": {"type": "integer", "minimum": 1, "maximum": 4},
            "reasoning": {"type": "string", "minLength": 1},
        },
        "required": [
            "covered_count",
            "redundant",
            "missing_facets",
            "confidence",
            "reasoning",
        ],
        "additionalProperties": False,
    },
    "judge_rerank_improvement": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["improved", "neutral", "degraded"]},
            "confidence": {"type": "integer", "minimum": 1, "maximum": 4},
            "reasoning": {"type": "string", "minLength": 1},
        },
        "required": ["verdict", "confidence", "reasoning"],
        "additionalProperties": False,
    },
    "judge_result_quality": {
        "type": "object",
        "properties": {
            "intent_match": {"type": "boolean"},
            "informativeness": {"type": "integer", "minimum": 1, "maximum": 4},
            "confidence": {"type": "integer", "minimum": 1, "maximum": 4},
            "reasoning": {"type": "string", "minLength": 1},
        },
        "required": ["intent_match", "informativeness", "confidence", "reasoning"],
        "additionalProperties": False,
    },
    "judge_failure_cause": {
        "type": "object",
        "properties": {
            "root_cause": {
                "type": "string",
                "enum": [
                    "no_results",
                    "irrelevant_sources",
                    "rerank_error",
                    "provider_timeout",
                    "other",
                ],
            },
            "stage": {"type": "string", "minLength": 1},
            "suggested_fix": {"type": "string", "minLength": 1},
            "confidence": {"type": "integer", "minimum": 1, "maximum": 4},
            "reasoning": {"type": "string", "minLength": 1},
        },
        "required": [
            "root_cause",
            "stage",
            "suggested_fix",
            "confidence",
            "reasoning",
        ],
        "additionalProperties": False,
    },
}
# Lazy singleton ThreadPoolExecutor for fire-and-forget judge runs.
# Sized to absorb bursts without blocking the search event loop.
#
# Exit contract (CLI): workers are daemon and are intentionally NOT
# registered in concurrent.futures' ``_threads_queues``. CPython's
# ``_python_exit`` joins every registered worker even when daemon=True;
# omitting registration lets CLI exit abandon in-flight judge calls after
# ``shutdown_judge_executor(wait=False)``. Incomplete judgment rows are
# acceptable on CLI exit (same tradeoff as DuckDB write cancel).


class _DaemonThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor with daemon workers that atexit will not join.

    Mirrors CPython's ``_adjust_thread_count`` but:
    - sets ``daemon=True`` before ``start()``
    - does **not** insert into ``_threads_queues`` (so ``_python_exit``
      cannot block process exit on abandoned judge work)
    """

    def _adjust_thread_count(self) -> None:  # noqa: SLF001
        if self._idle_semaphore.acquire(timeout=0):  # noqa: SLF001
            return

        def weakref_cb(_, q=self._work_queue):  # noqa: SLF001
            q.put(None)

        num_threads = len(self._threads)  # noqa: SLF001
        if num_threads < self._max_workers:
            thread_name = "%s_%d" % (self._thread_name_prefix or self, num_threads)
            t = threading.Thread(
                name=thread_name,
                target=_worker,
                args=(
                    weakref.ref(self, weakref_cb),
                    self._work_queue,  # noqa: SLF001
                    self._initializer,  # noqa: SLF001
                    self._initargs,  # noqa: SLF001
                ),
                daemon=True,
            )
            t.start()
            self._threads.add(t)  # noqa: SLF001
            # Deliberately skip: _threads_queues[t] = self._work_queue


@dataclass(frozen=True, slots=True)
class _JudgeExecutorLifecycle:
    generation: int
    state: str


_JUDGE_EXECUTOR: ThreadPoolExecutor | None = None
_JUDGE_EXECUTOR_LOCK = Lock()
_JUDGE_LIFECYCLE = _JudgeExecutorLifecycle(generation=0, state="running")
_JUDGE_SCHEDULE_LOCK = Lock()
"""Serializes lifecycle checks with executor acquisition and shutdown."""


def _get_judge_executor() -> ThreadPoolExecutor:
    global _JUDGE_EXECUTOR
    if _JUDGE_EXECUTOR is None:
        with _JUDGE_EXECUTOR_LOCK:
            if _JUDGE_EXECUTOR is None:
                _JUDGE_EXECUTOR = _DaemonThreadPoolExecutor(
                    max_workers=4,
                    thread_name_prefix="judge",
                )
    return _JUDGE_EXECUTOR


def shutdown_judge_executor(*, wait: bool = False) -> None:
    """Stop the shared judge ThreadPoolExecutor.

    ``wait=False`` cancels pending facet/run jobs (``cancel_futures=True``).
    Workers are daemon and not in concurrent.futures' atexit join map, so CLI
    process exit is not pinned by in-flight judge calls.

    The lifecycle state blocks new submissions only while this shutdown is in
    progress. Completion advances the executor generation and reopens the
    scheduler for a fresh lazy executor.
    """
    global _JUDGE_EXECUTOR, _JUDGE_LIFECYCLE
    with _JUDGE_SCHEDULE_LOCK:
        if _JUDGE_LIFECYCLE.state == "shutting_down":
            return
        generation = _JUDGE_LIFECYCLE.generation
        _JUDGE_LIFECYCLE = _JudgeExecutorLifecycle(
            generation=generation,
            state="shutting_down",
        )
        with _JUDGE_EXECUTOR_LOCK:
            executor = _JUDGE_EXECUTOR
            _JUDGE_EXECUTOR = None
    try:
        if executor is not None:
            executor.shutdown(wait=wait, cancel_futures=not wait)
    finally:
        with _JUDGE_SCHEDULE_LOCK:
            if (
                _JUDGE_LIFECYCLE.generation == generation
                and _JUDGE_LIFECYCLE.state == "shutting_down"
            ):
                _JUDGE_LIFECYCLE = _JudgeExecutorLifecycle(
                    generation=generation + 1,
                    state="running",
                )


# Default rubric version stamped on every judgment row. A future prompt
# bump would set this to 'v2' (and rename the FlockMTL prompt) so v1 and
# v2 coexist in the catalog and trend comparisons stay filterable.
_DEFAULT_RUBRIC_VERSION = "v1"

# Hard ceiling for any single rerank probe the digest reports: we
# top-10 to keep the digest ≤ ~2k tokens.
_DIGEST_TOP_N_FINAL = 10

# Score names banned from `judge_run_overview`, `judge_rerank_improvement`,
# and `judge_result_quality` contexts (G-Eval self-enhancement bias).
_BANNED_RERANK_SCORES = (
    "final_score",
    "llm_raw_score",
    "cross_encoder_raw",
    "fused_score",
    "hybrid_rrf_score",
)

# Module-level model selector for `judge_search_run`. Both aliases run the
# SAME two-stage inference chain (Gemini/Gemma -> NanoGPT/DeepSeek-thinking);
# the alias survives as `llm_judgments.model_name` provenance and as the
# calibration harness's A/B tag (`analytics/judge_calibration.py` rebinds
# it to `"judge_fast"` for A/B rows, then restores it). Defined as a private
# mutable default — calibration is the only intentional mutator.
_JUDGE_MODEL = "judge_quality"


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
        from .writers.connection import _LOCK, _ensure_flockmtl_secret

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


def _run_prompt(
    connection: duckdb.DuckDBPyConnection,
    *,
    model_name: str,
    prompt_name: str,
    context_columns: list[dict[str, object]],
    response_format: dict[str, object] | None = None,
) -> tuple[str | None, float]:
    """Run one FlockMTL judge prompt and return (raw_text_or_none, duration_seconds).

    Two execution paths:

      (a) Schema-mode (default for the 6 production facets): when a
          `response_format` is derived, run the TWO-STAGE INFERENCE CHAIN:
          Stage 1 — Google Gemini API hosting Gemma (`gemma-4-26b-a4b-it`,
          native google-genai SDK, plain-text prompt; Gemma has neither
          reliable OpenAI-compat access nor responseSchema support).
          Stage 2 — NanoGPT serving `deepseek/deepseek-v4-flash-0731:thinking`
          WITH strict response_format=json_schema. Each stage retries
          transient failures (timeouts / 408 / 409 / 425 / 429 / 5xx) with
          exponential backoff before failing over to the next stage. The
          Hugging Face router is retired from judge inference (2026-08-22)
          after monthly-credit depletion caused a silent multi-week outage.

          Structured output is guaranteed on stage 2; stage 1 leans on the
          prompt's `### Output Format` footer plus the 3-tier
          `_parse_result` salvage (same contract as summary_backend's
          Gemma calls).

      (b) FlockMTL `llm_complete` last resort: reached only when BOTH
          stages exhaust. Its registry/secret point at NanoGPT (see
          `writers/connection.py`), so no judge code path contacts
          Hugging Face any more.

    Neither chain stage ships a template engine, so `_render_prompt`
    substitutes each `{{name}}` placeholder in the prompt template with
    the corresponding `context_columns` `data` value before sending
    (byte-equivalent to what FlockMTL would render).
    """
    started = time.perf_counter()
    schema = (
        _FACET_SCHEMAS.get(prompt_name)
        if response_format is None
        else (response_format if "json_schema" in response_format else None)
    )
    # Use per-call response_format if supplied; else fall back to the
    # canonical schema for this prompt.
    effective_rf: dict[str, object] | None = response_format or (
        {
            "type": "json_schema",
            "json_schema": {
                "name": prompt_name,
                "strict": True,
                "schema": schema,
            },
        }
        if schema is not None
        else None
    )

    # Path (a) — two-stage chain: Gemini/Gemma -> NanoGPT/DeepSeek-thinking.
    if effective_rf is not None:
        try:
            return _judge_chain_call(
                model_name=model_name,
                prompt_name=prompt_name,
                context_columns=context_columns,
                response_format=effective_rf,
            )
        except Exception as exc:
            duration = time.perf_counter() - started
            logger.warning(
                "judge chain failed for model=%s prompt=%s: %s; "
                "falling back to FlockMTL llm_complete",
                model_name,
                prompt_name,
                exc,
            )
            # Path (b) fallback — kept short so a total chain outage
            # doesn't poison the row.

    # Path (b) — FlockMTL llm_complete.
    try:
        row = connection.execute(
            "SELECT llm_complete(?, ?)",
            [
                {"model_name": model_name},
                {
                    "prompt_name": prompt_name,
                    "context_columns": context_columns,
                },
            ],
        ).fetchone()
        duration = time.perf_counter() - started
        return (row[0] if row else None, duration)
    except Exception as exc:
        duration = time.perf_counter() - started
        logger.warning(
            "llm_complete failed for model=%s prompt=%s: %s",
            model_name,
            prompt_name,
            exc,
        )
        return (None, duration)


def _is_retryable_stage_error(exc: BaseException) -> bool:
    """True for transient failures worth backing off before a retry.

    Recognises typed statuses when the SDK exposes them (google-genai
    errors carry ``code``, httpx/openai errors carry ``status_code``)
    and falls back to conservative string markers otherwise. Auth and
    quota errors (401/402/403/404) are NOT retried — they fail over to
    the next stage immediately.
    """
    status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in (408, 409, 425, 429) or 500 <= status <= 599
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "timeout",
            "timed out",
            "temporarily unavailable",
            "rate limit",
            "internal error",
            "bad gateway",
            "service unavailable",
            "connection reset",
            "connection aborted",
        )
    )


def _is_rejected_response_format(exc: BaseException) -> bool:
    """True only when the gateway rejected the response_format payload itself.

    A bare 400 for an unrelated reason (bad model id, malformed prompt)
    must propagate so the real error surfaces instead of triggering a
    pointless schema-less retry.
    """
    return getattr(exc, "status_code", None) == 400 and "response_format" in str(exc).lower()


def _stage_backoff_seconds(attempt: int) -> float:
    """Exponential backoff for attempt N (0-based), doubling and capped."""
    initial = max(settings.judge_retry_initial_backoff_seconds, 0.05)
    ceiling = max(settings.judge_retry_max_backoff_seconds, initial)
    return min(initial * (2**attempt), ceiling)


_GEMINI_CLIENT: Any = None
_GEMINI_CLIENT_KEY: str | None = None


def _get_gemini_client() -> Any:
    """Lazily build and cache ONE google-genai Client per API key.

    Mirrors content/summary_backend's shared-client pattern: constructing
    a Client per call wastes setup and churns the underlying HTTP pool.
    Rebuilt automatically if GEMINI_API_KEY changes at runtime.
    """
    global _GEMINI_CLIENT, _GEMINI_CLIENT_KEY
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    if _GEMINI_CLIENT is None or _GEMINI_CLIENT_KEY != settings.gemini_api_key:
        from google import genai  # type: ignore[import-untyped]

        _GEMINI_CLIENT = genai.Client(api_key=settings.gemini_api_key)
        _GEMINI_CLIENT_KEY = settings.gemini_api_key
    return _GEMINI_CLIENT


def _call_gemini_stage(prompt_text: str) -> str:
    """Stage 1: Gemini API hosting Gemma via the native google-genai SDK.

    Plain text in/out on purpose: the OpenAI-compat endpoint is unreliable
    for Gemma models and Gemma supports no responseSchema — structured
    output is recovered downstream by `_parse_result`. Uses the shared
    cached Client (`_get_gemini_client`).
    """
    from google.genai import types as genai_types

    client: Any = _get_gemini_client()
    response = client.models.generate_content(
        model=settings.judge_gemini_model,
        contents=prompt_text,
        config=genai_types.GenerateContentConfig(
            temperature=0.0,
            max_output_tokens=3000,
        ),
    )
    text = (response.text or "").strip()
    if not text:
        candidates = getattr(response, "candidates", None) or []
        finish = getattr(candidates[0], "finish_reason", "?") if candidates else "no-candidates"
        parts = getattr(candidates[0], "content", None) if candidates else None
        parts_dump = [
            {
                "text_len": len(getattr(p, "text", "") or ""),
                "thought": bool(getattr(p, "thought", False)),
            }
            for p in (getattr(parts, "parts", None) or [])
        ][:5]
        raise RuntimeError(
            f"empty gemma completion (finish_reason={finish}, parts={parts_dump}); "
            "likely safety block, thought-only output, or prompt rejected"
        )
    return text


_NANOGPT_STAGE_TIMEOUT_S = 120.0


def _call_nanogpt_stage(
    prompt_name: str,
    prompt_text: str,
    response_format: dict[str, object] | None,
) -> str:
    """Stage 2: NanoGPT (OpenAI-compatible) serving DeepSeek Flash thinking.

    Sends strict response_format=json_schema when provided. If the gateway
    rejects the response_format itself, one immediate retry WITHOUT it lets
    the whole stage.
    """
    from openai import OpenAI

    if not settings.nano_gpt_api_key:
        raise RuntimeError("NANOGPT_API_KEY not set")
    client = OpenAI(
        base_url=settings.judge_nanogpt_base_url,
        api_key=settings.nano_gpt_api_key,
        timeout=_NANOGPT_STAGE_TIMEOUT_S,
        max_retries=0,  # retry policy owned by the chain, not the SDK
    )
    kwargs: dict[str, Any] = {
        "model": settings.judge_nanogpt_model,
        "messages": [{"role": "user", "content": prompt_text}],
        # 8000: :thinking burns completion budget before the JSON verdict.
        # reasoning.exclude (documented NanoGPT extension): suppress the
        # separate reasoning stream so `content` carries the full answer —
        # without it, content can come back as fragments like '","'.
        "max_tokens": 8000,
    }
    try:
        if response_format is not None:
            kwargs["response_format"] = response_format
        completion = client.chat.completions.create(
            **kwargs,
            extra_body={"reasoning": {"exclude": True}},
        )
    except Exception as exc:
        if response_format is None or not _is_rejected_response_format(exc):
            logger.warning("nanogpt stage %s attempt failed: %s", prompt_name, exc)
            raise
        logger.warning(
            "nanogpt rejected response_format for %s (%s); retrying without it",
            prompt_name,
            exc,
        )
        kwargs.pop("response_format", None)
        completion = client.chat.completions.create(
            **kwargs,
            extra_body={"reasoning": {"exclude": True}},
        )
    content = completion.choices[0].message.content or "" if completion.choices else ""
    # :thinking variants sometimes leak snake_case chain-of-thought into
    # content ahead of the answer (reasoning.exclude is not reliably
    # honored on the subscription endpoint); cut to the LAST template
    # anchor so the parser sees 'Feedback: ... [RESULT] {json}' instead
    # of thought soup.
    anchor = content.rfind("Feedback:")
    if anchor > 0:
        content = content[anchor:]
    if _parse_result(content) is None:
        # :thinking output format is nondeterministic on the subscription
        # route (fragments / leaked CoT / bare prose). One cheap
        # self-extraction pass: ask the model to emit ONLY the verdict JSON
        # from its own prior answer; keep raw content as last resort.
        conform_messages = [
            {"role": "user", "content": prompt_text},
            {"role": "assistant", "content": content},
            {
                "role": "user",
                "content": (
                    "Extract the final judge verdict from your answer above and "
                    "return it as a single-line JSON object matching the rubric "
                    "(keys such as verdict/intent_match/informativeness/"
                    "confidence/reasoning). Output ONLY the JSON object — no "
                    "prose, no markdown fences, no array."
                ),
            },
        ]
        try:
            conform = client.chat.completions.create(
                model=settings.judge_nanogpt_model,
                messages=conform_messages,
                temperature=0.0,
                max_tokens=4000,
                extra_body={"reasoning": {"exclude": True}},
            )
            c2 = (conform.choices[0].message.content or "" if conform.choices else "").strip()
            if _parse_result(c2) is not None:
                logger.info("conformance pass recovered a parseable verdict")
                return c2
            logger.warning("conformance pass output still unparseable: %r", c2[:120])
        except Exception as exc:
            logger.warning("conformance pass failed: %s", exc)
    return content.strip()


def _judge_chain_call(
    *,
    model_name: str,
    prompt_name: str,
    context_columns: list[dict[str, object]],
    response_format: dict[str, object],
) -> tuple[str | None, float]:
    """Run one judged prompt through the two-stage chain with backoff.

    Stage order is fixed (Gemini/Gemma first, NanoGPT/DeepSeek second);
    `model_name` (the FlockMTL alias) is carried for logs only — the chain
    is uniform across aliases. Raises RuntimeError listing every attempt
    when all stages exhaust, letting `_run_prompt` fall back to the
    FlockMTL llm_complete last resort.
    """
    started = time.perf_counter()
    prompt_text = _render_prompt(prompt_name, context_columns)
    stages: tuple[tuple[str, Callable[[], str]], ...] = (
        ("gemini/gemma", lambda: _call_gemini_stage(prompt_text)),
        (
            f"nanogpt/{settings.judge_nanogpt_model}",
            lambda: _call_nanogpt_stage(prompt_name, prompt_text, response_format),
        ),
    )
    attempts = 1 + max(settings.judge_stage_max_retries, 0)
    failures: list[str] = []
    for stage_label, invoke in stages:
        for attempt in range(attempts):
            try:
                content = str(invoke())
            except Exception as exc:
                failures.append(f"{stage_label}#{attempt + 1}: {exc}")
                if attempt >= attempts - 1 or not _is_retryable_stage_error(exc):
                    break
                sleep_for = _stage_backoff_seconds(attempt)
                logger.warning(
                    "judge stage %s attempt %d/%d failed (%s); backing off %.1fs",
                    stage_label,
                    attempt + 1,
                    attempts,
                    exc,
                    sleep_for,
                )
                time.sleep(sleep_for)
                continue
            if content:
                duration = time.perf_counter() - started
                return (content, duration)
            # Empty completion = stage-level failure: stop retrying this
            # stage and fail over immediately (a retry against the same
            # stage rarely differs; the next provider is the real remedy).
            failures.append(f"{stage_label}#{attempt + 1}: empty completion")
            break
        logger.warning("judge stage %s exhausted; failing over", stage_label)
    raise RuntimeError("all judge stages failed :: " + " | ".join(failures))


def _render_prompt(prompt_name: str, context_columns: list[dict[str, object]]) -> str:
    """Render the FlockMTL prompt template with context_columns values.

    FlockMTL's template engine substitutes `{{name}}` placeholders with
    the matching `data` field of a context_column. The chain stages have
    no such engine, so we do the substitution here. This is
    byte-equivalent to what FlockMTL would render.
    """
    from .writers.connection import _FLOCKMTL_PROMPTS

    template = dict(_FLOCKMTL_PROMPTS).get(prompt_name, "")
    for col in context_columns:
        name = col.get("name", "")
        data = col.get("data", "")
        template = template.replace("{{" + str(name) + "}}", str(data))
    return template


def _parse_result(raw: str | None) -> dict | None:
    """Parse the model's response into a structured dict. Three tiers:

      1. `json.loads(raw)` — for the strict-schema path. The model
         returns pure JSON like `{"verdict": "good", ...}` with no
         `[RESULT]` token, no prose wrapper. This is the dominant
         path now that `response_format=json_schema strict=true`
         forces schema-conformant output.

      2. `[RESULT] {...}` token split — for the prose fallback (a
         model that ignored the response_format and emitted the
         Prometheus scaffold's verbal "Feedback: ... [RESULT] {...}"
         form).

      3. First `{...}` block in `raw` — last-resort for both paths
         when the model added trailing commentary after the JSON.

    Returns None if no tier succeeds. Callers must treat None as a
    parse failure (store `status='error'`, do NOT crash).
    """
    if not raw:
        return None
    raw = raw.strip()
    # Tier 0: JSON array wrapper — some :thinking models emit a list of
    # feedback objects instead of the bare schema object; judge the first.
    if raw.startswith("["):
        try:
            arr = json.loads(raw)
            if isinstance(arr, list) and arr and isinstance(arr[0], dict):
                return arr[0]
        except Exception:
            pass
    # Tier 1: pure JSON (schema-strict path).
    if raw.startswith("{") and raw.endswith("}"):
        try:
            return json.loads(raw)
        except Exception:
            pass
    # Tier 2: [RESULT] JSON split.
    marker = raw.find("[RESULT]")
    if marker >= 0:
        tail = raw[marker + len("[RESULT]") :].strip()
        try:
            return json.loads(tail)
        except Exception:
            m = re.search(r"\{.*\}", tail, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    pass
    # Tier 3: first {...} block anywhere in raw.
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


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
    build_verdict,
    build_reasoning,
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


_REWRITE_STRATEGIES = (
    "augmented free-web query",
    "SERP1 keyword query",
    "SERP2 keyword query",
    "semantic Tavily query",
    "semantic Exa query",
)


def _ctx(*pairs: tuple[str, object]) -> list[dict[str, object]]:
    """Build FlockMTL `context_columns` entries with required `name` + `data` keys.

    FlockMTL's input parser restricts context_columns keys to
    `name, data, type, detail, transcription_model` (verified against
    the flock source -- see `src/functions/input_parser.cpp`). The
    `name` matches `{{name}}` placeholders in the prompt template;
    `data` carries the value.
    """
    return [{"name": name, "data": data} for name, data in pairs]


def _fetch_branch_errors(
    connection: duckdb.DuckDBPyConnection,
    run_key: str,
) -> list[tuple[str | None, int | None, str | None]]:
    """Fetch per-branch error signals via a JOIN onto provider_calls.

    `search_branches` has NO `error_type` column; the canonical
    per-branch error signal lives on `provider_calls.error_type`,
    keyed by `branch_index`. The SELECT whitelists three columns
    from each table so a future schema addition can't silently leak
    banned fields. Returns list of (branch_role, results_count,
    comma-joined provider error_types or None).
    """
    rows = connection.execute(
        """
        SELECT sb.branch_role, sb.results_count,
               (SELECT string_agg(pc.error_type, '/') FILTER (WHERE pc.error_type IS NOT NULL)
                FROM provider_calls pc
                WHERE pc.run_key = sb.run_key AND pc.branch_index = sb.branch_index
               ) AS provider_err_str
        FROM search_branches sb
        WHERE sb.run_key = ?
        ORDER BY sb.branch_index
        """,
        [run_key],
    ).fetchall()
    return rows


def _build_run_digest(
    connection: duckdb.DuckDBPyConnection,
    run_key: str,
) -> str:
    """Build a compact one-string digest of the entire run for judge_run_overview.

    Includes STRUCTURAL facts only: ranks, titles, links, counts,
    branches, rerank stage names + counts. Does NOT include raw
    reranker scores (`final_score`, `llm_raw_score`, `cross_encoder_raw`,
    `fused_score`, `hybrid_rrf_score`) -- the overview is a summary,
    not a rubber-stamp of the reranker. Empty fields render as empty
    strings; no exceptions raised on missing rows.
    """
    run_row = connection.execute(
        "SELECT query, intent, research_goal, understanding_confidence, "
        "rewritten_branch_queries, selected_providers, branch_count, "
        "merged_count, reranked_count, final_result_count, candidate_count, "
        "rewrite_enabled, error_type, status "
        "FROM search_runs WHERE run_key = ?",
        [run_key],
    ).fetchone()
    if not run_row:
        return ""
    (
        q,
        intent,
        research_goal,
        uc,
        rewritten,
        providers,
        branch_count,
        merged,
        reranked,
        final_count,
        cand_count,
        rewrite_enabled,
        err_type,
        st,
    ) = run_row
    rewrites = [str(s) for s in (rewritten or []) if s] or []
    providers_str = ", ".join(providers) if providers else "none"
    parts: list[str] = []
    parts.append(f"query: {q or ''}")
    parts.append(
        f"intent: {intent or 'unknown'} (conf={uc})  "
        f"goal: {research_goal or ''}  status: {st or '?'}  "
        f"err_type: {err_type or ''}  rewrite_enabled: {bool(rewrite_enabled)}"
    )
    if rewrites:
        parts.append("rewrites:")
        for i, r in enumerate(rewrites[:5]):
            strat = _REWRITE_STRATEGIES[i] if i < len(_REWRITE_STRATEGIES) else "unknown"
            parts.append(f"  {i + 1}. {r}  [{strat}]")
    else:
        parts.append("rewrites: (none -- planner skipped or errored)")

    branches = _fetch_branch_errors(connection, run_key)
    branch_count_actual = branch_count if branch_count is not None else len(branches)
    parts.append(f"branches: {branch_count_actual}  providers: {providers_str}")
    for role, rc, perr in branches:
        line = f"  - {role or 'unknown'}: {rc or 0} results"
        if perr:
            line += f"  provider_errors={perr}"
        parts.append(line)

    parts.append(
        f"candidates: merged={merged or 0}  reranked={reranked or 0}  "
        f"final={final_count or 0}  raw={cand_count or 0}"
    )

    # Reank stage structural summary -- stage name, counts, threshold.
    # No raw scores (the digest is reranker-blind).
    stages = connection.execute(
        "SELECT stage, provider, model, input_count, output_count, status, "
        "error_type, score_threshold "
        "FROM rerank_stages WHERE run_key = ? ORDER BY recorded_at",
        [run_key],
    ).fetchall()
    if stages:
        parts.append("rerank stages:")
        for stage, prov, model, inp, outp, sst, serr, thr in stages:
            line = (
                f"  - {stage or '?'}: in={inp or 0} out={outp or 0} "
                f"thr={thr} st={sst or '?'} prov={prov or '?'} m={model or '?'}"
            )
            if serr:
                line += f" err={serr}"
            parts.append(line)
    else:
        parts.append("rerank stages: (none recorded)")

    # Top N final results -- rank, title, link only (no final_score).
    finals = connection.execute(
        "SELECT rank, title, link FROM final_results WHERE run_key = ? ORDER BY rank LIMIT ?",
        [run_key, _DIGEST_TOP_N_FINAL],
    ).fetchall()
    if finals:
        parts.append(f"final results (top {_DIGEST_TOP_N_FINAL}, rank: title -- link):")
        for rank, title, link in finals:
            parts.append(f"  {rank or '?'}. {title or ''} -- {link or ''}")
    else:
        parts.append("final results: (none)")
    return "\n".join(parts)


def _format_overview_reasoning(parsed: dict) -> str:
    """Format judge_run_overview reasoning: analysis + recommendations block."""
    analysis = str(parsed.get("analysis") or "").strip()
    recs = parsed.get("recommendations") or []
    if not isinstance(recs, list):
        recs = []
    if not analysis and not recs:
        return ""
    lines: list[str] = []
    if analysis:
        lines.append(analysis)
    if recs:
        lines.append("Recommendations:")
        for i, r in enumerate(recs):
            lines.append(f"  {i + 1}. {str(r).strip()}")
    return "\n".join(lines)


def judge_search_run(
    run_key: str,
    *,
    db_path: str | None = None,
) -> int:
    """Run all six facet-decomposed FlockMTL prompts against the given search run.

    Returns the number of judgment rows persisted. Returns 0 when:
      - `settings.flockmtl_enabled` is False
      - FlockMTL install/load fails (community catalog unreachable)
      - the run_key doesn't exist in `search_runs`

    Each facet call's failure is caught and persisted as a
    `status='error'` row so the orchestrator never crashes the calling
    search pipeline. Per-rubric blindness: `judge_run_overview`,
    `judge_rerank_improvement`, and `judge_result_quality` SELECTs are
    explicit whitelists that never read reranker scores, matching the
    blindness statements in their prompt templates.

    Args:
        run_key: The run_key of the search to judge.
        db_path: Optional analytics DuckDB path override (testing).
    """
    if not settings.flockmtl_enabled:
        logger.debug("flockmtl_enabled=false, skipping judge for %s", run_key)
        return 0

    path = Path(_db_path(db_path))
    if not path.exists():
        logger.warning("analytics DB missing at %s, skipping judge", path)
        return 0

    connection = _connect(db_path)
    try:
        if not _ensure_loaded(connection):
            logger.warning("FlockMTL load failed, skipping judge for %s", run_key)
            return 0

        run_row = connection.execute(
            "SELECT query, intent, status, error_type, selected_providers, "
            "final_result_count, rewritten_branch_queries, research_goal, "
            "understanding_confidence, rewrite_enabled "
            "FROM search_runs WHERE run_key = ?",
            [run_key],
        ).fetchone()
        if not run_row:
            logger.warning("run_key=%s not found in search_runs", run_key)
            return 0

        (
            query,
            intent,
            status,
            error_type,
            selected_providers,
            final_count,
            rewritten_queries,
            research_goal,
            understanding_confidence,
            rewrite_enabled,
        ) = run_row
        providers_str = ", ".join(selected_providers) if selected_providers else "none"
        # DuckDB returns VARCHAR[] as a list[Optional[str]] when read via
        # pyarrow=False; normalize to plain list[str] or empty list.
        rewrites: list[str] = [str(q) for q in (rewritten_queries or []) if q]
        judgments_written = 0

        # (a) judge_run_overview -- always fires FIRST (1 call, judge_quality).
        # Firing first means the overview is independent of the five
        # diagnostic facets (no circularity -- it reads raw run data,
        # not their verdicts). Blindness: the digest contains NO raw
        # reranker scores (see _build_run_digest + SELECT whitelist).
        digest = _build_run_digest(connection, run_key)
        overview_ctx = _ctx(("run_digest", digest))
        raw, duration = _run_prompt(
            connection,
            model_name=_JUDGE_MODEL,
            prompt_name="judge_run_overview",
            context_columns=overview_ctx,
        )
        _store_judgment_row(
            connection,
            run_key=run_key,
            judgment_kind="run_overview",
            judgment_target=run_key,
            prompt_name="judge_run_overview",
            model_name=_JUDGE_MODEL,
            raw=raw,
            duration=duration,
            parsed=_parse_result(raw),
            context_columns=overview_ctx,
            build_verdict=lambda p: str(p.get("verdict") or ""),
            build_reasoning=_format_overview_reasoning,
        )
        judgments_written += 1

        # (b) judge_intent_coherence -- always fires (1 call, judge_quality).
        intent_ctx = _ctx(
            ("query", query or ""),
            ("research_goal", research_goal or ""),
            ("intent", intent or "unknown"),
            (
                "understanding_confidence",
                "" if understanding_confidence is None else str(understanding_confidence),
            ),
            ("rewrites", "\n".join(rewrites) if rewrites else ""),
        )
        raw, duration = _run_prompt(
            connection,
            model_name=_JUDGE_MODEL,
            prompt_name="judge_intent_coherence",
            context_columns=intent_ctx,
        )
        _store_judgment_row(
            connection,
            run_key=run_key,
            judgment_kind="intent_coherence",
            judgment_target=run_key,
            prompt_name="judge_intent_coherence",
            model_name=_JUDGE_MODEL,
            raw=raw,
            duration=duration,
            parsed=_parse_result(raw),
            context_columns=intent_ctx,
            build_verdict=lambda p: str(p.get("verdict") or ""),
            build_reasoning=lambda p: str(p.get("reasoning") or ""),
        )
        judgments_written += 1

        # (c) judge_rewrite_coverage -- fires only when rewrite_enabled
        # truthy AND rewrites non-empty (1 call, judge_quality).
        if rewrite_enabled and rewrites:
            variants_lines = []
            for i, variant in enumerate(rewrites[:5]):
                strat = _REWRITE_STRATEGIES[i] if i < len(_REWRITE_STRATEGIES) else "unknown"
                variants_lines.append(f"  {i + 1}. {variant}  [{strat}]")
            coverage_ctx = _ctx(
                ("query", query or ""),
                ("research_goal", research_goal or ""),
                ("variants", "\n".join(variants_lines)),
            )
            raw, duration = _run_prompt(
                connection,
                model_name=_JUDGE_MODEL,
                prompt_name="judge_rewrite_coverage",
                context_columns=coverage_ctx,
            )

            def _coverage_verdict(p: dict) -> str:
                rewrites_slice = rewrites[:5]
                return f"covered={p.get('covered_count', 0)}/{len(rewrites_slice)}; redundant={bool(p.get('redundant'))}"

            _store_judgment_row(
                connection,
                run_key=run_key,
                judgment_kind="rewrite_coverage",
                judgment_target="rewrite:set",
                prompt_name="judge_rewrite_coverage",
                model_name=_JUDGE_MODEL,
                raw=raw,
                duration=duration,
                parsed=_parse_result(raw),
                context_columns=coverage_ctx,
                build_verdict=_coverage_verdict,
                build_reasoning=lambda p: str(p.get("reasoning") or ""),
            )
            judgments_written += 1
        # its own short-lived DuckDB connection (connections are not shared
        # across threads). Cap workers at 4 to avoid unbounded HF QPS.
        stage_rows = connection.execute(
            "SELECT stage FROM rerank_stages WHERE run_key = ? ORDER BY recorded_at",
            [run_key],
        ).fetchall()
        stage_payloads: list[tuple[str, list[tuple]]] = []
        for (stage_name,) in stage_rows:
            cand_rows = connection.execute(
                "SELECT rank_before, rank_after, survived, link "
                "FROM rerank_candidates WHERE run_key = ? AND stage = ? "
                "ORDER BY rank_after LIMIT 10",
                [run_key, stage_name],
            ).fetchall()
            stage_payloads.append((stage_name or "", list(cand_rows)))

        result_rows = connection.execute(
            "SELECT rank, title, link, snippet FROM final_results WHERE run_key = ? ORDER BY rank",
            [run_key],
        ).fetchall()

        # Close the orchestrator connection before parallel workers open their
        # own connections — avoids holding a write lock across the pool wait.
        connection.close()
        connection = None

        facet_jobs: list[_RerankImprovementJob | _ResultQualityJob] = []
        for stage_name, cand_rows in stage_payloads:
            facet_jobs.append(
                _RerankImprovementJob(
                    run_key=run_key,
                    stage_name=stage_name,
                    query=query or "",
                    cand_rows=cand_rows,
                    db_path=db_path,
                )
            )
        for rank, title, link, snippet in result_rows:
            facet_jobs.append(
                _ResultQualityJob(
                    run_key=run_key,
                    query=query or "",
                    research_goal=research_goal or "",
                    intent=intent or "unknown",
                    rank=rank,
                    title=title or "",
                    link=link or "",
                    snippet=snippet or "",
                    db_path=db_path,
                )
            )

        if facet_jobs:
            with _JUDGE_SCHEDULE_LOCK:
                if _JUDGE_LIFECYCLE.state == "shutting_down":
                    logger.debug(
                        "Judge executor is shutting down; skipping parallel facets for %s",
                        run_key,
                    )
                    return 0
            pool = _DaemonThreadPoolExecutor(
                max_workers=min(4, len(facet_jobs)),
                thread_name_prefix="judge-facet",
            )
            try:
                try:
                    futures = [pool.submit(_run_parallel_facet, job) for job in facet_jobs]
                except RuntimeError:
                    # CPython atexit set the global _shutdown flag; fall back to inline
                    # execution so partial judgments are still persisted.
                    logger.debug(
                        "Judge facet pool submit blocked (interpreter shutting down); "
                        "running %d facets inline for %s",
                        len(facet_jobs),
                        run_key,
                    )
                    for job in facet_jobs:
                        try:
                            judgments_written += int(_run_parallel_facet(job) or 0)
                        except Exception:
                            logger.exception("inline judge facet failed for run_key=%s", run_key)
                    return judgments_written
                for fut in as_completed(futures):
                    try:
                        judgments_written += int(fut.result() or 0)
                    except RuntimeError:
                        logger.debug(
                            "Judge facet pool shut down mid-run for %s; "
                            "preserving %d partial judgments",
                            run_key,
                            judgments_written,
                        )
                        return judgments_written
                    except Exception:
                        logger.exception("parallel judge facet failed for run_key=%s", run_key)
            finally:
                try:
                    pool.shutdown(wait=False, cancel_futures=True)
                except RuntimeError:
                    pass
        # Re-open connection for failure_cause (serial, needs branch errors).
        connection = _connect(db_path)
        loaded_ok = _ensure_loaded(connection)
        if not loaded_ok:
            logger.warning(
                "FlockMTL reload failed after parallel facets, skipping failure_cause for %s",
                run_key,
            )

        # (f) judge_failure_cause -- 1 call iff status != 'success' OR
        # final_count == 0 (judge_quality). Branch error signals come
        # from `_fetch_branch_errors` (JOIN onto provider_calls,
        # not search_branches -- the latter has no error_type column).
        if loaded_ok and (status != "success" or (final_count or 0) == 0):
            branch_err_rows = _fetch_branch_errors(connection, run_key)
            branch_lines = []
            for role, rc, perr in branch_err_rows:
                line = f"  - {role or 'unknown'}: {rc or 0} results"
                if perr:
                    line += f"  provider_errors={perr}"
                branch_lines.append(line)
            if not branch_lines:
                branch_lines = ["  (no branch rows)"]
            failure_ctx = _ctx(
                ("query", query or ""),
                ("intent", intent or "unknown"),
                ("error_type", error_type or ""),
                ("providers", providers_str),
                ("branch_errors", "\n".join(branch_lines)),
            )
            raw, duration = _run_prompt(
                connection,
                model_name=_JUDGE_MODEL,
                prompt_name="judge_failure_cause",
                context_columns=failure_ctx,
            )

            def _failure_verdict(p: dict) -> str:
                return f"{p.get('root_cause', 'other')} @ {p.get('stage', '?')}"

            _store_judgment_row(
                connection,
                run_key=run_key,
                judgment_kind="failure_cause",
                judgment_target=run_key,
                prompt_name="judge_failure_cause",
                model_name=_JUDGE_MODEL,
                raw=raw,
                duration=duration,
                parsed=_parse_result(raw),
                context_columns=failure_ctx,
                build_verdict=_failure_verdict,
                build_reasoning=lambda p: str(p.get("reasoning") or ""),
            )
            judgments_written += 1

        logger.info(
            "judge_search_run(%s): wrote %d judgments (status=%s, finals=%s)",
            run_key,
            judgments_written,
            status,
            final_count,
        )
        return judgments_written
    except Exception:
        logger.exception("judge_search_run(%s) failed", run_key)
        return 0
    finally:
        if connection is not None:
            connection.close()


@dataclass(frozen=True, slots=True)
class _RerankImprovementJob:
    run_key: str
    stage_name: str
    query: str
    cand_rows: list[tuple]
    db_path: str | None


@dataclass(frozen=True, slots=True)
class _ResultQualityJob:
    run_key: str
    query: str
    research_goal: str
    intent: str
    rank: object
    title: str
    link: str
    snippet: str
    db_path: str | None


def _run_parallel_facet(job: _RerankImprovementJob | _ResultQualityJob) -> int:
    """Run one independent per-result facet on its own DuckDB connection.

    Returns 1 when a judgment row is written, else 0.
    """
    connection = _connect(job.db_path)
    try:
        if not _ensure_loaded(connection):
            return 0
        if isinstance(job, _RerankImprovementJob):
            before_lines = [f"  {rb}. {link}" for rb, _ra, _s, link in job.cand_rows]
            after_lines = [
                f"  {ra}. {link}  (survived={bool(surv)})" for _rb, ra, surv, link in job.cand_rows
            ]
            rerank_ctx = _ctx(
                ("query", job.query),
                ("stage", job.stage_name),
                ("before", "\n".join(before_lines) if before_lines else "(no rows)"),
                ("after", "\n".join(after_lines) if after_lines else "(no rows)"),
            )
            raw, duration = _run_prompt(
                connection,
                model_name=_JUDGE_MODEL,
                prompt_name="judge_rerank_improvement",
                context_columns=rerank_ctx,
            )
            _store_judgment_row(
                connection,
                run_key=job.run_key,
                judgment_kind="rerank_improvement",
                judgment_target=job.stage_name,
                prompt_name="judge_rerank_improvement",
                model_name=_JUDGE_MODEL,
                raw=raw,
                duration=duration,
                parsed=_parse_result(raw),
                context_columns=rerank_ctx,
                build_verdict=lambda p: str(p.get("verdict") or ""),
                build_reasoning=lambda p: str(p.get("reasoning") or ""),
            )
            return 1

        rq_ctx = _ctx(
            ("query", job.query),
            ("research_goal", job.research_goal),
            ("intent", job.intent),
            ("rank", "" if job.rank is None else str(job.rank)),
            ("title", job.title),
            ("snippet", job.snippet),
        )
        raw, duration = _run_prompt(
            connection,
            model_name=_JUDGE_MODEL,
            prompt_name="judge_result_quality",
            context_columns=rq_ctx,
        )

        def _rq_verdict(p: dict) -> str:
            return (
                f"intent_match={bool(p.get('intent_match'))}; "
                f"informativeness={int(p.get('informativeness') or 0)}"
            )

        _store_judgment_row(
            connection,
            run_key=job.run_key,
            judgment_kind="result_quality",
            judgment_target=job.link,
            prompt_name="judge_result_quality",
            model_name=_JUDGE_MODEL,
            raw=raw,
            duration=duration,
            parsed=_parse_result(raw),
            context_columns=rq_ctx,
            build_verdict=_rq_verdict,
            build_reasoning=lambda p: str(p.get("reasoning") or ""),
        )
        return 1
    except Exception:
        logger.exception("parallel facet %s failed", type(job).__name__)
        return 0
    finally:
        connection.close()


_PENDING_JUDGE_FUTURES: set[Future[int]] = set()
_PENDING_JUDGE_FUTURES_LOCK = Lock()


def drain_judges(timeout_seconds: float = 30.0) -> None:
    """Wait for all pending judge futures to complete before shutdown."""
    from concurrent.futures import wait

    with _PENDING_JUDGE_FUTURES_LOCK:
        futures = list(_PENDING_JUDGE_FUTURES)
    if not futures:
        return
    done, not_done = wait(futures, timeout=timeout_seconds)
    if not_done:
        logger.warning("%d judge tasks timed out during drain", len(not_done))


def schedule_judge_search_run(run_key: str) -> Future[int]:
    """Fire-and-forget judge for `run_key` on the global thread pool.

    Returns the submitted Future so callers (tests, observability) can
    inspect the result. Production callers ignore the return value --
    judge failures are logged but never break the search pipeline.

    The returned Future resolves to the integer count of judgments
    persisted by `judge_search_run` (0 on failure, which itself logs
    the underlying exception).

    Shutdown resilience
    -------------------
    Two guards protect against the CPython 3.12 shutdown race where
    ``_python_exit`` (atexit) sets the module-level ``_shutdown`` flag
    in ``concurrent.futures.thread``, which blocks ALL
    ``ThreadPoolExecutor.submit()`` calls — including on our
    ``_DaemonThreadPoolExecutor`` that deliberately skips
    ``_threads_queues`` registration:

    1. ``_JUDGE_SCHEDULE_LOCK`` serialises the lifecycle-state check
       with ``shutdown_judge_executor`` so our own shutdown path
       never races with submission.

    2. If ``submit()`` raises ``RuntimeError`` (from CPython's
       module-level ``_shutdown`` flag), the judge runs **inline** on
       the calling thread.  This guarantees the FlockMTL verdicts are
       persisted durably to the DuckDB database even when CPython's
       atexit handler has already started shutting down thread pools.
       The inline call opens its own short-lived DuckDB connection and
       writes independently, so there is no conflict with any
       already-closed caller connection.
    """
    with _JUDGE_SCHEDULE_LOCK:
        if _JUDGE_LIFECYCLE.state == "shutting_down":
            logger.debug("Judge executor is shutting down; skipping judge for %s", run_key)
            f: Future[int] = Future()
            f.set_result(0)
            return f
        executor = _get_judge_executor()

    try:
        future = executor.submit(judge_search_run, run_key)
    except RuntimeError:
        # CPython's _python_exit (atexit) set the module-level _shutdown
        # flag in concurrent.futures.thread, which blocks ALL
        # ThreadPoolExecutor.submit() calls.  Fall back to inline
        # execution so the judge still runs and scores are persisted.
        logger.debug(
            "Judge executor submit blocked (interpreter shutting down); "
            "running judge inline for %s",
            run_key,
        )
        try:
            count = judge_search_run(run_key)
        except Exception:
            logger.exception("Inline judge failed for %s", run_key)
            count = 0
        f = Future()
        f.set_result(count)
        return f

    with _PENDING_JUDGE_FUTURES_LOCK:
        _PENDING_JUDGE_FUTURES.add(future)

    def _discard(f: Future[int]) -> None:
        with _PENDING_JUDGE_FUTURES_LOCK:
            _PENDING_JUDGE_FUTURES.discard(f)

    future.add_done_callback(_discard)
    return future


__all__ = [
    "judge_search_run",
    "schedule_judge_search_run",
    "shutdown_judge_executor",
    "drain_judges",
    "_parse_result",
    "_build_run_digest",
    "_store_judgment_row",
    "_BANNED_RERANK_SCORES",
]
