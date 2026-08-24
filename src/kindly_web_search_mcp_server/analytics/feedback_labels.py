"""Materialize LLM judge evaluations into offline result_labels foundation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any

import duckdb

from ..utils.url_canonicalize import canonicalize_url
from .observability_ids import _canonical_result_id
from .writers.connection import _db_path
from .writers.core import _generate_result_label_id, insert_result_labels


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


def materialize_result_labels(
    *,
    db_path: str | None = None,
    source_cutoff: datetime | None = None,
    rubric_version: str = "v1",
) -> LabelMaterializationReport:
    """Read successful LLM judge results and insert offline result_labels rows."""
    cutoff = source_cutoff or datetime.now(timezone.utc)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)

    path = _db_path(db_path)
    if not path.exists():
        return LabelMaterializationReport(
            inspected=0,
            accepted=0,
            rejected_payload=0,
            rejected_target=0,
            rejected_position=0,
            duplicate_candidates=0,
            submitted=0,
        )

    con = duckdb.connect(str(path), read_only=True)
    try:
        # Verify required tables exist
        table_rows = con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
        table_names = {row[0] for row in table_rows}
        if "llm_judgments" not in table_names or "final_results" not in table_names:
            return LabelMaterializationReport(
                inspected=0,
                accepted=0,
                rejected_payload=0,
                rejected_target=0,
                rejected_position=0,
                duplicate_candidates=0,
                submitted=0,
            )

        judgments = con.execute(
            """
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
            ORDER BY recorded_at ASC
            """,
            [rubric_version, cutoff.timestamp()],
        ).fetchall()

        if not judgments:
            return LabelMaterializationReport(
                inspected=0,
                accepted=0,
                rejected_payload=0,
                rejected_target=0,
                rejected_position=0,
                duplicate_candidates=0,
                submitted=0,
            )

        # Collect unique run_keys to fetch final_results in one query
        run_keys = list({row[0] for row in judgments if row[0]})
        final_results_by_run: dict[str, list[dict[str, Any]]] = {}
        if run_keys:
            placeholders = ", ".join(["?"] * len(run_keys))
            fr_rows = con.execute(
                f"""
                SELECT
                    run_key,
                    rank,
                    link,
                    canonical_result_id
                FROM final_results
                WHERE run_key IN ({placeholders})
                """,
                run_keys,
            ).fetchall()
            for rk, rank, link, cid in fr_rows:
                final_results_by_run.setdefault(rk, []).append(
                    {
                        "rank": rank,
                        "link": link or "",
                        "canonical_result_id": (cid or "").strip(),
                    }
                )
    finally:
        con.close()

    inspected = 0
    accepted = 0
    rejected_payload = 0
    rejected_target = 0
    rejected_position = 0
    duplicate_candidates = 0

    rows_to_insert: list[dict[str, Any]] = []
    seen_label_ids: set[str] = set()

    for rk, target_raw, model_name, j_rubric_ver, recorded_at_epoch, raw_payload in judgments:
        inspected += 1

        quality = parse_result_quality_payload(raw_payload)
        if quality is None:
            rejected_payload += 1
            continue

        target = (target_raw or "").strip()
        if not target:
            rejected_target += 1
            continue

        fr_candidates = final_results_by_run.get(rk, [])
        if not fr_candidates:
            rejected_target += 1
            continue

        # 1. Exact match on link == judgment_target
        matched_fr: dict[str, Any] | None = None
        exact_matches = [fr for fr in fr_candidates if fr["link"] == target]
        if len(exact_matches) == 1:
            matched_fr = exact_matches[0]
        elif len(exact_matches) > 1:
            rejected_target += 1
            continue
        else:
            # 2. Canonicalized URL match only when exactly one matches
            target_canon = canonicalize_url(target)
            canon_matches = [
                fr for fr in fr_candidates if canonicalize_url(fr["link"]) == target_canon
            ]
            if len(canon_matches) == 1:
                matched_fr = canon_matches[0]
            else:
                rejected_target += 1
                continue

        rank = matched_fr.get("rank")
        if rank is None or isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
            rejected_position += 1
            continue

        position = rank - 1
        link = matched_fr.get("link") or target
        stored_cid = matched_fr.get("canonical_result_id")
        canonical_result_id = stored_cid if stored_cid else _canonical_result_id(link)

        stage = "final"
        source = "llm_judge"
        annotator_id = (model_name or "").strip() or "judge"
        eff_rubric_ver = (j_rubric_ver or "").strip() or rubric_version

        label_id = _generate_result_label_id(
            rk,
            position,
            stage,
            source,
            annotator_id,
            eff_rubric_ver,
            canonical_result_id or link,
        )

        if label_id in seen_label_ids:
            duplicate_candidates += 1
            continue

        seen_label_ids.add(label_id)
        accepted += 1

        try:
            recorded_at_dt = datetime.fromtimestamp(float(recorded_at_epoch), tz=timezone.utc)
        except Exception:
            recorded_at_dt = datetime.now(timezone.utc)

        payload_dict = {
            "parsed": {
                "intent_match": quality.intent_match,
                "informativeness": quality.informativeness,
                "confidence": quality.confidence,
            },
            "judge_label": quality.label,
            "confidence_fraction": quality.confidence / 4.0,
            "model_name": model_name or "judge",
            "source_judgment_recorded_at": (recorded_at_dt.isoformat()),
        }

        rows_to_insert.append(
            {
                "label_id": label_id,
                "run_key": rk,
                "position": position,
                "stage": stage,
                "source": source,
                "annotator_id": annotator_id,
                "rubric_version": eff_rubric_ver,
                "recorded_at": recorded_at_dt,
                "label": quality.label,
                "canonical_result_id": canonical_result_id,
                "raw_url": link,
                "payload_json": payload_dict,
            }
        )

    if rows_to_insert:
        insert_result_labels(rows_to_insert, db_path=db_path, sync=True)

    return LabelMaterializationReport(
        inspected=inspected,
        accepted=accepted,
        rejected_payload=rejected_payload,
        rejected_target=rejected_target,
        rejected_position=rejected_position,
        duplicate_candidates=duplicate_candidates,
        submitted=len(rows_to_insert),
    )
