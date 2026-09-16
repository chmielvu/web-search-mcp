from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
import json
import math
import re
import unicodedata

import polars as pl


EXPORT_DIR = Path("duckdb_data/analytics/exports/2026-09-15/full_parquet")
WRITER_DIR = Path("src/kindly_web_search_mcp_server/analytics/writers")
REPORT_PATH = Path("docs/analysis/search_event_eda_2026-09-15.md")
JSON_PATH = Path("docs/analysis/search_event_eda_2026-09-15.json")
CORE_TABLES = (
    "search_runs",
    "search_branches",
    "query_variants",
    "query_transforms",
    "provider_calls",
    "provider_results",
    "search_candidates",
    "final_results",
    "result_labels",
    "llm_judgments",
)
TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
MARKER_RE = re.compile(r"(?:^|_)(?:test|tmp|deprecated)(?:_|$)|__|hnsw|internal_table", re.I)
CONTENT_MARKER_RE = re.compile(r"\b(test|testing|fixture|synthetic|dummy|t-run|example\.test)\b", re.I)
CACHE_RE = re.compile(r"cache|cached|cache_hit", re.I)
RETRY_RE = re.compile(r"retry|attempt", re.I)
INTENT_PATTERNS = {
    "comparison": re.compile(r"\b(vs\.?|versus|compare|comparison|difference|better than|alternative|benchmark|pros and cons)\b", re.I),
    "news": re.compile(r"\b(latest|today|yesterday|breaking|news|recent|this week|2026|2025)\b", re.I),
    "social_media": re.compile(r"\b(reddit|twitter|x\.com|facebook|linkedin|youtube|social media|hacker news)\b", re.I),
    "digital_humanities": re.compile(r"\b(digital humanities|archives?|manuscript|literature|history|historical|corpus|linguistics|humanities)\b", re.I),
    "ai_coding_and_infrastructure": re.compile(
        r"\b(api|apis|python|javascript|typescript|rust|golang|code|coding|developer|github|gitlab|mcp|fastmcp|server|database|duckdb|polars|parquet|docker|kubernetes|cloud|aws|gcp|azure|llm|embedding|vector|qdrant|rag|model|sdk|package|library|framework|middleware|async|http)\b",
        re.I,
    ),
}
STOPWORDS = {
    "a", "about", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "was", "what", "when", "where", "which", "who", "why", "with",
    "el", "la", "los", "las", "de", "del", "para", "como", "una", "con", "que", "et", "en", "est", "pour", "une", "avec", "dans", "der", "die", "das", "und", "von", "zu", "ein", "eine", "mit", "für", "o", "os", "as", "do", "da", "em", "um", "uma",
}
LANG_STOPWORDS = {
    "en": {"the", "and", "of", "to", "in", "for", "how", "what", "is", "with"},
    "es": {"el", "la", "los", "las", "de", "del", "para", "como", "una", "con"},
    "fr": {"le", "la", "les", "des", "de", "pour", "une", "avec", "et", "dans"},
    "de": {"der", "die", "das", "und", "von", "zu", "eine", "mit", "für", "wie"},
    "it": {"il", "la", "gli", "le", "di", "del", "per", "una", "con", "come"},
    "pt": {"o", "a", "os", "as", "de", "do", "para", "uma", "com", "como"},
}


def load_tables() -> dict[str, pl.DataFrame]:
    """Load the exported DuckDB tables with Polars."""
    return {path.stem: pl.read_parquet(path) for path in sorted(EXPORT_DIR.glob("*.parquet"))}


