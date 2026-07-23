"""LLM-backed query understanding."""

from __future__ import annotations

import logging
import time as time_module

from ...settings import settings
from ...utils.background_tasks import fire_and_forget
from ...llm.phoenix_tracing import LLMTraceContext
from ...llm.worker import build_llm_worker
from ...llm.structured import StructuredLLMRequest
from ...prompts.builders import REASONING_EFFORT_LOW
from ...prompts.registry import build_prompt
from ...training.session_state import get_session_state_store
from ...training.query_understanding_jsonl import append_query_understanding_record
from ...utils.observability import emit_observability_event
from ...ab_testing.wiring import get_ab_overrides
from ...ab_testing.shadow_runner import run_shadow
from ..intents import SearchIntent, normalize_intent
from ..normalize import normalize_query
from .models import QueryUnderstandingResult

logger = logging.getLogger(__name__)


def _build_ab_router(ab_overrides: dict) -> object:
    """Build a custom LLM router from experiment variant config.

    The variant config may contain ``model`` and/or ``timeout_seconds``
    keys to override the default classifier endpoint.
    """
    from ...llm.models import LLMEndpoint
    from ...llm.router import LLMRouter
    from ...llm.config import build_vercel_gpt_oss_endpoint

    cfg = ab_overrides["config"]
    model = cfg.get("model", settings.query_understanding_model)
    timeout = float(cfg.get("timeout_seconds", 20.0))

    # Normalise model name
    model_str: str = f"groq/{model.removeprefix('groq/')}"

    endpoint = LLMEndpoint(
        name="groq",
        model=model_str,
        base_url=settings.groq_base_url,
        api_key=settings.groq_api_key,
        timeout_seconds=timeout,
    )
    return LLMRouter((endpoint, build_vercel_gpt_oss_endpoint(timeout_seconds=timeout)))


