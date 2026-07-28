import logging
import time
from typing import Any, Callable, Coroutine

from ..analytics.duckdb_store import insert_ab_shadow_run

logger = logging.getLogger(__name__)


async def run_shadow(
    run_key: str,
    experiment_id: str,
    variant: str,
    layer: str,
    shadow_fn: Callable[..., Coroutine],
    shadow_kwargs: dict,
    control_duration_ms: float,
    control_result_summary: dict | None = None,
) -> None:
    """Fire-and-forget shadow execution. Runs variant B and records results.

    This function is designed to be called via asyncio.create_task() or
    asyncio.ensure_future(). It never propagates exceptions.

    Parameters:
        run_key: The search run identifier
        experiment_id: The A/B experiment identifier
        variant: Which variant this shadow is testing (e.g., "treatment")
        layer: Which pipeline layer (e.g., "query_understanding")
        shadow_fn: Async callable to execute the shadow variant
        shadow_kwargs: Keyword arguments to pass to shadow_fn
        control_duration_ms: How long the control (production) variant took
        control_result_summary: Optional summary of control result for comparison
    """
    shadow_start = time.monotonic()
    error_type = None

    try:
        normalized_kwargs = dict(shadow_kwargs)
        if "top_k" in normalized_kwargs and "top_n" not in normalized_kwargs:
            normalized_kwargs["top_n"] = normalized_kwargs.pop("top_k")
        shadow_result = await shadow_fn(**normalized_kwargs)
        shadow_duration_ms = (time.monotonic() - shadow_start) * 1000
    except Exception as exc:
        shadow_duration_ms = (time.monotonic() - shadow_start) * 1000
        error_type = "shadow_failed"
        shadow_result = None
        logger.debug("Shadow execution failed for %s/%s: %s", experiment_id, run_key, exc)

    # Record shadow run in DuckDB
    try:
        latency_delta_ms = shadow_duration_ms - control_duration_ms

        insert_ab_shadow_run(
            run_key=run_key,
            experiment_id=experiment_id,
            variant=variant,
            layer=layer,
            duration_ms=round(shadow_duration_ms, 3),
            judge_score=None,  # Judge evaluation happens separately
            tokens_used=None,
            cost_usd=None,
            error_type=error_type,
            payload_json={
                "control_duration_ms": round(control_duration_ms, 3),
                "latency_delta_ms": round(latency_delta_ms, 3),
                "control_summary": control_result_summary,
                "shadow_summary": _safe_summary(shadow_result),
            },
        )
    except Exception as exc:
        logger.debug("Shadow DuckDB insert failed for %s/%s: %s", experiment_id, run_key, exc)


def _safe_summary(obj: Any) -> Any:
    """Create a JSON-safe summary of a result object."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {
            k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v
            for k, v in list(obj.items())[:10]
        }
    if isinstance(obj, list):
        return {"count": len(obj), "first_3": [str(x)[:200] for x in obj[:3]]}
    if hasattr(obj, "__dict__"):
        return {k: str(v)[:200] for k, v in list(obj.__dict__.items())[:10]}
    return str(obj)[:500]