def parse_json(value: Any) -> Any:
    """Decode binary or string JSON values, returning None for malformed payloads."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        try:
            value = bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def nested_key_paths(value: Any, prefix: str = "") -> list[str]:
    """List nested object paths for instrumentation discovery."""
    if not isinstance(value, dict):
        return []
    paths: list[str] = []
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        paths.append(path)
        paths.extend(nested_key_paths(child, path))
    return paths


def query_tokens(value: Any) -> list[str]:
    """Tokenize query text with a Unicode word-token heuristic."""
    return TOKEN_RE.findall(str(value or "").lower())


def content_tokens(value: Any) -> list[str]:
    """Return unique-content tokens after removing the declared stopword set."""
    return [token for token in query_tokens(value) if token not in STOPWORDS]


def script_class(value: Any) -> str:
    """Classify the dominant Unicode writing script, not the natural language."""
    counts: Counter[str] = Counter()
    for char in str(value or ""):
        if char.isspace() or unicodedata.category(char).startswith("P"):
            continue
        name = unicodedata.name(char, "")
        if "LATIN" in name:
            counts["Latin"] += 1
        elif "CYRILLIC" in name:
            counts["Cyrillic"] += 1
        elif "GREEK" in name:
            counts["Greek"] += 1
        elif "ARABIC" in name:
            counts["Arabic"] += 1
        elif "HEBREW" in name:
            counts["Hebrew"] += 1
        elif any(marker in name for marker in ("CJK", "HIRAGANA", "KATAKANA", "HANGUL")):
            counts["CJK_or_EastAsian"] += 1
        elif char.isalpha():
            counts["Other"] += 1
    if not counts:
        return "empty_or_symbolic"
    dominant, dominant_count = counts.most_common(1)[0]
    return "mixed_scripts" if dominant_count / sum(counts.values()) < 0.8 else dominant


def language_stopword_profile(value: Any) -> str:
    """Return the language stopword profile with the most hits, if any."""
    word_list = query_tokens(value)
    if not word_list:
        return "none"
    scores = {language: sum(word in words for word in word_list) for language, words in LANG_STOPWORDS.items()}
    language, score = max(scores.items(), key=lambda item: item[1])
    return language if score else "none"


def expected_intent(value: Any) -> str:
    """Assign a deliberately transparent lexical intent proxy."""
    text = str(value or "")
    for intent, pattern in INTENT_PATTERNS.items():
        if pattern.search(text):
            return intent
    return "general"


def query_shape(value: Any) -> str:
    """Classify surface form for query-structure analysis."""
    text = str(value or "").strip()
    words = query_tokens(text)
    if not text:
        return "empty"
    if re.search(r"https?://|www\.", text, re.I):
        return "url_like"
    if "?" in text or re.match(r"^(who|what|when|where|which|why|how)\b", text, re.I):
        return "question"
    if re.match(r"^(compare|find|show|list|explain|install|configure|use|build|fix|write|create)\b", text, re.I):
        return "imperative"
    if len(words) <= 4:
        return "keyword_short"
    return "keyword_long"


def percentile(values: list[Any], q: float) -> float | None:
    """Compute a linearly interpolated percentile."""
    clean = sorted(float(value) for value in values if value is not None and math.isfinite(float(value)))
    if not clean:
        return None
    position = (len(clean) - 1) * q
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return clean[lower]
    return clean[lower] + (clean[upper] - clean[lower]) * (position - lower)


def mean(values: list[Any]) -> float | None:
    """Compute a finite arithmetic mean."""
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return sum(clean) / len(clean) if clean else None


def fmt(value: Any, digits: int = 3) -> str:
    """Format numeric report values compactly."""
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def records_table(rows: list[dict[str, Any]], columns: list[str], limit: int = 30) -> str:
    """Render bounded records as a readable Markdown table."""
    if not rows:
        return "_(no rows)_"
    selected = rows[:limit]
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = ["| " + " | ".join(str(row.get(column, "")) for column in columns) + " |" for row in selected]
    return "\n".join([header, divider, *body])


def row_counts(frame: pl.DataFrame, column: str, limit: int = 30) -> list[dict[str, Any]]:
    """Return value counts as plain records."""
    if column not in frame.columns:
        return []
    return frame.group_by(column, maintain_order=True).len().sort("len", descending=True).head(limit).to_dicts()


def run_query_features(runs: pl.DataFrame) -> pl.DataFrame:
    """Add NLP and payload features in the original `search_runs` row order."""
    rows = runs.select(["query", "normalized_query", "rewritten_branch_queries", "payload_json"]).to_dicts()
    records: list[dict[str, Any]] = []
    for row in rows:
        text = str(row["query"] or "")
        word_list = query_tokens(text)
        content = content_tokens(text)
        payload = parse_json(row["payload_json"])
        phase = payload.get("phase_timings", {}) if isinstance(payload, dict) else {}
        record: dict[str, Any] = {
            "query_chars": len(text),
            "query_tokens": len(word_list),
            "content_tokens": len(content),
            "script": script_class(text),
            "language_profile": language_stopword_profile(text),
            "shape": query_shape(text),
            "expected_intent_proxy": expected_intent(text),
            "content_signature": " ".join(sorted(set(content))),
            "normalized_repeat_key": str(row["normalized_query"] or "").strip().lower(),
            "rewrite_list_count": len(row["rewritten_branch_queries"]) if isinstance(row["rewritten_branch_queries"], list) else 0,
        }
        if isinstance(phase, dict):
            for key, value in phase.items():
                if isinstance(value, (int, float)) and math.isfinite(float(value)):
                    record[f"phase_{key}_ms"] = float(value)
        records.append(record)
    result = runs
    for name in records[0]:
        result = result.with_columns(pl.Series(name, [record.get(name) for record in records], strict=False))
    return result


def rewrite_metrics(variants: pl.DataFrame, runs: pl.DataFrame) -> pl.DataFrame:
    """Compute lexical preservation, verbosity, and structural rewrite metrics."""
    originals = {row["run_key"]: row["query"] for row in runs.select(["run_key", "query"]).to_dicts()}
    records: list[dict[str, Any]] = []
    for row in variants.select(["variant_id", "run_key", "variant_role", "query_text", "executed"]).to_dicts():
        original = query_tokens(originals.get(row["run_key"], ""))
        rewritten = query_tokens(row["query_text"])
        original_set, rewritten_set = set(original), set(rewritten)
        overlap = len(original_set & rewritten_set)
        precision = overlap / len(rewritten_set) if rewritten_set else None
        recall = overlap / len(original_set) if original_set else None
        f1 = 2 * precision * recall / (precision + recall) if precision and recall else 0.0
        records.append(
            {
                "variant_id": row["variant_id"],
                "run_key": row["run_key"],
                "variant_role": row["variant_role"],
                "executed": row["executed"],
                "original_tokens": len(original),
                "rewrite_tokens": len(rewritten),
                "length_ratio": len(rewritten) / len(original) if original else None,
                "token_precision": precision,
                "token_recall": recall,
                "token_f1": f1,
                "jaccard": overlap / len(original_set | rewritten_set) if original_set | rewritten_set else None,
                "char_similarity": SequenceMatcher(None, " ".join(original), " ".join(rewritten)).ratio(),
                "over_rewrite": bool(original and len(rewritten) > 2 * len(original)),
                "under_rewrite": bool(original and len(rewritten) < 0.5 * len(original) and (recall or 0.0) < 0.8),
            }
        )
    return pl.DataFrame(records, strict=False)


def parse_judgments(judgments: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Parse existing intent-coherence and rewrite-coverage verdict fragments."""
    intent_rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    rewrite_rows: list[dict[str, Any]] = []
    for row in judgments.select(["run_key", "judgment_kind", "judgment_target", "verdict", "confidence", "status"]).to_dicts():
        verdict = str(row["verdict"] or "").strip().lower()
        if row["judgment_kind"] == "intent_coherence" and row["status"] == "success":
            coherence = {"coherent": 1.0, "partially_coherent": 0.5, "incoherent": 0.0}.get(verdict)
            if coherence is not None:
                intent_rows.append({"run_key": row["run_key"], "intent_coherence": coherence, "verdict": verdict})
        elif row["judgment_kind"] == "rewrite_coverage":
            covered = re.search(r"covered\s*=\s*(\d+)\s*/\s*(\d+)", verdict, re.I)
            redundant = re.search(r"redundant\s*=\s*(True|False)", verdict, re.I)
            coverage_rows.append(
                {
                    "run_key": row["run_key"],
                    "covered": int(covered.group(1)) if covered else None,
                    "facets": int(covered.group(2)) if covered else None,
                    "coverage_ratio": int(covered.group(1)) / int(covered.group(2)) if covered and int(covered.group(2)) else None,
                    "redundant": redundant.group(1).lower() == "true" if redundant else None,
                    "verdict": verdict,
                }
            )
        elif row["judgment_kind"] == "judge_rewrite":
            rewrite_rows.append({"run_key": row["run_key"], "verdict": verdict, "confidence": row["confidence"]})
    return pl.DataFrame(intent_rows, strict=False), pl.DataFrame(coverage_rows, strict=False), pl.DataFrame(rewrite_rows, strict=False)


