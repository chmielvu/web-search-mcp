#!/usr/bin/env python3
"""Merge remapped + synthetic data, dedup, stratified 80/20 split."""

import json
from pathlib import Path
from collections import Counter
from sklearn.model_selection import train_test_split

DATA_DIR = Path("training/data")

records = []
for fname in ["remapped.jsonl", "synthetic.jsonl"]:
    path = DATA_DIR / fname
    if not path.exists():
        print(f"WARNING: {path} not found, skipping")
        continue
    with path.open() as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

# Global dedup
seen = set()
unique = []
for rec in records:
    key = (rec["text"].casefold(), rec["label"].casefold())
    if key not in seen:
        seen.add(key)
        unique.append(rec)

labels = [r["label"] for r in unique]
print(f"Total: {len(records)} → Deduped: {len(unique)}")
print("Distribution:")
for k, v in Counter(labels).most_common():
    print(f"  {k}: {v}")

# Stratified split — need at least 4 samples per class
min_count = min(Counter(labels).values())
stratify = labels if min_count >= 4 else None

train, val = train_test_split(
    unique,
    test_size=0.2,
    stratify=stratify,
    random_state=42,
)

for name, split in [("train.jsonl", train), ("val.jsonl", val)]:
    out = DATA_DIR / name
    with out.open("w") as f:
        for rec in split:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    dist = Counter(r["label"] for r in split)
    print(f"\n{name}: {len(split)} records")
    for k, v in dist.most_common():
        print(f"  {k}: {v}")
