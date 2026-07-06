import hashlib
import logging
from typing import Optional

from .models import ABExperiment, Assignment

logger = logging.getLogger(__name__)


def _hash_to_bucket(run_key: str, experiment_id: str, buckets: int = 10000) -> int:
    """Deterministic hash-based bucket assignment."""
    digest = hashlib.sha256(f"{experiment_id}:{run_key}".encode()).hexdigest()
    return int(digest, 16) % buckets


def get_assigned_variant(
    run_key: str,
    layer: str,
    experiments: list[ABExperiment],
) -> Optional[Assignment]:
    """Sticky assignment. Returns None if no running experiment or not enrolled.

    Only one running experiment per layer at a time (mutual exclusion).
    Traffic_pct controls what fraction of run_keys are enrolled.
    Within enrolled run_keys, variant weights determine allocation.
    """
    running = [e for e in experiments if e.status == "running" and e.layer == layer]
    if not running:
        return None

    # Only one running experiment per layer
    experiment = max(running, key=lambda e: e.started_at or "")

    # Traffic enrollment
    bucket = _hash_to_bucket(run_key, experiment.experiment_id)
    if bucket >= int(experiment.traffic_pct * 100):
        return None

    # Variant selection based on weights
    total_weight = sum(v.weight for v in experiment.variants)
    variant_bucket = bucket % total_weight
    cumulative = 0
    selected = experiment.variants[0]
    for v in experiment.variants:
        cumulative += v.weight
        if variant_bucket < cumulative:
            selected = v
            break

    return Assignment(
        run_key=run_key,
        experiment_id=experiment.experiment_id,
        variant_key=selected.variant_key,
        layer=layer,
    )
