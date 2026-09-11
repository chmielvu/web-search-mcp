#!/usr/bin/env python3
"""Run all analysis stages end-to-end.

Usage:  python run_all.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SCRIPTS = [
    "01_extract.py",
    "02_query_transform.py",
    "03_graph.py",
    "04_quality.py",
    "05_provider_overlap.py",
    "06_intent_drift.py",
]

for s in SCRIPTS:
    print(f"\n=== {s} ===")
    r = subprocess.run([sys.executable, str(ROOT / s)], cwd=str(ROOT))
    if r.returncode != 0:
        print(f"!! {s} exited with {r.returncode}, stopping.")
        sys.exit(r.returncode)

print("\nAll stages complete. See REPORT.md, tables/, figures/.")
