"""Entity overlap scoring for rerank feature (measured signal).

Returns bounded score in [-1.0, 1.0].
Only blended into rerank score when KINDLY_RERANK_ENTITY_OVERLAP_ENABLED=true.
Weights per label from design: exact match positive contribution, version/repo mismatch negative.
"""

from __future__ import annotations

from typing import Any

from .models import EntitySpan


# Weights (exact match contrib, mismatch penalty)
_LABEL_WEIGHTS: dict[str, tuple[float, float]] = {
    "package": (0.3, -0.5),
    "version": (0.2, -0.4),
    "error_class": (0.25, -0.1),
    "api_function": (0.15, 0.0),
    "repo_ref": (0.25, -0.3),
    "model_id": (0.15, -0.2),
    "cli_flag": (0.1, 0.0),
    "env_var": (0.1, 0.0),
    "file_path": (0.1, -0.1),
}


def _norm(t: str) -> str:
    return (t or "").strip().lower()


def compute_entity_overlap(
    query_entities: list[EntitySpan] | None,
    candidate_entities: list[dict[str, Any]] | None,
) -> float:
    """Compute signed overlap score between query entities and a candidate's entities.

    Positive for shared key entities (package match), negative for conflicting
    (different version of same package, different repo).
    """
    if not query_entities or not candidate_entities:
        return 0.0

    q_by_label: dict[str, set[str]] = {}
    for e in query_entities:
        if e.label:
            q_by_label.setdefault(e.label, set()).add(_norm(e.text))

    c_by_label: dict[str, set[str]] = {}
    for d in candidate_entities or []:
        lab = str(d.get("label") or d.get("entity_type") or "")
        txt = _norm(str(d.get("text") or d.get("entity") or ""))
        if lab and txt:
            c_by_label.setdefault(lab, set()).add(txt)

    score = 0.0
    for lab, qset in q_by_label.items():
        cset = c_by_label.get(lab, set())
        w_exact, w_mis = _LABEL_WEIGHTS.get(lab, (0.1, -0.1))
        inter = qset & cset
        if inter:
            score += w_exact * len(inter) / max(1, len(qset))
        # mismatches within label
        only_q = qset - cset
        only_c = cset - qset
        if only_q and only_c:
            # conflicting values for same label type
            score += w_mis * min(len(only_q), len(only_c)) / max(1, len(qset))
    # bound
    return max(-1.0, min(1.0, score))
