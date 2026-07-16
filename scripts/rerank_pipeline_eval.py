"""CLI for measured cross calibration and frozen rerank pipeline replay."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path


from rerank_eval_calibration import calibrate_cross_thresholds
from rerank_eval_capture import capture_fusion_inputs, file_checksum, materialize_diversity_windows
from rerank_eval_common import load_jsonl, write_json, write_jsonl
from rerank_eval_diversity import judge_diversity_winner, tune_diversity
from rerank_eval_fusion import attach_rrf_judgments, tune_rrf

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_CORPUS = ROOT / "scripts" / "live_web_search_quality_queries.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    calibrate = subparsers.add_parser("calibrate-cross")
    calibrate.add_argument("--fixture", type=Path, required=True)
    calibrate.add_argument("--output-dir", type=Path, required=True)

    capture = subparsers.add_parser("capture")
    capture.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--max-in-flight", type=int, default=5)

    rrf = subparsers.add_parser("tune-rrf")
    rrf.add_argument("--input", type=Path, required=True)
    rrf.add_argument("--output-dir", type=Path, required=True)
    rrf.add_argument("--judge-subset-size", type=int, default=10)
    rrf.add_argument("--skip-judge", action="store_true")

    materialize = subparsers.add_parser("materialize-diversity")
    materialize.add_argument("--input", type=Path, required=True)
    materialize.add_argument("--rrf-decision", type=Path, required=True)
    materialize.add_argument("--cross-thresholds", type=Path, required=True)
    materialize.add_argument("--output", type=Path, required=True)

    diversity = subparsers.add_parser("tune-diversity")
    diversity.add_argument("--input", type=Path, required=True)
    diversity.add_argument("--output-dir", type=Path, required=True)
    diversity.add_argument("--judge-subset-size", type=int, default=10)
    diversity.add_argument("--skip-judge", action="store_true")
    return parser


async def _run(args: argparse.Namespace) -> None:
    if args.command == "calibrate-cross":
        artifact = await calibrate_cross_thresholds(load_jsonl(args.fixture))
        write_json(args.output_dir / "cross-thresholds.json", artifact)
        return
    if args.command == "capture":
        corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
        records = await capture_fusion_inputs(corpus, max_in_flight=args.max_in_flight)
        write_jsonl(args.output, records)
        return
    if args.command == "tune-rrf":
        records = load_jsonl(args.input)
        judged = (
            records
            if args.skip_judge
            else await attach_rrf_judgments(records, subset_size=args.judge_subset_size)
        )
        artifact = tune_rrf(judged)
        artifact["judge_subset_size"] = 0 if args.skip_judge else args.judge_subset_size
        write_json(args.output_dir / "rrf-decision.json", artifact)
        return
    if args.command == "materialize-diversity":
        records = load_jsonl(args.input)
        rrf_artifact = json.loads(args.rrf_decision.read_text(encoding="utf-8"))
        threshold_artifact = json.loads(args.cross_thresholds.read_text(encoding="utf-8"))
        selected_k = int(rrf_artifact["decision"]["selected_k"])
        windows = await materialize_diversity_windows(
            records,
            selected_k=selected_k,
            thresholds=threshold_artifact.get("thresholds", {}),
            cross_threshold_checksum=file_checksum(args.cross_thresholds),
        )
        write_jsonl(args.output, windows)
        return
    if args.command == "tune-diversity":
        records = load_jsonl(args.input)
        initial = tune_diversity(records)
        winner = initial["decision"]["winner"]
        comparison = None
        if winner is not None and not args.skip_judge:
            comparison = await judge_diversity_winner(
                records,
                winner=winner,
                subset_size=args.judge_subset_size,
            )
        artifact = tune_diversity(records, judge_comparison=comparison)
        artifact["judge_subset_size"] = 0 if args.skip_judge else args.judge_subset_size
        write_json(args.output_dir / "diversity-decision.json", artifact)
        return
    raise AssertionError(f"Unhandled command: {args.command}")


def main() -> None:
    asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    main()
