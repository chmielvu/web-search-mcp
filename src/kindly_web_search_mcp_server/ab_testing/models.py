from dataclasses import dataclass, field
from typing import Any


@dataclass
class ABVariant:
    variant_key: str
    weight: int  # must be > 0
    config: dict[str, Any] = field(default_factory=dict)
    description: str = ""


@dataclass
class ABExperiment:
    experiment_id: str
    layer: str  # e.g. "query_understanding", "reranking", "provider_weights"
    status: str = "draft"  # draft, running, paused, concluded
    hypothesis: str = ""
    primary_metric: str = ""
    traffic_pct: float = 10.0  # 0-100
    guardrail_metrics: list[str] = field(default_factory=list)
    started_at: str | None = None
    ended_at: str | None = None
    winning_variant: str | None = None
    variants: list[ABVariant] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> list[str]:
        """Return list of validation errors. Empty = valid."""
        errors = []
        if not self.experiment_id:
            errors.append("experiment_id is required")
        if not self.layer:
            errors.append("layer is required")
        if self.status not in ("draft", "running", "paused", "concluded"):
            errors.append(f"invalid status: {self.status}")
        if not (0 < self.traffic_pct <= 100):
            errors.append(
                f"traffic_pct must be in (0, 100], got {self.traffic_pct}"
            )
        if len(self.variants) < 2:
            errors.append("need at least 2 variants")
        for v in self.variants:
            if v.weight <= 0:
                errors.append(f"variant {v.variant_key} weight must be > 0")
        return errors


@dataclass
class Assignment:
    run_key: str
    experiment_id: str
    variant_key: str
    layer: str
    shadow_mode: bool = False