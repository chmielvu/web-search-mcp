"""LLM stage calling + response parsing for the judge pipeline.

Owns the per-facet JSON schemas (``_FACET_SCHEMAS``), the FlockMTL
``_run_prompt`` entrypoint that wraps the Gemini/Gemma ->
NanoGPT/DeepSeek-thinking chain, the stage-level retry/backoff helpers,
the Gemini client cache, the NanoGPT stage, the two-stage chain
orchestrator (``_judge_chain_call``), the FlockMTL-template renderer
(``_render_prompt``), and the three-tier response parser
(``_parse_result``).
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from typing import Any

import duckdb

from ...settings import settings

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

# Module-level model selector for `judge_search_run`. Both aliases run the
# SAME two-stage inference chain (Gemini/Gemma -> NanoGPT/DeepSeek-thinking);
# the alias survives as `llm_judgments.model_name` provenance. Defined as a
# private mutable default — rebind only for deliberate provenance tagging.
_JUDGE_MODEL = "judge_quality"

_NANOGPT_STAGE_TIMEOUT_S = 120.0


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
          `_parse_result` salvage (same contract as ai_summary's
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
_GEMINI_CLIENT_LOCK = threading.RLock()
_GEMINI_CLIENT_KEY: str | None = None


def _get_gemini_client() -> Any:
    """Lazily build and cache ONE google-genai Client per API key.

    Mirrors content/ai_summary's shared-client pattern: constructing
    a Client per call wastes setup and churns the underlying HTTP pool.
    Rebuilt automatically if GEMINI_API_KEY changes at runtime.
    """
    global _GEMINI_CLIENT, _GEMINI_CLIENT_KEY
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    if _GEMINI_CLIENT is None or settings.gemini_api_key != _GEMINI_CLIENT_KEY:
        with _GEMINI_CLIENT_LOCK:
            if _GEMINI_CLIENT is None or settings.gemini_api_key != _GEMINI_CLIENT_KEY:
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
    from ..writers.connection import _FLOCKMTL_PROMPTS

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
        with contextlib.suppress(Exception):
            arr = json.loads(raw)
            if isinstance(arr, list) and arr and isinstance(arr[0], dict):
                return arr[0]
    # Tier 1: pure JSON (schema-strict path).
    if raw.startswith("{") and raw.endswith("}"):
        with contextlib.suppress(Exception):
            return json.loads(raw)
    # Tier 2: [RESULT] JSON split.
    marker = raw.find("[RESULT]")
    if marker >= 0:
        tail = raw[marker + len("[RESULT]") :].strip()
        try:
            return json.loads(tail)
        except Exception:
            m = re.search(r"\{.*\}", tail, re.DOTALL)
            if m:
                with contextlib.suppress(Exception):
                    return json.loads(m.group(0))
    # Tier 3: first {...} block anywhere in raw.
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        with contextlib.suppress(Exception):
            return json.loads(m.group(0))
    return None
