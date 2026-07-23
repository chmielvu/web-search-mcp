import logging
from pathlib import Path

import yaml

from .models import ABExperiment, ABVariant
from ..utils.paths import DEFAULT_EXPERIMENTS_YAML

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(DEFAULT_EXPERIMENTS_YAML)


def load_experiments(
    config_path: Path | str | None = None,
) -> list[ABExperiment]:
    """Load experiments from YAML config file.

    Returns empty list if file doesn't exist.
    Validates each experiment and logs warnings for invalid ones.
    """
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.exists():
        logger.debug("No experiments config at %s", path)
        return []

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    experiments = []
    for raw in data.get("experiments", []):
        try:
            variants = [
                ABVariant(
                    variant_key=v["variant_key"],
                    weight=v.get("weight", 1),
                    config=v.get("config", {}),
                    description=v.get("description", ""),
                )
                for v in raw.get("variants", [])
            ]
            # Surface missing weights at debug (no log spam on every reload)
            missing = [v["variant_key"] for v in raw.get("variants", []) if "weight" not in v]
            if missing:
                logger.debug(
                    "Experiment %s variants missing 'weight' (defaulting to 1): %s",
                    raw.get("experiment_id"),
                    missing,
                )
            exp = ABExperiment(
                experiment_id=raw["experiment_id"],
                layer=raw["layer"],
                status=raw.get("status", "draft"),
                hypothesis=raw.get("hypothesis", ""),
                primary_metric=raw.get("primary_metric", ""),
                traffic_pct=raw.get("traffic_pct", 10.0),
                guardrail_metrics=raw.get("guardrail_metrics", []),
                started_at=raw.get("started_at"),
                ended_at=raw.get("ended_at"),
                winning_variant=raw.get("winning_variant"),
                variants=variants,
                payload=raw.get("payload", {}),
            )
            errors = exp.validate()
            if errors:
                logger.warning("Invalid experiment %s: %s", exp.experiment_id, errors)
                continue
            experiments.append(exp)
        except (KeyError, TypeError) as exc:
            logger.warning("Failed to parse experiment: %s", exc)

    return experiments


def save_experiments(
    experiments: list[ABExperiment],
    config_path: Path | str | None = None,
) -> None:
    """Save experiments back to YAML config file."""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {"experiments": []}
    for exp in experiments:
        raw = {
            "experiment_id": exp.experiment_id,
            "layer": exp.layer,
            "status": exp.status,
            "hypothesis": exp.hypothesis,
            "primary_metric": exp.primary_metric,
            "traffic_pct": exp.traffic_pct,
            "guardrail_metrics": exp.guardrail_metrics,
            "started_at": exp.started_at,
            "ended_at": exp.ended_at,
            "winning_variant": exp.winning_variant,
            "variants": [
                {
                    "variant_key": v.variant_key,
                    "weight": v.weight,
                    "config": v.config,
                    "description": v.description,
                }
                for v in exp.variants
            ],
        }
        if exp.payload:
            raw["payload"] = exp.payload
        data["experiments"].append(raw)

    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
