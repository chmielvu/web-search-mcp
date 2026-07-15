from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from live_web_search_quality_support import EXPORT_TABLES, dump_json, read_jsonl


def _encode_nested(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def _dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(rows)
    for column in frame.columns:
        if frame[column].dtype == "object" and any(
            isinstance(value, (dict, list, tuple)) for value in frame[column] if value is not None
        ):
            frame[column] = frame[column].map(_encode_nested)
    return frame


def export_pandas(run_dir: Path) -> dict[str, Any]:
    sources = {
        "calls": run_dir / "calls.jsonl",
        "progress": run_dir / "progress.jsonl",
        "quality_by_query": run_dir / "quality_by_query.jsonl",
        "process_logs": run_dir / "process_logs.jsonl",
        "manual_review": run_dir / "manual_review.jsonl",
        **{
            table: run_dir / "analytics" / f"{table}.jsonl"
            for table in (*EXPORT_TABLES, "embedding_coverage")
        },
    }
    output_dir = run_dir / "pandas"
    output_dir.mkdir(parents=True, exist_ok=True)
    datasets: dict[str, Any] = {}
    for name, source in sources.items():
        if not source.exists():
            continue
        rows = read_jsonl(source)
        frame = _dataframe(rows)
        target = output_dir / f"{name}.parquet"
        frame.to_parquet(target, engine="pyarrow", compression="zstd", index=False)
        round_trip_rows = len(pd.read_parquet(target, engine="pyarrow"))
        if round_trip_rows != len(frame):
            raise RuntimeError(f"{name}: Parquet round trip returned {round_trip_rows} rows")
        datasets[name] = {
            "source": str(source.relative_to(run_dir)),
            "parquet": str(target.relative_to(run_dir)),
            "rows": len(frame),
            "round_trip_rows": round_trip_rows,
            "columns": list(frame.columns),
            "nested_values": "JSON strings in object columns",
        }
    manifest = {
        "format": "Apache Parquet",
        "writer": f"pandas {pd.__version__} with pyarrow",
        "compression": "zstd",
        "index_written": False,
        "datasets": datasets,
    }
    dump_json(output_dir / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export a live web-search run as pandas Parquet files"
    )
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    manifest = export_pandas(args.run_dir.resolve())
    print(
        json.dumps({"datasets": len(manifest["datasets"]), "output": str(args.run_dir / "pandas")})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