async def resolve_query_understanding(
    *,
    query: str,
    research_goal: str | None,
    intent_hint: SearchIntent | None = None,
    session_id: str | None = None,
    run_key: str | None = None,
) -> QueryUnderstandingResult:
    normalized_query = normalize_query(query)

    # ------------------------------------------------------------------
    # Primary path: ONNX intent classifier (fast, ~5ms)
    # ------------------------------------------------------------------
    from .onnx_classifier import classify_intent

    onnx_result = await classify_intent(normalized_query)
    if onnx_result is not None:
        label, confidence = onnx_result
        # Even at moderate confidence we trust the classifier — it's
        # the primary path. The LLM fallback only triggers if the
        # classifier service is down (onnx_result is None).
        understanding = QueryUnderstandingResult(
            intent=normalize_intent(label),
            confidence=confidence,
            rationale="onnx-classifier",
            should_decompose=False,
        )

        # Emit observability + write JSONL + analytics (same as LLM path)
        emit_observability_event(
            logger,
            "search.query_understanding.resolved",
            query=normalized_query,
            intent=understanding.intent,
            confidence=understanding.confidence,
            should_decompose=False,
            model="tinybert-4l-onnx-int8",
            model_used="tinybert-4l-onnx-int8",
            provider="onnx",
            fallback=False,
        )
        if settings.query_understanding_jsonl_enabled:
            try:
                await append_query_understanding_record(
                    raw_query=query,
                    normalized_query=normalized_query,
                    research_goal=research_goal,
                    understanding=understanding,
                    model_name="tinybert-4l-onnx-int8",
                    prompt_name="onnx-classifier",
                    path=settings.query_understanding_jsonl_path,
                    session_id=session_id,
                )
                if session_id:
                    get_session_state_store().get(session_id).last_intent = understanding.intent
            except Exception as exc:
                logger.warning("query understanding JSONL write failed: %s", exc)

        return understanding

    # ------------------------------------------------------------------
    # Fallback path: LLM-backed query understanding (slow, ~1-10s)
    # Only reached if the ONNX classifier service is unavailable.
    # ------------------------------------------------------------------
    logger.warning("ONNX classifier unavailable, falling back to LLM query understanding")

    system_prompt, user_prompt = build_prompt(
        "query_understanding",
        query=normalized_query,
        research_goal=research_goal,
        intent=intent_hint,
        provider_name="groq",
    )

    # ------------------------------------------------------------------
    # A/B experiment override: check if this run_key is enrolled
    # ------------------------------------------------------------------
    ab_overrides = (
        get_ab_overrides(run_key=run_key, layer="query_understanding") if run_key else None
    )
    timeout_seconds = settings.query_classifier_timeout_seconds
    use_ab_router = False
    if ab_overrides:
        cfg = ab_overrides["config"]
        shadow_mode = ab_overrides["shadow_mode"]
        if "timeout_seconds" in cfg:
            timeout_seconds = float(cfg["timeout_seconds"])
        # Build router with different endpoint config if model differs
        use_ab_router = bool("model" in cfg or "timeout_seconds" in cfg)
    else:
        shadow_mode = False

    fallback_reason = "Query classifier unavailable; defaulting to general."
    result_model_name = "fallback-general"
    result_provider_name = "fallback"
    result_input_tokens: int | None = None
    result_output_tokens: int | None = None
    fallback_used = False
    control_start = time_module.monotonic()
    langfuse_trace = LLMTraceContext(
        trace_name="query_understanding",
        session_id=session_id or run_key,
        metadata={
            "task": "query_understanding",
            "run_key": run_key,
            "intent_hint": intent_hint or "",
            "research_goal": research_goal or "",
        },
    )

    try:
        if use_ab_router:
            # A/B variant: use a custom router with overridden model/timeout
            router = _build_ab_router(ab_overrides)  # type: ignore[arg-type]
            generation = await router.complete_json(  # type: ignore[attr-defined]
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                timeout_seconds=timeout_seconds,
                response_model=QueryUnderstandingResult,
                reasoning_effort=REASONING_EFFORT_LOW,
                langfuse=langfuse_trace,
            )
            result_model_name = generation.endpoint.model
            result_provider_name = generation.endpoint.name
            result_input_tokens = generation.input_tokens
            result_output_tokens = generation.output_tokens
            content = generation.content
        else:
            # Production path: use the standard LLMWorker
            result = await build_llm_worker().complete_structured(
                StructuredLLMRequest(
                    task="query_understand",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.0,
                    timeout_seconds=timeout_seconds,
                    response_model=QueryUnderstandingResult,
                    reasoning_effort=REASONING_EFFORT_LOW,
                    langfuse=langfuse_trace,
                )
            )
            result_model_name = result.model_name
            result_provider_name = result.endpoint_name
            result_input_tokens = result.input_tokens
            result_output_tokens = result.output_tokens
            content = result.content

        control_duration_ms = (time_module.monotonic() - control_start) * 1000
        understanding = QueryUnderstandingResult.model_validate_json(content)
        understanding = understanding.model_copy(
            update=dict(intent=normalize_intent(understanding.intent))
        )
    except Exception as exc:
        control_duration_ms = (time_module.monotonic() - control_start) * 1000
        logger.warning("query understanding failed; falling back to general: %s", exc)
        fallback_used = True
        emit_observability_event(
            logger,
            "search.query_understanding.fallback",
            query=normalized_query,
            error=str(exc)[:300],
            fallback_intent="general",
            fallback_reason=fallback_reason,
        )
        understanding = QueryUnderstandingResult(
            intent="general",
            confidence=0.0,
            rationale=fallback_reason,
            should_decompose=False,
        )

    # ------------------------------------------------------------------
    # Shadow mode: fire-and-forget the variant in the background
    # ------------------------------------------------------------------
    if shadow_mode and ab_overrides and run_key and not fallback_used:
        shadow_cfg = ab_overrides["config"]
        shadow_router = _build_ab_router(ab_overrides)
        shadow_timeout = float(
            shadow_cfg.get("timeout_seconds", settings.query_classifier_timeout_seconds)
        )

        async def _shadow_fn() -> QueryUnderstandingResult:
            shadow_gen = await shadow_router.complete_json(  # type: ignore[attr-defined]
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                timeout_seconds=shadow_timeout,
                response_model=QueryUnderstandingResult,
                reasoning_effort=REASONING_EFFORT_LOW,
                langfuse=langfuse_trace,
            )
            return QueryUnderstandingResult.model_validate_json(shadow_gen.content)

        fire_and_forget(
            run_shadow(
                run_key=run_key,
                experiment_id=ab_overrides["experiment_id"],
                variant=ab_overrides["variant_key"],
                layer="query_understanding",
                shadow_fn=_shadow_fn,
                shadow_kwargs={},
                control_duration_ms=control_duration_ms,
                control_result_summary={
                    "intent": understanding.intent,
                    "confidence": understanding.confidence,
                    "model": result_model_name,
                },
            ),
            name=f"shadow-qu-{run_key[:8]}",
        )
    emit_observability_event(
        logger,
        "search.query_understanding.resolved",
        query=normalized_query,
        intent=understanding.intent,
        confidence=understanding.confidence,
        should_decompose=understanding.should_decompose,
        model=result_model_name,
        model_used=result_model_name,
        provider=result_provider_name,
        input_tokens=result_input_tokens,
        output_tokens=result_output_tokens,
        entities=[entity.model_dump() for entity in understanding.entities],
        preserved_terms=understanding.preserved_terms,
        fallback=fallback_used,
    )
    if settings.query_understanding_jsonl_enabled:
        try:
            await append_query_understanding_record(
                raw_query=query,
                normalized_query=normalized_query,
                research_goal=research_goal,
                understanding=understanding,
                model_name=result_model_name,
                prompt_name="query_understanding",
                path=settings.query_understanding_jsonl_path,
                session_id=session_id,
            )
            if session_id:
                get_session_state_store().get(session_id).last_intent = understanding.intent
        except Exception as exc:
            logger.warning("query understanding JSONL write failed: %s", exc)

    return understanding