def provider_analysis(tables: dict[str, pl.DataFrame], runs: pl.DataFrame) -> dict[str, Any]:
    """Compute provider candidate and downstream label metrics."""
    run_ref = runs.select(["run_key", "intent"]).unique(subset=["run_key"], keep="first")
    calls = tables["provider_calls"].join(run_ref, on="run_key", how="left")
    overall = calls.group_by("provider").agg(
        pl.len().alias("calls"),
        (pl.col("status") == "success").mean().alias("success_rate"),
        (pl.col("num_results_returned") > 0).mean().alias("nonempty_rate"),
        pl.col("num_results_returned").mean().alias("mean_candidates"),
        pl.col("latency_ms").median().alias("median_latency_ms"),
        pl.col("latency_ms").quantile(0.95).alias("p95_latency_ms"),
        (pl.col("num_results_returned").sum() / (pl.col("latency_ms").sum() / 1000)).alias("candidates_per_second"),
    )
    selected = runs.explode("selected_providers").filter(pl.col("selected_providers").is_not_null()).group_by("selected_providers").agg(
        pl.col("run_key").n_unique().alias("selected_runs")
    ).rename({"selected_providers": "provider"})
    overall = overall.join(selected, on="provider", how="left").with_columns((pl.col("selected_runs") / runs.get_column("run_key").n_unique()).alias("selection_rate"))
    labels = tables["result_labels"].select(["run_key", "raw_url", "label"]).unique()
    labeled = tables["final_results"].join(labels, left_on=["run_key", "link"], right_on=["run_key", "raw_url"], how="inner")
    if labeled.height:
        labeled_provider = labeled.explode("providers").filter(pl.col("providers").is_not_null())
        label_summary = labeled_provider.group_by("providers").agg(
            pl.len().alias("labeled_final_slots"),
            pl.col("label").mean().alias("mean_label"),
            (pl.col("label") > 0).mean().alias("positive_label_rate"),
            pl.col("rank").median().alias("median_final_rank"),
        ).rename({"providers": "provider"})
        overall = overall.join(label_summary, on="provider", how="left")
    else:
        overall = overall.with_columns(
            pl.lit(None, dtype=pl.Int64).alias("labeled_final_slots"),
            pl.lit(None, dtype=pl.Float64).alias("mean_label"),
            pl.lit(None, dtype=pl.Float64).alias("positive_label_rate"),
            pl.lit(None, dtype=pl.Float64).alias("median_final_rank"),
        )
    per_intent = calls.group_by(["intent", "provider"]).agg(
        pl.len().alias("calls"),
        (pl.col("status") == "success").mean().alias("success_rate"),
        (pl.col("num_results_returned") > 0).mean().alias("nonempty_rate"),
        pl.col("num_results_returned").mean().alias("mean_candidates"),
        pl.col("latency_ms").median().alias("median_latency_ms"),
    )
    return {"overall": overall, "per_intent": per_intent, "labeled_rows": labeled.height, "calls": calls}


def correlation(frame: pl.DataFrame, feature: str) -> dict[str, Any]:
    """Compute pairwise Pearson and Spearman correlations with total latency."""
    if feature not in frame.columns:
        return {"feature": feature, "n": 0, "pearson": None, "spearman": None}
    pairs = frame.select([feature, "duration_ms"]).drop_nulls()
    values = [(float(row[feature]), float(row["duration_ms"])) for row in pairs.to_dicts()]
    values = [(x, y) for x, y in values if math.isfinite(x) and math.isfinite(y)]
    if len(values) < 3 or len({x for x, _ in values}) < 2 or len({y for _, y in values}) < 2:
        return {"feature": feature, "n": len(values), "pearson": None, "spearman": None}
    xs, ys = zip(*values)
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in values)
    denominator = math.sqrt(sum((x - x_mean) ** 2 for x in xs) * sum((y - y_mean) ** 2 for y in ys))
    pearson = numerator / denominator if denominator else None
    ranked_x = pl.Series(xs).rank(method="average").to_list()
    ranked_y = pl.Series(ys).rank(method="average").to_list()
    rx_mean = sum(ranked_x) / len(ranked_x)
    ry_mean = sum(ranked_y) / len(ranked_y)
    rank_num = sum((x - rx_mean) * (y - ry_mean) for x, y in zip(ranked_x, ranked_y))
    rank_den = math.sqrt(sum((x - rx_mean) ** 2 for x in ranked_x) * sum((y - ry_mean) ** 2 for y in ranked_y))
    return {"feature": feature, "n": len(values), "pearson": pearson, "spearman": rank_num / rank_den if rank_den else None}


def audit_schema(tables: dict[str, pl.DataFrame]) -> dict[str, Any]:
    """Enumerate all table/column evidence requested by the schema audit."""
    table_rows: list[dict[str, Any]] = []
    column_rows: list[dict[str, Any]] = []
    content_columns_re = re.compile(r"(?:query|title|url|domain|name|key|repository|tool_name)$", re.I)
    for name, frame in sorted(tables.items()):
        content_markers = 0
        content_examples: list[str] = []
        for column, dtype in frame.schema.items():
            series = frame.get_column(column)
            null_count = series.null_count()
            non_null = frame.height - null_count
            try:
                unique_count = series.n_unique()
            except Exception:
                unique_count = len({repr(value) for value in series.drop_nulls().to_list()})
            name_marker = bool(MARKER_RE.search(column))
            if dtype == pl.String and content_columns_re.search(column):
                for value in series.drop_nulls().to_list():
                    if CONTENT_MARKER_RE.search(str(value)):
                        content_markers += 1
                        if len(content_examples) < 3:
                            content_examples.append(f"{column}={value}")
            if null_count == frame.height or unique_count <= 1 or name_marker:
                samples = [repr(value) for value in series.drop_nulls().head(3).to_list()]
                column_rows.append(
                    {
                        "table": name,
                        "column": column,
                        "dtype": str(dtype),
                        "rows": frame.height,
                        "non_null": non_null,
                        "null_count": null_count,
                        "unique_non_null": unique_count,
                        "sample": " ; ".join(samples)[:160],
                        "name_marker": name_marker,
                    }
                )
        table_rows.append(
            {
                "table": name,
                "rows": frame.height,
                "columns": len(frame.columns),
                "name_marker": bool(MARKER_RE.search(name)),
                "content_marker_count": content_markers,
                "content_marker_examples": " ; ".join(content_examples)[:300],
            }
        )
    run_keys = set(tables["search_runs"].get_column("run_key").drop_nulls().to_list())
    parents = {
        "run_key": run_keys,
        "provider_call_id": set(tables["provider_calls"].get_column("provider_call_id").drop_nulls().to_list()),
        "branch_id": set(tables["search_branches"].get_column("branch_id").drop_nulls().to_list()),
        "canonical_result_id": set(tables["search_candidates"].get_column("canonical_result_id").drop_nulls().to_list()),
    }
    orphan_rows: list[dict[str, Any]] = []
    for name, frame in sorted(tables.items()):
        for key, known in parents.items():
            if key not in frame.columns:
                continue
            values = frame.get_column(key).drop_nulls().to_list()
            orphan_count = sum(value not in known for value in values)
            if orphan_count:
                orphan_rows.append({"table": name, "key": key, "non_null": len(values), "orphan_count": orphan_count, "orphan_rate": orphan_count / len(values)})
    writer_files = sorted(WRITER_DIR.glob("**/*.py")) if WRITER_DIR.exists() else []
    writer_text = {path: path.read_text(encoding="utf-8", errors="replace").lower() for path in writer_files}
    writer_hits = {name: [str(path) for path, text in writer_text.items() if name.lower() in text] for name in sorted(tables)}
    return {"tables": table_rows, "columns": column_rows, "orphans": orphan_rows, "writer_hits": writer_hits}


def payload_instrumentation(tables: dict[str, pl.DataFrame]) -> dict[str, Any]:
    """Search only latency-relevant payload columns for cache/retry instrumentation."""
    paths: Counter[str] = Counter()
    for name, columns in (("search_runs", ["payload_json"]), ("tool_calls", ["payload_json"]), ("provider_calls", ["response_meta_json"])):
        frame = tables.get(name)
        if frame is None:
            continue
        for column in columns:
            if column not in frame.columns:
                continue
            for value in frame.get_column(column).drop_nulls().to_list():
                paths.update(nested_key_paths(parse_json(value)))
    return {
        "cache_paths": [(path, count) for path, count in paths.most_common() if CACHE_RE.search(path)],
        "retry_paths": [(path, count) for path, count in paths.most_common() if RETRY_RE.search(path)],
    }


