"""Materialize LLM judge evaluations into offline result_labels foundation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from typing import Any

import duckdb

from ..utils.url_canonicalize import canonicalize_url
from .observability_ids import _canonical_result_id
from .quality_metrics import compute_positional_discount
from .writers.connection import _db_path
from .writers.core import _generate_result_label_id, upsert_materialized_result_labels


@dataclass(frozen=True, slots=True)
class NormalizedResultQuality:
    intent_match: bool
    informativeness: int
    confidence: int
    label: float


@dataclass(frozen=True, slots=True)
class LabelMaterializationReport:
    inspected: int
    accepted: int
    rejected_payload: int
    rejected_target: int
    rejected_position: int
    rejected_timestamp: int
    duplicate_candidates: int
    submitted: int


def parse_result_quality_payload(
    payload_json: str | Mapping[str, Any] | None,
) -> NormalizedResultQuality | None:
    """Parse and validate structured result_quality payload from llm_judgments.

    Reads only `payload_json['parsed']`. Does not reparse compact verdict strings.
    """
    if payload_json is None:
        return None

    parsed_obj: Any = None
    if isinstance(payload_json, str):
        if not payload_json.strip():
            return None
        try:
            raw_dict = json.loads(payload_json)
        except Exception:
            return None
        if isinstance(raw_dict, dict):
            parsed_obj = raw_dict.get("parsed")
    elif isinstance(payload_json, Mapping):
        parsed_obj = payload_json.get("parsed")
    else:
        return None

    if not isinstance(parsed_obj, dict):
        return None

    intent_match = parsed_obj.get("intent_match")
    if not isinstance(intent_match, bool):
        return None

    informativeness = parsed_obj.get("informativeness")
    if isinstance(informativeness, bool) or not isinstance(informativeness, int):
        return None
    if informativeness < 1 or informativeness > 4:
        return None

    confidence = parsed_obj.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, int):
        return None
    if confidence < 1 or confidence > 4:
        return None

    if not intent_match:
        label = 0.0
    else:
        label = (informativeness - 1) / 3.0

    return NormalizedResultQuality(
        intent_match=intent_match,
        informativeness=informativeness,
        confidence=confidence,
        label=label,
    )


def _as_utc_timestamp(value: Any) -> datetime | None:
    """Return a finite epoch timestamp as an aware UTC datetime."""
    try:
        epoch_seconds = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(epoch_seconds):
        return None
    try:
        return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _immutable_judgment_hash(
    *,
    run_key: str,
    target: str,
    model_name: str | None,
    rubric_version: str | None,
    recorded_at_epoch: Any,
    payload_json: Any,
) -> str:
    """Provide a stable same-timestamp tie-breaker without a judgment identifier."""
    immutable_fields = {
        "model_name": model_name or "",
        "payload_json": payload_json,
        "recorded_at_epoch": recorded_at_epoch,
        "rubric_version": rubric_version or "",
        "run_key": run_key,
        "target": target,
    }
    encoded = json.dumps(
        immutable_fields,
        ensure_ascii=True,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _empty_materialization_report() -> LabelMaterializationReport:
    return LabelMaterializationReport(
        inspected=0,
        accepted=0,
        rejected_payload=0,
        rejected_target=0,
        rejected_position=0,
        rejected_timestamp=0,
        duplicate_candidates=0,
        submitted=0,
    )


def materialize_result_labels(
    *,
    db_path: str | None = None,
    source_cutoff: datetime | None = None,
    source_start: datetime | None = None,
    rubric_version: str = "v1",
) -> LabelMaterializationReport:
    """Materialize the latest valid LLM-judge label for each result observation."""
    cutoff = source_cutoff or datetime.now(timezone.utc)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    window_start = source_start
    if window_start is not None and window_start.tzinfo is None:
        window_start = window_start.replace(tzinfo=timezone.utc)
    if window_start is not None and window_start > cutoff:
        raise ValueError("source_start must not be later than source_cutoff")

    path = _db_path(db_path)
    if not path.exists():
        return _empty_materialization_report()

    con = duckdb.connect(str(path), read_only=True)
    try:
        table_names = {
            row[0]
            for row in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }
        if "llm_judgments" not in table_names or "final_results" not in table_names:
            return _empty_materialization_report()

        window_filter = ""
        parameters: list[Any] = [rubric_version, cutoff.timestamp()]
        if window_start is not None:
            window_filter = " AND recorded_at >= to_timestamp(?)"
            parameters.append(window_start.timestamp())
        judgments = con.execute(
            f"""
            SELECT
                run_key,
                judgment_target,
                model_name,
                rubric_version,
                epoch(recorded_at) AS recorded_at_epoch,
                payload_json
            FROM llm_judgments
            WHERE judgment_kind = 'result_quality'
              AND status = 'success'
              AND rubric_version = ?
              AND recorded_at <= to_timestamp(?)
              {window_filter}
            ORDER BY recorded_at ASC
            """,
            parameters,
        ).fetchall()
        if not judgments:
            return _empty_materialization_report()

        run_keys = list({row[0] for row in judgments if row[0]})
        final_results_by_run: dict[str, list[dict[str, Any]]] = {}
        if run_keys:
            placeholders = ", ".join(["?"] * len(run_keys))
            for rk, rank, link, canonical_result_id in con.execute(
                f"""
                SELECT run_key, rank, link, canonical_result_id
                FROM final_results
                WHERE run_key IN ({placeholders})
                """,
                run_keys,
            ).fetchall():
                final_results_by_run.setdefault(rk, []).append(
                    {
                        "rank": rank,
                        "link": link or "",
                        "canonical_result_id": (canonical_result_id or "").strip(),
                    }
                )
    finally:
        con.close()

    inspected = rejected_payload = rejected_target = rejected_position = rejected_timestamp = 0
    duplicate_candidates = 0
    observations: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}

    for rk, target_raw, model_name, judgment_rubric, recorded_at_epoch, raw_payload in judgments:
        inspected += 1
        recorded_at = _as_utc_timestamp(recorded_at_epoch)
        if recorded_at is None:
            rejected_timestamp += 1
            continue

        quality = parse_result_quality_payload(raw_payload)
        if quality is None:
            rejected_payload += 1
            continue

        target = (target_raw or "").strip()
        if not target:
            rejected_target += 1
            continue
        final_candidates = final_results_by_run.get(rk, [])
        exact_matches = [result for result in final_candidates if result["link"] == target]
        if len(exact_matches) == 1:
            matched_result = exact_matches[0]
        elif len(exact_matches) > 1:
            rejected_target += 1
            continue
        else:
            canonical_target = canonicalize_url(target)
            canonical_matches = [
                result
                for result in final_candidates
                if canonicalize_url(result["link"]) == canonical_target
            ]
            if len(canonical_matches) != 1:
                rejected_target += 1
                continue
            matched_result = canonical_matches[0]

        rank = matched_result["rank"]
        if rank is None or isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
            rejected_position += 1
            continue

        position = rank - 1
        raw_url = matched_result["link"] or target
        canonical_result_id = (
            matched_result["canonical_result_id"] or _canonical_result_id(raw_url)
        )
        stage = "final"
        source = "llm_judge"
        annotator_id = (model_name or "").strip() or "judge"
        effective_rubric = (judgment_rubric or "").strip() or rubric_version
        observation_key = (
            rk,
            canonical_result_id,
            stage,
            source,
            effective_rubric,
            annotator_id,
        )
        candidate = {
            "annotator_id": annotator_id,
            "canonical_result_id": canonical_result_id,
            "confidence_fraction": quality.confidence / 4.0,
            "discounted_gain": compute_positional_discount(quality.label, position),
            "label": quality.label,
            "model_name": model_name or "judge",
            "payload_json": {
                "parsed": {
                    "intent_match": quality.intent_match,
                    "informativeness": quality.informativeness,
                    "confidence": quality.confidence,
                },
                "judge_label": quality.label,
                "confidence_fraction": quality.confidence / 4.0,
                "model_name": model_name or "judge",
                "source_judgment_recorded_at": recorded_at.isoformat(),
            },
            "position": position,
            "raw_url": raw_url,
            "recorded_at": recorded_at,
            "rubric_version": effective_rubric,
            "run_key": rk,
            "source": source,
            "stage": stage,
            "tie_breaker": _immutable_judgment_hash(
                run_key=rk,
                target=target,
                model_name=model_name,
                rubric_version=judgment_rubric,
                recorded_at_epoch=recorded_at_epoch,
                payload_json=raw_payload,
            ),
        }
        existing = observations.get(observation_key)
        if existing is not None:
            duplicate_candidates += 1
            if (recorded_at, candidate["tie_breaker"]) <= (
                existing["recorded_at"],
                existing["tie_breaker"],
            ):
                continue
        observations[observation_key] = candidate

    rows_to_upsert: list[dict[str, Any]] = []
    for key in sorted(observations):
        observation = observations[key]
        rows_to_upsert.append(
            {
                "annotator_id": observation["annotator_id"],
                "canonical_result_id": observation["canonical_result_id"],
                "discounted_gain": observation["discounted_gain"],
                "label": observation["label"],
                "label_id": _generate_result_label_id(
                    observation["run_key"],
                    observation["position"],
                    observation["stage"],
                    observation["source"],
                    observation["annotator_id"],
                    observation["rubric_version"],
                    observation["canonical_result_id"],
                ),
                "payload_json": observation["payload_json"],
                "position": observation["position"],
                "raw_url": observation["raw_url"],
                "recorded_at": observation["recorded_at"],
                "rubric_version": observation["rubric_version"],
                "run_key": observation["run_key"],
                "source": observation["source"],
                "stage": observation["stage"],
            }
        )
    if rows_to_upsert:
        upsert_materialized_result_labels(rows_to_upsert, db_path=db_path, sync=True)

    return LabelMaterializationReport(
        inspected=inspected,
        accepted=len(rows_to_upsert),
        rejected_payload=rejected_payload,
        rejected_target=rejected_target,
        rejected_position=rejected_position,
        rejected_timestamp=rejected_timestamp,
        duplicate_candidates=duplicate_candidates,
        submitted=len(rows_to_upsert),
    )
