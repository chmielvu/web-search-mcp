#!/usr/bin/env python3
"""Extract query+label from query_understanding.jsonl, remap labels, dedup."""

import json
from pathlib import Path
from collections import Counter

LABEL_MAP = {
    "ai_coding": "ai_coding_and_infrastructure",
    "general": "general",
    "comparison": "comparison",
    "digital_humanities": "digital_humanities",
}

INPUT = Path("duckdb_data/training/query_understanding.jsonl")
OUTPUT = Path("training/data/remapped.jsonl")
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

seen = set()
records = []
raw_count = 0

with INPUT.open() as fin:
    for line in fin:
        if not line.strip():
            continue
        raw_count += 1
        rec = json.loads(line)
        raw_intent = rec.get("intent", "general")
        label = LABEL_MAP.get(raw_intent, raw_intent)
        query = (rec.get("query") or rec.get("normalized_query", "")).strip()
        if not query:
            continue
        key = (query.casefold(), label)
        if key in seen:
            continue
        seen.add(key)
        records.append({"text": query, "label": label})

with OUTPUT.open("w") as fout:
    for rec in records:
        fout.write(json.dumps(rec, ensure_ascii=False) + "\n")

print(f"Raw: {raw_count} → Deduped: {len(records)} → {OUTPUT}")
print("Distribution:")
for k, v in Counter(r["label"] for r in records).most_common():
    print(f"  {k}: {v}")