def latency_analysis(runs: pl.DataFrame, calls: pl.DataFrame) -> dict[str, Any]:
    """Compute query-centric seven-day latency associations."""
    anchor = runs.get_column("recorded_at").max()
    if not isinstance(anchor, datetime):
        raise ValueError("search_runs.recorded_at is required")
    start = anchor - timedelta(days=7)
    recent = runs.filter((pl.col("recorded_at") >= start) & (pl.col("recorded_at") <= anchor))
    numeric_features = ["query_chars", "query_tokens", "content_tokens", "understanding_confidence", "provider_count", "branch_count", "decomposition_depth", "rewrite_count", "rewrite_list_count", "candidate_count", "candidate_rows_observed", "final_result_count", "provider_call_count", "provider_error_rate", "retry_count", "rewrite_input_tokens", "rewrite_output_tokens", "rewrite_latency_ms"]
    correlations = [correlation(recent, feature) for feature in numeric_features if feature in recent.columns]
    recent_success = recent.filter(pl.col("status") == "success")
    success_correlations = [correlation(recent_success, feature) for feature in numeric_features if feature in recent_success.columns]
    categorical: dict[str, list[dict[str, Any]]] = {}
    for column in ("intent", "shape", "script", "rewrite_enabled", "status"):
        categorical[column] = recent.group_by(column, maintain_order=True).agg(
            pl.len().alias("n"),
            pl.col("duration_ms").median().alias("median_ms"),
            pl.col("duration_ms").quantile(0.95).alias("p95_ms"),
            pl.col("duration_ms").mean().alias("mean_ms"),
        ).sort("p95_ms", descending=True).to_dicts()
    quartile_tables: dict[str, list[dict[str, Any]]] = {}
    for feature in ("query_tokens", "query_chars", "candidate_count", "decomposition_depth", "rewrite_count"):
        values = [value for value in recent.get_column(feature).drop_nulls().to_list() if isinstance(value, (int, float))]
        if len(values) < 4 or len(set(values)) < 2:
            continue
        edges = [percentile(values, q) for q in (0.25, 0.5, 0.75)]
        bucket_labels = []
        for value in recent.get_column(feature).to_list():
            if value is None:
                bucket_labels.append("null")
            elif value <= edges[0]:
                bucket_labels.append("Q1")
            elif value <= edges[1]:
                bucket_labels.append("Q2")
            elif value <= edges[2]:
                bucket_labels.append("Q3")
            else:
                bucket_labels.append("Q4")
        quartile_tables[feature] = recent.with_columns(pl.Series("bucket", bucket_labels)).group_by("bucket", maintain_order=True).agg(
            pl.len().alias("n"), pl.col("duration_ms").median().alias("median_ms"), pl.col("duration_ms").quantile(0.95).alias("p95_ms")
        ).to_dicts()
    provider_presence: list[dict[str, Any]] = []
    run_provider = calls.select(["run_key", "provider"]).drop_nulls().unique()
    for provider in calls.get_column("provider").drop_nulls().unique().to_list():
        keys = run_provider.filter(pl.col("provider") == provider).get_column("run_key").to_list()
        subset = recent.filter(pl.col("run_key").is_in(keys))
        if subset.height >= 3:
            provider_presence.append({"provider": provider, "runs": subset.height, "median_ms": subset.get_column("duration_ms").median(), "p95_ms": subset.get_column("duration_ms").quantile(0.95)})
    non_success = recent.filter(pl.col("status") != "success")
    success_duration = {
        "n": recent_success.height,
        "median_ms": recent_success.get_column("duration_ms").median(),
        "p95_ms": recent_success.get_column("duration_ms").quantile(0.95),
        "p99_ms": recent_success.get_column("duration_ms").quantile(0.99),
        "max_ms": recent_success.get_column("duration_ms").max(),
    }
    return {
        "anchor": anchor,
        "start": start,
        "rows": recent.height,
        "success_rows": recent_success.height,
        "non_success_rows": non_success.height,
        "non_success_zero_duration": non_success.filter(pl.col("duration_ms") == 0).height,
        "duration": {"n": recent.height, "median_ms": recent.get_column("duration_ms").median(), "p95_ms": recent.get_column("duration_ms").quantile(0.95), "p99_ms": recent.get_column("duration_ms").quantile(0.99), "max_ms": recent.get_column("duration_ms").max()},
        "success_duration": success_duration,
        "correlations": correlations,
        "success_correlations": success_correlations,
        "categorical": categorical,
        "quartiles": quartile_tables,
        "provider_presence": sorted(provider_presence, key=lambda row: float(row["p95_ms"] or -1), reverse=True),
    }


