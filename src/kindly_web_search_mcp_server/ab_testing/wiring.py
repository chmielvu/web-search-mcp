"""A/B testing wiring helper — merges experiment overrides into pipeline kwargs.

This module provides the glue between the A/B experiment definitions (YAML)
and the pipeline stages (query understanding, reranking, etc.). It is
layer-agnostic: any pipeline stage can call ``get_ab_overrides()`` to
retrieve variant configuration for the current ``run_key``.
"""

from __future__ import annotations

import logging
from typing import Any

from ..settings import settings
from .assignment import get_assigned_variant
from .yaml_loader import load_experiments

logger = logging.getLogger(__name__)


def get_ab_overrides(
    *,
    run_key: str,
    layer: str,
) -> dict[str, Any] | None:
    """Return AB variant config if *run_key* is enrolled in a running experiment.

    Parameters
    ----------
    run_key:
        Unique search-run identifier (used for sticky bucketing).
    layer:
        Pipeline layer name, e.g. ``"query_understanding"``, ``"reranking"``.

    Returns
    -------
    ``None`` when
    - AB testing is globally disabled (``settings.ab_testing_enabled`` is
      ``False``),
    - no experiment YAML file exists,
    - no running experiment for *layer*, or
    - the *run_key* falls outside the experiment's ``traffic_pct``.

    Otherwise a dict with keys:

    - ``experiment_id`` – the matched experiment identifier
    - ``variant_key``  – the assigned variant key
    - ``shadow_mode``  – ``True`` when the variant should run as a shadow
      (production uses control config; variant runs in the background)
    - ``config``       – the variant's ``config`` dict (may be empty)
    """
    if not settings.ab_testing_enabled:
        return None

    config_path = settings.ab_config_path if settings.ab_config_path else None
    experiments = load_experiments(config_path)
    if not experiments:
        return None

    assignment = get_assigned_variant(run_key, layer, experiments)
    if assignment is None:
        return None

    # Look up the variant config dict from the matched experiment
    variant_config: dict[str, Any] = {}
    for exp in experiments:
        if exp.experiment_id == assignment.experiment_id:
            for v in exp.variants:
                if v.variant_key == assignment.variant_key:
                    variant_config = v.config
                    break

    shadow_mode = assignment.shadow_mode or variant_config.get("shadow", False)

    return {
        "experiment_id": assignment.experiment_id,
        "variant_key": assignment.variant_key,
        "shadow_mode": bool(shadow_mode),
        "config": variant_config,
    }
