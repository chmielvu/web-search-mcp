"""Deterministic I/O, fixture validation, and ranking metrics for rerank tuning."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlsplit

BORDERLINE_MIN_ROWS = 30
BORDERLINE_MAX_ROWS = 40
CANDIDATE_KEYS = ("title", "snippet", "url", "domain", "providers", "provider_count")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def records_checksum(records: Sequence[dict[str, Any]]) -> str:
    payload = "\n".join(canonical_json(record) for record in records).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{number} is not a JSON object")
        records.append(value)
    return records


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [canonical_json(record) for record in records]
    path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_borderline_fixture(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not BORDERLINE_MIN_ROWS <= len(records) <= BORDERLINE_MAX_ROWS:
        raise ValueError("borderline fixture must contain 30-40 rows")
    ids: set[str] = set()
    queries: set[str] = set()
    groups: Counter[str] = Counter()
    required = {"id", "intent", "template_group", "query", "research_goal", "candidate", "label"}
    for position, record in enumerate(records, 1):
        missing = required - record.keys()
        if missing:
            raise ValueError(f"fixture row {position} is missing {sorted(missing)}")
        row_id = str(record["id"]).strip()
        query = str(record["query"]).strip()
        goal = str(record["research_goal"]).strip()
        group = str(record["template_group"]).strip()
        if not row_id or not query or not goal or not group or record["label"] != "borderline":
            raise ValueError(f"fixture row {position} has an invalid boundary field")
        if row_id in ids or query in queries:
            raise ValueError("borderline fixture IDs and queries must be unique")
        ids.add(row_id)
        queries.add(query)
        groups[group] += 1
        candidate = record["candidate"]
        if not isinstance(candidate, dict) or tuple(candidate) != CANDIDATE_KEYS:
            raise ValueError(f"fixture row {position} candidate keys are not ordered as required")
        if not str(candidate["title"]).strip() or not str(candidate["url"]).strip():
            raise ValueError(f"fixture row {position} candidate lacks title or URL")
        if not isinstance(candidate["providers"], list) or not isinstance(
            candidate["provider_count"], int
        ):
            raise ValueError(f"fixture row {position} candidate provenance is invalid")
    if any(count < 3 for count in groups.values()):
        raise ValueError("each represented template group must have at least three rows")
    return {
        "row_count": len(records),
        "template_group_allocation": dict(sorted(groups.items())),
        "fixture_checksum": records_checksum(records),
    }


def rrf_ids(rankings: Sequence[Sequence[str]], k: int) -> tuple[list[str], dict[str, float]]:
    if k < 1:
        raise ValueError("k must be at least 1")
    scores: dict[str, float] = {}
    encounter: dict[str, int] = {}
    next_encounter = 0
    for ranking in rankings:
        seen: set[str] = set()
        for rank, item_id in enumerate(ranking, 1):
            if item_id in seen:
                continue
            seen.add(item_id)
            if item_id not in encounter:
                encounter[item_id] = next_encounter
                next_encounter += 1
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    order = sorted(scores, key=lambda item_id: (-scores[item_id], encounter[item_id]))
    return order, scores


def hybrid_rrf(record: dict[str, Any], k: int) -> tuple[list[str], dict[str, float]]:
    consensus, _ = rrf_ids(record["provider_rankings"], k)
    if "bm25_scores" in record:
        scores = record["bm25_scores"]
        indexed = list(enumerate(consensus))
        indexed.sort(
            key=lambda pair: (
                -(scores.get(pair[1], 0.0) if scores.get(pair[1], 0.0) > 0 else 0.0),
                pair[0],
            )
        )
        bm25 = [item for _, item in indexed]
    else:
        bm25 = [item for item in record["bm25_order"] if item in set(consensus)]
    return rrf_ids([consensus, bm25], k)


def hybrid_rrf_order(record: dict[str, Any], k: int) -> list[str]:
    order, _ = hybrid_rrf(record, k)
    return order


def jaccard_at(left: Sequence[str], right: Sequence[str], cutoff: int) -> float:
    a, b = set(left[:cutoff]), set(right[:cutoff])
    return len(a & b) / len(a | b) if a or b else 1.0


def rbo(left: Sequence[str], right: Sequence[str], *, persistence: float = 0.9) -> float:
    depth = max(len(left), len(right))
    if depth == 0:
        return 1.0
    score = 0.0
    left_seen: set[str] = set()
    right_seen: set[str] = set()
    for index in range(depth):
        if index < len(left):
            left_seen.add(left[index])
        if index < len(right):
            right_seen.add(right[index])
        overlap = len(left_seen & right_seen) / (index + 1)
        score += (1.0 - persistence) * persistence**index * overlap
    return score + persistence**depth * len(left_seen & right_seen) / depth


def intent_stratified_subset(records: Sequence[dict[str, Any]], size: int) -> list[dict[str, Any]]:
    if size < 1:
        raise ValueError("subset size must be positive")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record.get("intent", "general")), []).append(record)
    selected: list[dict[str, Any]] = []
    depth = 0
    while len(selected) < min(size, len(records)):
        added = False
        for intent in sorted(grouped):
            group = grouped[intent]
            if depth < len(group):
                selected.append(group[depth])
                added = True
                if len(selected) == min(size, len(records)):
                    break
        if not added:
            break
        depth += 1
    return selected


def ndcg_from_reference(selected: Sequence[int], *, cutoff: int = 15) -> float:
    gains = [31 - index for index in selected[:cutoff]]
    dcg = sum(gain / math.log2(rank + 2) for rank, gain in enumerate(gains))
    ideal = sum((31 - rank) / math.log2(rank + 2) for rank in range(min(cutoff, 30)))
    return dcg / ideal if ideal else 1.0


def normalized_host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")