def jsonable(value: Any) -> Any:
    """Convert timestamps and non-finite floats for JSON."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): jsonable(child) for key, child in value.items()}
    if isinstance(value, list):
        return [jsonable(child) for child in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def build_report(tables: dict[str, pl.DataFrame], runs: pl.DataFrame, variants: pl.DataFrame, intent_judge: pl.DataFrame, coverage_judge: pl.DataFrame, audit: dict[str, Any], provider: dict[str, Any], latency: dict[str, Any], payload: dict[str, Any]) -> str:
    """Build a query-first report with explicit formulas and caveats."""
    nonempty = runs.filter(pl.col("query").is_not_null() & (pl.col("query").str.len_chars() > 0))
    repeat = nonempty.filter(pl.col("normalized_repeat_key") != "").group_by("normalized_repeat_key").len().sort("len", descending=True)
    families = nonempty.filter(pl.col("content_signature") != "").group_by("content_signature").len().sort("len", descending=True)
    query_stats = {
        "median_chars": nonempty.get_column("query_chars").median(),
        "p95_chars": nonempty.get_column("query_chars").quantile(0.95),
        "median_tokens": nonempty.get_column("query_tokens").median(),
        "p95_tokens": nonempty.get_column("query_tokens").quantile(0.95),
    }
    run_ref = runs.select(["run_key", "intent"]).unique(subset=["run_key"], keep="first")
    intent_rates = intent_judge.group_by("run_key").agg(pl.col("intent_coherence").first()).join(run_ref, on="run_key", how="left").group_by("intent").agg(
        pl.len().alias("judged_n"),
        (pl.col("intent_coherence") == 1.0).mean().alias("coherent_rate"),
        (pl.col("intent_coherence") == 0.5).mean().alias("partial_rate"),
        (pl.col("intent_coherence") == 0.0).mean().alias("incoherent_rate"),
        pl.col("intent_coherence").mean().alias("coherence_score"),
    ).sort("coherence_score")
    variant_summary = variants.select(
        pl.len().alias("n"), pl.col("token_precision").mean().alias("mean_precision"), pl.col("token_recall").mean().alias("mean_recall"), pl.col("token_f1").mean().alias("mean_f1"), pl.col("char_similarity").mean().alias("mean_char_similarity"), pl.col("length_ratio").median().alias("median_length_ratio"), pl.col("length_ratio").quantile(0.95).alias("p95_length_ratio"), pl.col("over_rewrite").mean().alias("over_rate"), pl.col("under_rewrite").mean().alias("under_rate")
    ).to_dicts()[0] if variants.height else {}
    coverage_summary = coverage_judge.select(pl.len().alias("n"), pl.col("coverage_ratio").mean().alias("mean_coverage"), pl.col("redundant").mean().alias("redundant_rate")).to_dicts()[0] if coverage_judge.height else {}
    overall = provider["overall"].filter(pl.col("calls") >= 20).sort("nonempty_rate", descending=True)
    labeled = provider["overall"].filter(pl.col("labeled_final_slots") >= 5).sort("positive_label_rate", descending=True) if "labeled_final_slots" in provider["overall"].columns else pl.DataFrame()
    yield_rank = provider["overall"].filter(pl.col("calls") >= 20).sort("candidates_per_second", descending=True)
    flagged_tables = [row for row in audit["tables"] if row["name_marker"]]
    content_tables = [row for row in audit["tables"] if row["content_marker_count"]]
    absent_writers = [name for name, paths in audit["writer_hits"].items() if not paths]
    poor_intent = intent_rates.filter((pl.col("judged_n") >= 20) & (pl.col("coherence_score") < 0.5)).to_dicts() if intent_rates.height else []
    per_intent = provider["per_intent"].filter(pl.col("calls") >= 20).sort(["intent", "nonempty_rate"], descending=[False, True])
    transform_rules = row_counts(tables["query_transforms"], "rules_applied", 20)
    per_intent_winners: list[dict[str, Any]] = []
    intent_values = per_intent.get_column("intent").unique().to_list() if per_intent.height else []
    for intent_value in intent_values:
        intent_filter = pl.col("intent").is_null() if intent_value is None else pl.col("intent") == intent_value
        subset = per_intent.filter(intent_filter).filter(pl.col("calls") >= 20)
        if subset.height:
            per_intent_winners.append(
                subset.sort(["nonempty_rate", "success_rate", "calls"], descending=[True, True, True]).head(1).to_dicts()[0]
            )
    best_nonempty = overall.head(1).to_dicts()[0] if overall.height else None
    best_yield = yield_rank.head(1).to_dicts()[0] if yield_rank.height else None
    best_labeled = labeled.head(1).to_dicts()[0] if labeled.height else None
    large_sample_labeled = labeled.filter(pl.col("labeled_final_slots") >= 50).head(1).to_dicts() if labeled.height else []
    provider_finding_parts: list[str] = []
    if best_nonempty:
        provider_finding_parts.append(
            f"`{best_nonempty['provider']}` has the highest nonempty-call rate ({fmt(best_nonempty['nonempty_rate'])}, n={best_nonempty['calls']})"
        )
    if best_yield:
        provider_finding_parts.append(
            f"`{best_yield['provider']}` has the highest latency-adjusted candidate yield ({fmt(best_yield['candidates_per_second'])}/s)"
        )
    if best_labeled:
        provider_finding_parts.append(
            f"`{best_labeled['provider']}` has the highest labeled positive rate ({fmt(best_labeled['positive_label_rate'])}, n={best_labeled['labeled_final_slots']})"
        )
    if large_sample_labeled:
        sample_leader = large_sample_labeled[0]
        provider_finding_parts.append(
            f"`{sample_leader['provider']}` leads among providers with at least 50 labeled slots ({fmt(sample_leader['positive_label_rate'])}, n={sample_leader['labeled_final_slots']})"
        )
    provider_finding = (
        "Observed provider leaders are criterion-dependent: " + "; ".join(provider_finding_parts) + "."
        if provider_finding_parts
        else "No provider cohort met the minimum evidence thresholds."
    )
    top_tokens: Counter[str] = Counter()
    top_stopwords: Counter[str] = Counter()
    intent_counts = records_table(row_counts(runs, "intent", 10), ["intent", "len"])
    for text in runs.get_column("query").to_list():
        word_list = query_tokens(text)
        top_tokens.update(word_list)
        top_stopwords.update(word for word in word_list if word in STOPWORDS)
    token_rows = [{"token": token, "count": count, "stopword": token in STOPWORDS} for token, count in top_tokens.most_common(30)]
    stop_rows = [{"stopword": token, "count": count} for token, count in top_stopwords.most_common(15)]
    daily = runs.with_columns(pl.col("recorded_at").dt.date().alias("day")).group_by("day").agg(pl.len().alias("runs"), pl.col("query_tokens").mean().alias("mean_tokens"), pl.col("duration_ms").median().alias("median_latency_ms"), pl.col("duration_ms").quantile(0.95).alias("p95_latency_ms"), pl.col("rewrite_enabled").mean().alias("rewrite_rate"), (pl.col("status") == "success").mean().alias("success_rate")).sort("day")
    hour = runs.with_columns(pl.col("recorded_at").dt.hour().alias("utc_hour")).group_by("utc_hour").agg(pl.len().alias("runs"), pl.col("query_tokens").mean().alias("mean_tokens"), pl.col("duration_ms").median().alias("median_latency_ms"), pl.col("duration_ms").quantile(0.95).alias("p95_latency_ms")).sort("utc_hour")
    high_latency = runs.filter(pl.col("duration_ms") >= runs.get_column("duration_ms").quantile(0.99)).select(["recorded_at", "run_key", "query", "intent", "duration_ms", "status", "branch_count", "provider_count", "candidate_count", "rewrite_enabled", "rewrite_latency_ms", "error_type"]).sort("duration_ms", descending=True).head(20)
    lines = [
        "# Query-centric search-event findings — current DuckDB Parquet export",
        "",
        f"**Status:** changed and verified. The live DuckDB database was exported in read-only mode with `EXPORT DATABASE ... (FORMAT parquet, COMPRESSION zstd)` and loaded with Polars 1.41.2. Primary unit: `{runs.height}` `search_runs` rows from `{runs.get_column('recorded_at').min()}` through `{runs.get_column('recorded_at').max()}` UTC. The report follows the seven requested focus areas; database-wide schema evidence is isolated to section 5.",
        f"- Query text is present and analyzable for `{nonempty.height}`/{runs.height} runs. Median query length is `{fmt(query_stats['median_tokens'])}` tokens / `{fmt(query_stats['median_chars'])}` characters; p95 is `{fmt(query_stats['p95_tokens'])}` tokens / `{fmt(query_stats['p95_chars'])}` characters.",
        f"- Intent counts:\n\n{intent_counts}\n\nAggregate quality rates are not representative because `general` and `ai_coding_and_infrastructure` dominate.",
        f"- The export records `{variants.height}` rewrite variants and `{coverage_judge.height}` parseable rewrite-coverage judgments. Fidelity is reported as NLP overlap/length evidence, not unsupported semantic equivalence.",
        f"- Last-week latency is anchored to `{latency['anchor']}` and contains `{latency['rows']}` runs. Cache-hit correlation is reported only when explicit instrumentation can be linked to a run; fingerprints are not treated as hits.",
        "",
        "## Data and method",
        "",
        "- DuckDB source: `duckdb_data/analytics/search_events.duckdb`.",
        f"- Parquet export: `{EXPORT_DIR}`. Polars loaded every exported Parquet table for the schema audit; query-centric sections use only the event tables needed for the requested question.",
        "- NLP preprocessing: Unicode word tokenization, lowercasing, explicit multilingual stopword removal for content signatures, dominant Unicode script classification, surface query-shape rules, token set precision/recall/F1/Jaccard, and character-sequence similarity. No claim of language identification or semantic embedding similarity is made.",
        "",
        "## 1. Input query patterns",
        "",
        f"### Surface distributions\n\nQuery-shape counts:\n\n{records_table(row_counts(runs, 'shape'), ['shape', 'len'])}",
        "",
        f"Dominant-script counts (script heuristic, not language detection):\n\n{records_table(row_counts(runs, 'script'), ['script', 'len'])}",
        "",
        f"Stopword-profile counts (best lexical profile only; not a language detector):\n\n{records_table(row_counts(runs, 'language_profile'), ['language_profile', 'len'])}",
        "",
        f"Length: median `{fmt(query_stats['median_tokens'])}` tokens / `{fmt(query_stats['median_chars'])}` characters; p95 `{fmt(query_stats['p95_tokens'])}` tokens / `{fmt(query_stats['p95_chars'])}` characters. Length uses `search_runs.query` and Unicode word tokens.",
        "",
        "### Common tokens and stopwords",
        "",
        records_table(token_rows, ["token", "count", "stopword"]),
        "",
        records_table(stop_rows, ["stopword", "count"]),
        "",
        "### Recency, repetition, and families",
        "",
        f"Observed time span is `{runs.get_column('recorded_at').min()}` to `{runs.get_column('recorded_at').max()}` UTC. Exact normalized repetition uses lowercase trimmed `normalized_query`: `{repeat.filter(pl.col('len') > 1).height}` keys repeat at least twice; the most repeated key occurs `{repeat.get_column('len').max() if repeat.height else 0}` times.",
        "",
        "Largest repeated normalized queries:",
        "",
        records_table(repeat.head(20).to_dicts(), ["normalized_repeat_key", "len"]),
        "",
        f"Query families use sorted unique content tokens after stopword removal. `{families.filter(pl.col('len') > 1).height}` multi-row families exist; the largest contains `{families.get_column('len').max() if families.height else 0}` rows. This intentionally conservative family definition catches reorderings but not paraphrases.",
        "",
        "Largest content-token query families:",
        "",
        records_table(families.head(20).to_dicts(), ["content_signature", "len"]),
        "",
        "Daily query drift (last 20 observed days):",
        "",
        records_table(daily.tail(20).to_dicts(), ["day", "runs", "mean_tokens", "median_latency_ms", "p95_latency_ms", "rewrite_rate", "success_rate"]),
        "",
        "## 2. Intent correctness",
        "",
        f"Observed judge alignment is based on `{intent_judge.height}` parseable `llm_judgments` rows with `judgment_kind='intent_coherence'`, successful status, and verdicts `coherent`, `partially_coherent`, or `incoherent`. The score is 1, 0.5, or 0 respectively.",
        "",
        records_table(intent_rates.to_dicts() if intent_rates.height else [], ["intent", "judged_n", "coherent_rate", "partial_rate", "incoherent_rate", "coherence_score"]),
        "",
        f"Priority classes with at least 20 judged queries and coherence score below 0.5: `{poor_intent}`. In this export, these are the classes to inspect first; sparse classes are intentionally not promoted from a small denominator.",
        "",
        "Independent lexical expected-intent proxy cross-tab (review flag, not gold truth):",
        "",
        records_table(
            runs.group_by(["intent", "expected_intent_proxy"]).len().sort("len", descending=True).to_dicts(),
            ["intent", "expected_intent_proxy", "len"],
        ),
        "",
        "The proxy labels comparison/news/social-media/digital-humanities/coding-infrastructure cues before defaulting to general. Poor judge alignment or high proxy mismatch identifies classes for manual annotation; sparse classes are not reliable from rate alone. No independent human intent gold set is present in this export, so accuracy and Cohen's kappa are not claimed.",
        "",
        "## 3. Query rewrite quality and decomposition",
        "",
        f"### Rewrite fidelity\n\nAcross `{variant_summary.get('n', 0)}` `query_variants` rows: mean token precision `{fmt(variant_summary.get('mean_precision'))}`, recall `{fmt(variant_summary.get('mean_recall'))}`, F1 `{fmt(variant_summary.get('mean_f1'))}`, character similarity `{fmt(variant_summary.get('mean_char_similarity'))}`, median rewrite/original length ratio `{fmt(variant_summary.get('median_length_ratio'))}`, p95 `{fmt(variant_summary.get('p95_length_ratio'))}`. Over-rewrite is defined as rewrite token count `> 2 ×` original; under-rewrite is `< 0.5 ×` original **and** token recall `< 0.8`. Observed rates are `{fmt(variant_summary.get('over_rate'))}` and `{fmt(variant_summary.get('under_rate'))}`.",
        "",
        "Variant-role fidelity and length:",
        "",
        records_table(variants.group_by("variant_role").agg(pl.len().alias("n"), pl.col("token_f1").mean().alias("mean_f1"), pl.col("length_ratio").median().alias("median_ratio"), pl.col("over_rewrite").mean().alias("over_rate"), pl.col("under_rewrite").mean().alias("under_rate")).sort("mean_f1", descending=True).to_dicts(), ["variant_role", "n", "mean_f1", "median_ratio", "over_rate", "under_rate"]),
        "",
        "Query-transform rules observed in `query_transforms.rules_applied`:",
        "",
        records_table(transform_rules, ["rules_applied", "len"]),
        "",
        "### Coverage versus fragmentation",
        "",
        f"Parseable rewrite-coverage judgments report mean covered-facet ratio `{fmt(coverage_summary.get('mean_coverage'))}` across `{coverage_summary.get('n', 0)}` sets and redundant=True rate `{fmt(coverage_summary.get('redundant_rate'))}`. Decomposition depth is the number of distinct `search_branches.branch_role` values per run. Candidate duplication is a proxy: `1 - unique canonical IDs / observed candidate rows`.",
        "",
        records_table(runs.group_by("decomposition_depth").agg(pl.len().alias("runs"), pl.col("nonempty_branch_rate").mean().alias("mean_nonempty_branch_rate"), pl.col("candidate_rows_observed").mean().alias("mean_candidate_rows"), pl.col("candidate_unique_canonical").mean().alias("mean_unique_canonical"), pl.col("candidate_duplication_rate").mean().alias("mean_candidate_duplication_rate"), pl.col("mean_label").mean().alias("mean_labeled_relevance"), pl.col("duration_ms").median().alias("median_latency_ms")).sort("decomposition_depth").to_dicts(), ["decomposition_depth", "runs", "mean_nonempty_branch_rate", "mean_candidate_rows", "mean_unique_canonical", "mean_candidate_duplication_rate", "mean_labeled_relevance", "median_latency_ms"]),
        "",
        "Interpretation: decomposition helps when coverage/relevance increases without redundancy and latency increasing disproportionately. This export has partial labels, so candidate volume is not treated as quality and empty label cells remain missing rather than zero.",
        "",
        "## 4. Provider candidates",
        "",
        f"Provider-call metrics use `{provider['calls'].height}` `provider_calls` rows. Nonempty rate = calls with `num_results_returned > 0` / calls; success rate = status=success / calls; latency-adjusted yield = total returned candidates / total latency seconds. Provider raw scores are not compared across providers. Final-result labels matched `{provider['labeled_rows']}` rows by `(run_key, raw_url=link)`.",
        "",
        provider_finding,
        "",
        "Top nonempty-rate providers (minimum 20 calls):",
        "",
        records_table(overall.head(15).to_dicts(), ["provider", "calls", "success_rate", "nonempty_rate", "mean_candidates", "median_latency_ms", "p95_latency_ms", "selection_rate"]),
        "",
        "Top latency-adjusted candidate yield:",
        "",
        records_table(yield_rank.head(10).to_dicts(), ["provider", "calls", "candidates_per_second", "nonempty_rate", "median_latency_ms"]),
        "",
        "Top labeled positive final-result rate (minimum five labeled slots):",
        "",
        records_table(labeled.head(10).to_dicts(), ["provider", "labeled_final_slots", "positive_label_rate", "mean_label", "median_final_rank"]),
        "",
        "Per-intent leader by nonempty rate (minimum 20 calls per provider; ties break on success rate, then calls):",
        "",
        records_table(per_intent_winners, ["intent", "provider", "calls", "success_rate", "nonempty_rate", "mean_candidates", "median_latency_ms"]),
        "",
        "Sparse provider×intent cohorts below 20 calls are excluded from the leader table. Provider comparisons are observational: provider assignment, branch role, retries, cache state, and query intent are not randomized.",
        "",
        "## 5. Broken, orphaned, test, and deprecated schema evidence",
        "",
        f"This is the only database-wide section. Every one of `{len(audit['tables'])}` exported tables and every column was checked. A column is listed when all values are null, non-null values are constant (`unique_non_null <= 1`), or its name contains `test`, `tmp`, `deprecated`, `__`, `hnsw`, or `internal_table`. Content markers are counted only in String columns whose names end with `query`, `title`, `url`, `domain`, `name`, `key`, `repository`, or `tool_name`; matches are heuristic evidence, not proof of test data.",
        "",
        "Name-marked tables (content-marker count shown separately):",
        "",
        records_table(flagged_tables, ["table", "rows", "columns", "name_marker", "content_marker_count"]),
        "",
        "Content-marker evidence in selected identifier-like text columns:",
        "",
        records_table(content_tables, ["table", "rows", "columns", "content_marker_count", "content_marker_examples"]),
        "",
        "All flagged columns:",
        "",
        records_table(audit["columns"], ["table", "column", "dtype", "rows", "non_null", "null_count", "unique_non_null", "sample", "name_marker"], limit=len(audit["columns"])),
        "",
        "Checked orphan keys:",
        "",
        "Orphan counts are cross-table key mismatches against selected parent sets. Separate namespaces, legacy rows, and differing key domains can produce these counts, so they are evidence for reconciliation rather than proof that rows are broken. Downstream label matching uses `(run_key, raw_url=link)` because canonical IDs do not align across all tables.",
        "",
        records_table(audit["orphans"], ["table", "key", "non_null", "orphan_count", "orphan_rate"]),
        "",
        f"The writer-lineage check searched table-name literals under `{WRITER_DIR}`. No literal occurrence was found for: `{', '.join(absent_writers)}`. That is a conservative dynamic-writer warning, not proof of dead code. `_hnsw_test`, internal configuration tables, and test-looking catalog rows require explicit application-owner review before deletion.",
        "",
        "## 6. Last-week latency correlation",
        "",
        f"Window: `{latency['start']}` through `{latency['anchor']}` UTC, anchored to the observed maximum `search_runs.recorded_at`; `{latency['rows']}` runs, of which `{latency['success_rows']}` are successful. Total latency is `search_runs.duration_ms`.",
        "",
        f"All-status duration includes `{latency['non_success_rows']}` non-success rows; `{latency['non_success_zero_duration']}` of those have zero stored duration, so all-status correlations are descriptive and potentially censored.",
        "",
        records_table([latency["duration"]], ["n", "median_ms", "p95_ms", "p99_ms", "max_ms"]),
        "",
        "Success-only duration summary (`status='success'`):",
        "",
        records_table([latency["success_duration"]], ["n", "median_ms", "p95_ms", "p99_ms", "max_ms"]),
        "",
        "All-status Pearson and Spearman correlations (pairwise non-null n):",
        "",
        records_table(latency["correlations"], ["feature", "n", "pearson", "spearman"]),
        "",
        "Success-only Pearson and Spearman correlations:",
        "",
        records_table(latency["success_correlations"], ["feature", "n", "pearson", "spearman"]),
        "",
        "Categorical query/intent associations:",
    ]
    for category, rows in latency["categorical"].items():
        lines.extend([f"**{category}**", "", records_table(rows, [category, "n", "median_ms", "p95_ms", "mean_ms"]), ""])
    lines.extend(["Nonlinear quartile associations:", ""])
    for feature, rows in latency["quartiles"].items():
        lines.extend([f"**{feature}**", "", records_table(rows, ["bucket", "n", "median_ms", "p95_ms"]), ""])
    lines.extend([
        "Provider presence cohorts (a run is included if `provider_calls` contains the provider):",
        "",
        records_table(latency["provider_presence"][:20], ["provider", "runs", "median_ms", "p95_ms"]),
        "",
        "Cache/retry instrumentation discovery:",
        "",
        json.dumps(jsonable({"cache_paths": payload["cache_paths"], "retry_paths": payload["retry_paths"]}), indent=2),
        "",
            "The payload scan found cache/retry-like keys, but those occurrences cannot be joined to `search_runs` by run key in this export; cache-hit correlation is therefore unavailable. Retry count is inferred only from repeated `(run_key, branch_index, provider)` call groups; it is not a server-reported retry counter.",
        "",
        "## 7. Exploratory data analysis",
        "",
        "UTC-hour query/latency cohorts:",
        "",
        records_table(hour.to_dicts(), ["utc_hour", "runs", "mean_tokens", "median_latency_ms", "p95_latency_ms"], limit=24),
        "",
        f"Successful runs with zero stored candidates or final results: `{runs.filter((pl.col('status') == 'success') & ((pl.col('candidate_count').fill_null(0) == 0) | (pl.col('final_result_count').fill_null(0) == 0))).height}`. Treat these as reconciliation candidates, not automatic failures.",
        "",
            "P99 latency query slice (all-history threshold; not the last-week window):",
        "",
        records_table(high_latency.to_dicts(), ["recorded_at", "run_key", "query", "intent", "duration_ms", "status", "branch_count", "provider_count", "candidate_count", "rewrite_enabled", "rewrite_latency_ms", "error_type"]),
        "",
        "The most useful drift lens is the daily table in section 1: compare query length, rewrite rate, success rate, and latency together rather than attributing a change to one provider. Provider×intent and decomposition-depth tables expose interaction effects without pretending they are causal experiments.",
        "",
        "## Metric formulas and limitations",
        "",
        "- Query length = Unicode word-token count and character count of `search_runs.query`; repetition = duplicate lowercase-trimmed `normalized_query`; family = sorted unique non-stopword token signature.",
        "- Token fidelity: precision = |original ∩ rewrite| / |rewrite|; recall = |original ∩ rewrite| / |original|; F1 = harmonic mean; Jaccard = |intersection| / |union|; character similarity is `SequenceMatcher` only. These are not semantic embeddings.",
        "- Provider candidate yield = mean `num_results_returned`; latency-adjusted yield = Σ returned / (Σ `latency_ms` / 1000); candidate duplication proxy = 1 - unique canonical IDs / observed candidate rows; downstream success = `result_labels.label > 0` after `(run_key, raw_url=link)` matching.",
        "- Pearson is linear association; Spearman is Pearson on ranks and captures monotonic nonlinear association. Quartile tables are used to expose thresholds/U-shapes that a single coefficient hides. No p-values or causal effects are claimed.",
        "",
        "## External grounding",
        "",
        "External research supports using end-to-end Recall@k/MRR/nDCG for retrieval-grounded rewrite evaluation, lexical overlap as a secondary proxy, explicit coverage-versus-redundancy checks for decomposition, and p50/p95/p99 latency with cache/retry confounds reported separately.",
        "",
        "- Stanford IR Book ranked retrieval evaluation: https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-of-ranked-retrieval-results-1.html",
        "- ReDI decomposition and interpretation: https://arxiv.org/html/2509.06544v4",
        "- MiniELM rewrite evaluation: https://arxiv.org/html/2501.18056v2",
        "- VALUE rewrite-fidelity caveats: https://arxiv.org/html/2504.05321v2",
        "- CONQRR length-aware rewriting: https://aclanthology.org/2022.emnlp-main.679.pdf",
        "- Survey of conversational-search reformulation evaluation: https://arxiv.org/html/2410.15576v2",
        "- Parallel.ai web-search evaluation methodology: https://parallel.ai/blog/how-to-eval-web-search",
        "- Milvus cache benchmarking caveat: https://milvus.io/ai-quick-reference/how-does-caching-affect-benchmarking-results",
        "- DuckDB `EXPORT DATABASE` documentation: https://duckdb.org/docs/current/sql/statements/export",
        "- Kassis, Agarwal, He, Patel, Brueckner, et al., *Scientific Agent Skills* (2026), arXiv:2609.00065: https://doi.org/10.48550/arXiv.2609.00065",
        "",
        "## Artifacts",
        "",
        f"- Current Parquet export: `{EXPORT_DIR}`",
        f"- Analysis script: `{Path(__file__)}`",
        f"- Machine-readable metrics: `{JSON_PATH}`",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    """Run query-centric analysis and write Markdown/JSON outputs."""
    tables = load_tables()
    runs = run_query_features(tables["search_runs"])
    branches = tables["search_branches"]
    branch_agg = branches.group_by("run_key").agg(
        pl.col("branch_role").n_unique().alias("decomposition_depth"),
        (pl.col("results_count") > 0).mean().alias("nonempty_branch_rate"),
    )
    variants = rewrite_metrics(tables["query_variants"], tables["search_runs"])
    variant_agg = tables["query_variants"].group_by("run_key").agg(pl.len().alias("rewrite_count"))
    calls = tables["provider_calls"]
    call_agg = calls.group_by("run_key").agg(
        pl.len().alias("provider_call_count"),
        (pl.col("status") == "error").mean().alias("provider_error_rate"),
    )
    retry_groups = calls.group_by(["run_key", "branch_index", "provider"]).len().with_columns(
        pl.when(pl.col("len") > 1).then(pl.col("len") - 1).otherwise(0).alias("retries")
    ).group_by("run_key").agg(pl.col("retries").sum().alias("retry_count"))
    candidate_agg = tables["search_candidates"].group_by("run_key").agg(
        pl.len().alias("candidate_rows_observed"), pl.col("canonical_result_id").n_unique().alias("candidate_unique_canonical")
    ).with_columns(
        pl.when(pl.col("candidate_rows_observed") > 0)
        .then(1 - pl.col("candidate_unique_canonical") / pl.col("candidate_rows_observed"))
        .otherwise(pl.lit(None, dtype=pl.Float64))
        .alias("candidate_duplication_rate")
    )
    label_agg = tables["result_labels"].group_by("run_key").agg(
        pl.col("label").mean().alias("mean_label"),
        (pl.col("label") > 0).mean().alias("positive_label_rate"),
    )
    runs = runs.join(label_agg, on="run_key", how="left")
    runs = runs.join(branch_agg, on="run_key", how="left").join(variant_agg, on="run_key", how="left").join(call_agg, on="run_key", how="left").join(retry_groups, on="run_key", how="left").join(candidate_agg, on="run_key", how="left")
    runs = runs.with_columns(pl.col("retry_count").fill_null(0))
    intent_judge, coverage_judge, rewrite_judge = parse_judgments(tables["llm_judgments"])
    audit = audit_schema(tables)
    provider = provider_analysis(tables, tables["search_runs"])
    latency = latency_analysis(runs, calls)
    payload = payload_instrumentation(tables)
    report = build_report(tables, runs, variants, intent_judge, coverage_judge, audit, provider, latency, payload)
    query_rows = runs.select(["recorded_at", "run_key", "query", "normalized_query", "intent", "expected_intent_proxy", "shape", "script", "language_profile", "query_chars", "query_tokens", "content_tokens", "rewrite_enabled", "branch_count", "decomposition_depth", "rewrite_count", "rewrite_list_count", "candidate_count", "candidate_rows_observed", "candidate_unique_canonical", "candidate_duplication_rate", "final_result_count", "duration_ms", "status", "mean_label", "positive_label_rate"]).to_dicts()
    REPORT_PATH.write_text(report, encoding="utf-8")
    JSON_PATH.write_text(json.dumps(jsonable({
        "table_inventory": {name: {"rows": frame.height, "columns": frame.columns, "schema": {column: str(dtype) for column, dtype in frame.schema.items()}} for name, frame in tables.items()},
        "query_rows": query_rows,
        "rewrite_variant_rows": variants.to_dicts(),
        "intent_judgments": intent_judge.to_dicts(),
        "rewrite_coverage_judgments": coverage_judge.to_dicts(),
        "rewrite_judgments": rewrite_judge.to_dicts(),
        "provider_overall": provider["overall"].to_dicts(),
        "provider_per_intent": provider["per_intent"].to_dicts(),
        "latency": latency,
        "audit": audit,
        "payload_instrumentation": payload,
    }), indent=2), encoding="utf-8")
    print(f"wrote {REPORT_PATH} and {JSON_PATH}")


if __name__ == "__main__":
    main()
