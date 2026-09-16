"""Analyze input/rewrite query text directly without judge scores or embeddings."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
import json
import math
import re
from statistics import median
from typing import Any

import polars as pl


EXPORT_DIR = Path("duckdb_data/analytics/exports/2026-09-15/full_parquet")
OUTPUT_DIR = Path("docs/analysis")
PAIR_PATH = OUTPUT_DIR / "semantic_query_rewrite_pairs_2026-09-15.parquet"
INPUT_PATH = OUTPUT_DIR / "semantic_query_inputs_2026-09-15.parquet"
REPORT_PATH = OUTPUT_DIR / "semantic_query_rewrite_text_2026-09-15.md"
JSON_PATH = OUTPUT_DIR / "semantic_query_rewrite_text_2026-09-15.json"

WORD_RE = re.compile(r"[\w][\w+#./:@-]*", re.UNICODE)
URL_RE = re.compile(r"(?:https?://|www\.)\S+|\b(?:[a-z0-9-]+\.)+(?:com|org|io|ai|dev|net|gov|edu)\b", re.I)
NUMBER_RE = re.compile(r"(?<!\w)(?:\d+(?:\.\d+)*(?:[a-z]+)?|[a-z]{2,}[0-9]+(?:[-_][a-z0-9]+)*)(?!\w)", re.I)
QUOTED_RE = re.compile(r"(?:\"([^\"]+)\"|'([^']+)')")

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in", "is", "it", "of",
    "on", "or", "that", "the", "this", "to", "using", "what", "when", "where", "which", "who", "why",
    "with", "without", "about", "can", "does", "do", "into", "than", "their", "there", "these", "those",
}
GENERIC_RETRIEVAL_TERMS = {
    "api", "best", "confirm", "documentation", "docs", "example", "examples", "find", "guide", "information",
    "official", "query", "reference", "search", "show", "sources", "support", "use", "usage", "web",
}
QUESTION_START_RE = re.compile(r"^(?:who|what|when|where|which|why|how|whether)\b", re.I)
IMPERATIVE_START_RE = re.compile(r"^(?:compare|find|show|list|explain|install|configure|use|build|fix|write|create|identify|locate|give)\b", re.I)
OPERATOR_RE = re.compile(r"(?:\bsite:|\bfiletype:|\bintitle:|\bOR\b|\bAND\b|\"|https?://|www\.|\bafter:|\bbefore:|[-+]\w)", re.I)


def safe_text(value: Any) -> str:
    """Return a query field as normalized text for analysis."""
    return str(value or "").strip()


def tokens(text: str) -> list[str]:
    """Tokenize query text while retaining technical punctuation inside terms."""
    return [match.group(0).lower() for match in WORD_RE.finditer(text)]


def content_terms(text: str) -> set[str]:
    """Return lower-case lexical content terms without ordinary stopwords."""
    return {token for token in tokens(text) if token not in STOPWORDS and len(token) > 1}


def anchor_terms(text: str) -> set[str]:
    """Extract technical/entity anchors whose loss can change query meaning."""
    found: set[str] = set()
    found.update(match.group(0).lower().rstrip(".,;)") for match in URL_RE.finditer(text))
    found.update(match.group(0).lower() for match in NUMBER_RE.finditer(text))
    for token in WORD_RE.findall(text):
        lower = token.lower()
        if any(marker in token for marker in ("_", "/", ":", "#", "+")) or "-" in token:
            found.add(lower)
        elif token.isupper() and len(token) >= 2:
            found.add(lower)
        elif any(char.isdigit() for char in token):
            found.add(lower)
    return found


def ngrams(values: list[str], size: int) -> Counter[str]:
    """Count contiguous token n-grams."""
    return Counter(" ".join(values[index : index + size]) for index in range(len(values) - size + 1))


def phrase_set(text: str) -> set[str]:
    """Return contiguous bigrams and trigrams used for phrase preservation checks."""
    values = tokens(text)
    return set(ngrams(values, 2)) | set(ngrams(values, 3))


def repeated_rewrite_phrases(text: str) -> list[str]:
    """Return duplicated contiguous phrases inside one rewrite."""
    values = tokens(text)
    repeated: set[str] = set()
    for size in (2, 3, 4):
        for phrase, count in ngrams(values, size).items():
            if count > 1:
                repeated.add(phrase)
    return sorted(repeated)


def operator_profile(text: str) -> set[str]:
    """Describe query operators and constrained syntax present in text."""
    profile: set[str] = set()
    if re.search(r"\bsite:", text, re.I):
        profile.add("site")
    if re.search(r"\bfiletype:", text, re.I):
        profile.add("filetype")
    if re.search(r"\bintitle:", text, re.I):
        profile.add("intitle")
    if re.search(r"\b(?:after|before):", text, re.I):
        profile.add("date")
    if re.search(r"\bOR\b|\bAND\b", text):
        profile.add("boolean")
    if '"' in text or "'" in text:
        profile.add("quoted")
    if URL_RE.search(text):
        profile.add("url")
    return profile


def frame(text: str) -> str:
    """Classify the leading textual request frame."""
    if QUESTION_START_RE.match(text):
        return "question"
    if IMPERATIVE_START_RE.match(text):
        return "imperative"
    return "keyword"


def ratio(intersection: int, denominator: int) -> float | None:
    """Compute a nullable set-overlap ratio."""
    return intersection / denominator if denominator else None


def analyze_pair(row: dict[str, Any]) -> dict[str, Any]:
    """Analyze one matched input/rewrite pair using only the two query strings."""
    original = safe_text(row["input_query"])
    rewritten = safe_text(row["rewritten_query"])
    original_tokens = tokens(original)
    rewritten_tokens = tokens(rewritten)
    original_content = content_terms(original)
    rewritten_content = content_terms(rewritten)
    original_anchors = anchor_terms(original)
    rewritten_anchors = anchor_terms(rewritten)
    original_phrases = phrase_set(original)
    rewritten_phrases = phrase_set(rewritten)
    repeated_phrases = repeated_rewrite_phrases(rewritten)
    added_content = sorted(rewritten_content - original_content)
    dropped_content = sorted(original_content - rewritten_content)
    added_anchors = sorted(rewritten_anchors - original_anchors)
    dropped_anchors = sorted(original_anchors - rewritten_anchors)
    changed_operator = operator_profile(original) != operator_profile(rewritten)
    original_frame, rewritten_frame = frame(original), frame(rewritten)
    exact = " ".join(original_tokens) == " ".join(rewritten_tokens)
    anchor_recall = ratio(len(original_anchors & rewritten_anchors), len(original_anchors))
    content_recall = ratio(len(original_content & rewritten_content), len(original_content))
    content_precision = ratio(len(original_content & rewritten_content), len(rewritten_content))
    phrase_recall = ratio(len(original_phrases & rewritten_phrases), len(original_phrases))
    if exact:
        text_class = "identity"
    elif dropped_anchors:
        text_class = "anchor_loss"
    elif repeated_phrases:
        text_class = "repeated_phrase_pollution"
    elif len(added_content) > max(4, len(original_content)):
        text_class = "broad_expansion"
    elif dropped_content:
        text_class = "content_contraction"
    elif added_content:
        text_class = "focused_expansion"
    else:
        text_class = "surface_rewrite"
    return {
        "variant_id": row["variant_id"],
        "run_key": row["run_key"],
        "variant_role": row["variant_role"],
        "executed": row["executed"],
        "input_query": original,
        "rewritten_query": rewritten,
        "input_token_count": len(original_tokens),
        "rewrite_token_count": len(rewritten_tokens),
        "input_content_terms": sorted(original_content),
        "rewrite_content_terms": sorted(rewritten_content),
        "added_content_terms": added_content,
        "dropped_content_terms": dropped_content,
        "input_anchor_terms": sorted(original_anchors),
        "rewrite_anchor_terms": sorted(rewritten_anchors),
        "added_anchor_terms": added_anchors,
        "dropped_anchor_terms": dropped_anchors,
        "content_recall": content_recall,
        "content_precision": content_precision,
        "anchor_recall": anchor_recall,
        "phrase_recall": phrase_recall,
        "added_content_count": len(added_content),
        "dropped_content_count": len(dropped_content),
        "dropped_anchor_count": len(dropped_anchors),
        "repeated_phrase_count": len(repeated_phrases),
        "repeated_phrases": repeated_phrases,
        "input_frame": original_frame,
        "rewrite_frame": rewritten_frame,
        "frame_changed": original_frame != rewritten_frame,
        "operator_changed": changed_operator,
        "text_class": text_class,
        "input_phrases": sorted(original_phrases),
        "rewrite_phrases": sorted(rewritten_phrases),
    }


def mean(values: list[Any]) -> float | None:
    """Return a finite mean for nullable numeric values."""
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return sum(clean) / len(clean) if clean else None


def percentile(values: list[Any], q: float) -> float | None:
    """Return a linear percentile for nullable numeric values."""
    clean = sorted(float(value) for value in values if value is not None and math.isfinite(float(value)))
    if not clean:
        return None
    position = (len(clean) - 1) * q
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return clean[lower]
    return clean[lower] + (clean[upper] - clean[lower]) * (position - lower)


def fmt(value: Any, digits: int = 3) -> str:
    """Format a report value."""
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def markdown_table(rows: list[dict[str, Any]], columns: list[str], limit: int = 30) -> str:
    """Render bounded records as Markdown."""
    if not rows:
        return "_(no rows)_"
    selected = rows[:limit]
    body = ["| " + " | ".join(str(row.get(column, "")) for column in columns) + " |" for row in selected]
    return "\n".join(["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |", *body])


def counter_rows(counter: Counter[str], key: str, limit: int = 30) -> list[dict[str, Any]]:
    """Convert a counter to report rows."""
    return [{key: value, "count": count} for value, count in counter.most_common(limit)]


def aggregate_pairs(pair_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Summarize text-preservation and expansion behavior by rewrite role."""
    by_role: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pair_rows:
        by_role[str(row["variant_role"])].append(row)
    role_rows: list[dict[str, Any]] = []
    added_by_role: list[dict[str, Any]] = []
    for role, rows in sorted(by_role.items()):
        role_rows.append(
            {
                "variant_role": role,
                "pairs": len(rows),
                "unique_inputs": len({row["run_key"] for row in rows}),
                "identity_rate": mean([row["text_class"] == "identity" for row in rows]),
                "content_recall": mean([row["content_recall"] for row in rows]),
                "anchor_recall": mean([row["anchor_recall"] for row in rows]),
                "anchor_loss_rate": mean([bool(row["dropped_anchor_terms"]) for row in rows]),
                "mean_added_terms": mean([row["added_content_count"] for row in rows]),
                "median_added_terms": median([row["added_content_count"] for row in rows]),
                "mean_dropped_terms": mean([row["dropped_content_count"] for row in rows]),
                "repeated_phrase_rate": mean([bool(row["repeated_phrases"]) for row in rows]),
                "frame_change_rate": mean([row["frame_changed"] for row in rows]),
                "operator_change_rate": mean([row["operator_changed"] for row in rows]),
            }
        )
        additions: Counter[str] = Counter()
        for row in rows:
            additions.update(row["added_content_terms"])
        added_by_role.extend({"variant_role": role, **item} for item in counter_rows(additions, "term", 15))
    return role_rows, added_by_role


def phrase_reuse(pair_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find rewrite phrases reused across distinct input queries."""
    occurrences: dict[str, set[str]] = defaultdict(set)
    roles: dict[str, Counter[str]] = defaultdict(Counter)
    for row in pair_rows:
        original_phrases = set(row["input_phrases"])
        for phrase in row["rewrite_phrases"]:
            if phrase not in original_phrases:
                occurrences[phrase].add(row["run_key"])
                roles[phrase][str(row["variant_role"])] += 1
    rows = []
    for phrase, run_keys in occurrences.items():
        if len(run_keys) >= 3:
            rows.append({"phrase": phrase, "distinct_inputs": len(run_keys), "occurrences": sum(roles[phrase].values()), "roles": dict(roles[phrase])})
    return sorted(rows, key=lambda row: (row["distinct_inputs"], row["occurrences"]), reverse=True)


def derive_conclusions(
    pair_rows: list[dict[str, Any]],
    role_rows: list[dict[str, Any]],
    phrase_rows: list[dict[str, Any]],
) -> list[str]:
    """Build conclusions from the completed text-only measurements."""
    if not pair_rows:
        return ["- No matched input/rewrite pairs were available."]

    pair_count = len(pair_rows)
    identity_count = sum(row["text_class"] == "identity" for row in pair_rows)
    anchor_loss_count = sum(bool(row["dropped_anchor_terms"]) for row in pair_rows)
    repeated_count = sum(bool(row["repeated_phrases"]) for row in pair_rows)
    total_added = sum(row["added_content_count"] for row in pair_rows)
    total_dropped = sum(row["dropped_content_count"] for row in pair_rows)
    all_added = Counter(term for row in pair_rows for term in row["added_content_terms"])
    all_dropped = Counter(term for row in pair_rows for term in row["dropped_content_terms"])
    frame_changes = Counter((row["input_frame"], row["rewrite_frame"]) for row in pair_rows if row["frame_changed"])
    conclusions = [
        (
            f"- Token-normalized text is unchanged in `{identity_count}/{pair_count}` pairs "
            f"({identity_count / pair_count:.1%}); the remaining `{pair_count - identity_count}` pairs "
            "change at least one token."
        ),
        (
            f"- The extracted-anchor diagnostic flags `{anchor_loss_count}/{pair_count}` pairs "
            f"({anchor_loss_count / pair_count:.1%}) with at least one input anchor absent from the rewrite. "
            "This is a text-preservation risk indicator, not a semantic-failure label."
        ),
        (
            f"- Rewrites add `{total_added}` content-term occurrences and drop `{total_dropped}`. "
            f"The most frequent additions are `{', '.join(term for term, _ in all_added.most_common(8)) or 'none'}`, "
            f"while the most frequent drops are `{', '.join(term for term, _ in all_dropped.most_common(8)) or 'none'}`."
        ),
        (
            f"- Repeated contiguous phrases occur in `{repeated_count}/{pair_count}` pairs "
            f"({repeated_count / pair_count:.1%}). This is a direct textual repetition signal, not a quality score."
        ),
    ]
    if role_rows:
        max_pair_count = max(row["pairs"] for row in role_rows)
        balanced_roles = [row for row in role_rows if row["pairs"] == max_pair_count]
        highest_anchor_loss = max(balanced_roles, key=lambda row: row["anchor_loss_rate"] or -1.0)
        highest_additions = max(balanced_roles, key=lambda row: row["mean_added_terms"] or -1.0)
        conclusions.append(
            f"- Among rewrite groups with the maximum observed pair count (n=`{max_pair_count}`), "
            f"`{highest_anchor_loss['variant_role']}` has the highest extracted-anchor-loss rate "
            f"(`{fmt(highest_anchor_loss['anchor_loss_rate'])}`), while `{highest_additions['variant_role']}` "
            f"has the highest mean number of added content terms (`{fmt(highest_additions['mean_added_terms'])}`)."
        )
    if frame_changes:
        transition, count = frame_changes.most_common(1)[0]
        conclusions.append(
            f"- The most common request-frame change is `{transition[0]} → {transition[1]}` "
            f"({count} pairs); this records surface framing change without asserting intent change."
        )
    if phrase_rows:
        phrase = phrase_rows[0]
        roles = ", ".join(sorted(str(role) for role in phrase["roles"]))
        conclusions.append(
            f"- The most widely reused added phrase is `{phrase['phrase']}`, appearing across "
            f"`{phrase['distinct_inputs']}` distinct inputs and `{phrase['occurrences']}` occurrences "
            f"(roles: `{roles}`). This is evidence of shared wording in the rewrite text."
        )
    return conclusions


def example_rows(pair_rows: list[dict[str, Any]], predicate: Any, limit: int = 20) -> list[dict[str, Any]]:
    """Return text-only examples selected by a predicate."""
    selected = [row for row in pair_rows if predicate(row)]
    selected.sort(key=lambda row: (row["dropped_anchor_count"], row["repeated_phrase_count"], row["added_content_count"]), reverse=True)
    return [
        {
            "variant_role": row["variant_role"],
            "input_query": row["input_query"],
            "rewritten_query": row["rewritten_query"],
            "text_class": row["text_class"],
            "added_terms": ", ".join(row["added_content_terms"][:15]),
            "dropped_anchors": ", ".join(row["dropped_anchor_terms"]),
            "repeated_phrases": "; ".join(row["repeated_phrases"][:4]),
        }
        for row in selected[:limit]
    ]


def jsonable(value: Any) -> Any:
    """Convert nested values to JSON-safe values."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): jsonable(child) for key, child in value.items()}
    if isinstance(value, list):
        return [jsonable(child) for child in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def build_report(
    inputs: pl.DataFrame,
    pairs: pl.DataFrame,
    pair_rows: list[dict[str, Any]],
    role_rows: list[dict[str, Any]],
    added_by_role: list[dict[str, Any]],
    phrase_rows: list[dict[str, Any]],
    loss_examples: list[dict[str, Any]],
    pollution_examples: list[dict[str, Any]],
    expansion_examples: list[dict[str, Any]],
    conclusions: list[str],
) -> str:
    """Build a report whose evidence is query text and rewrite text."""
    class_counts = Counter(row["text_class"] for row in pair_rows)
    all_added = Counter(term for row in pair_rows for term in row["added_content_terms"])
    all_dropped = Counter(term for row in pair_rows for term in row["dropped_content_terms"])
    frame_changes = Counter((row["input_frame"], row["rewrite_frame"]) for row in pair_rows if row["frame_changed"])
    operator_changes = Counter((tuple(sorted(operator_profile(row["input_query"]))), tuple(sorted(operator_profile(row["rewritten_query"])))) for row in pair_rows if row["operator_changed"])
    return "\n".join(
        [
            "# Semantic query-rewrite text analysis — 2026-09-15",
            "",
            "**Scope:** every input query is pulled from `search_runs.query`; every available rewrite is matched from `query_variants.query_text` by `run_key`. This analysis uses the text content only. It does not use `llm_judgments`, result labels, provider metrics, embeddings, or metadata-derived quality scores.",
            "",
            "## Corpus pulled from the data",
            "",
            f"- Unique input query rows: **{inputs.height}**.",
            f"- Inputs with at least one matched rewrite: **{pairs.get_column('run_key').n_unique()}**.",
            f"- Inputs without a rewrite row: **{inputs.height - pairs.get_column('run_key').n_unique()}**.",
            f"- Matched input/rewrite pairs: **{pairs.height}**. Every pair has non-null input and rewrite text.",
            f"- Rewrite roles represented: `{', '.join(sorted(str(value) for value in pairs.get_column('variant_role').unique().to_list()))}`.",
            "",
            "The complete paired text is stored in the Parquet artifact linked at the end of this report. The report first presents computed text distributions and exact examples; interpretation is performed separately after this data-only pass.",
            "",
            "## What the rewrite text is doing",
            "",
            f"Text classes across all pairs: `{dict(class_counts)}`. `identity` means token-normalized text is unchanged; `anchor_loss` means at least one technical/entity anchor, URL, number, version, or constrained token from the input disappears; `repeated_phrase_pollution` means a contiguous phrase repeats inside the rewrite; `broad_expansion` means the rewrite adds more than four content terms and more than the input content-term count; `content_contraction` drops input content terms; `focused_expansion` adds content without those loss/pollution signals. These classes are text rules, not quality labels.",
            "",
            "By rewrite role:",
            "",
            markdown_table(role_rows, ["variant_role", "pairs", "unique_inputs", "identity_rate", "content_recall", "anchor_recall", "anchor_loss_rate", "mean_added_terms", "median_added_terms", "mean_dropped_terms", "repeated_phrase_rate", "frame_change_rate", "operator_change_rate"]),
            "",
            "## Content terms added to the input",
            "",
            "Most frequent added terms across all rewritten text:",
            "",
            markdown_table(counter_rows(all_added, "term", 40), ["term", "count"]),
            "",
            "Most frequent input content terms dropped by rewrites:",
            "",
            markdown_table(counter_rows(all_dropped, "term", 30), ["term", "count"]),
            "",
            "Added terms by role:",
            "",
            markdown_table(added_by_role, ["variant_role", "term", "count"], limit=180),
            "",
            "### Data-derived added-text observations",
            "",
            f"Added content-term occurrences: `{sum(all_added.values())}`. The eight most frequent additions computed from the paired text are `{', '.join(term for term, _ in all_added.most_common(8)) or 'none'}`.",
            f"Input content terms dropped by rewrites: `{sum(all_dropped.values())}` occurrences. The eight most frequent dropped terms computed from the paired text are `{', '.join(term for term, _ in all_dropped.most_common(8)) or 'none'}`.",
            "The role table and examples below are the evidence for interpreting whether these additions clarify the input or introduce scope/template noise.",
            "",
            "## Anchor, phrase, frame, and operator preservation",
            "",
            f"Across all pairs, mean content-term recall is `{fmt(mean([row['content_recall'] for row in pair_rows]))}`, mean anchor recall is `{fmt(mean([row['anchor_recall'] for row in pair_rows]))}`, and `{sum(bool(row['dropped_anchor_terms']) for row in pair_rows)}` pairs lose at least one extracted anchor. These are preservation diagnostics: losing an anchor is a concrete text-level risk, while preserving one does not prove semantic correctness.",
            "",
            f"Frame changes (keyword/question/imperative): `{dict(frame_changes)}`. Operator-profile changes: `{len(operator_changes)}` distinct input→rewrite transitions across `{sum(operator_changes.values())}` pairs. The full pair artifact retains the exact text for every transition.",
            "",
            "Anchor-loss examples:",
            "",
            markdown_table(loss_examples, ["variant_role", "input_query", "rewritten_query", "text_class", "added_terms", "dropped_anchors", "repeated_phrases"]),
            "",
            "## Repeated template language and textual pollution",
            "",
            f"`{sum(bool(row['repeated_phrases']) for row in pair_rows)}` pairs contain repeated contiguous phrases inside the rewrite. Phrases reused across at least three distinct input queries are evidence of template leakage or boilerplate reuse, especially when the phrase is not present in the corresponding input.",
            "",
            markdown_table(phrase_rows[:50], ["phrase", "distinct_inputs", "occurrences", "roles"]),
            "",
            "Examples with repeated text inside the rewrite:",
            "",
            markdown_table(pollution_examples, ["variant_role", "input_query", "rewritten_query", "text_class", "added_terms", "dropped_anchors", "repeated_phrases"]),
            "",
            f"Repeated-phrase rows are `{sum(bool(row['repeated_phrases']) for row in pair_rows)}/{len(pair_rows)}`. Reused phrases and the exact text pairs below are the evidence to review; no external or judge-derived quality score is assigned.",
            "",
            "## Focused expansion examples",
            "",
            markdown_table(expansion_examples, ["variant_role", "input_query", "rewritten_query", "text_class", "added_terms", "dropped_anchors", "repeated_phrases"]),
            "",
            "Example selection criterion: added content terms with no extracted anchor loss and no repeated phrase. The examples are shown as text pairs without assigning a quality score.",
            "",
            "## Conclusions drawn after the data-only pass",
            "",
            "These conclusions are generated from the paired text measurements above after the data-only artifacts were written and inspected. They are not judge labels.",
            "",
            *conclusions,
            "",
            "## External baselines used",
            "",
            "The external baseline is methodological, not a local judge score: query reformulation work separates semantic preservation from downstream retrieval usefulness, and recommends inspecting both content retention and the retrieval dimensions introduced by a rewrite.",
            "",
            "- McQueen, fully specified query rewriting and entity preservation: https://aclanthology.org/2022.emnlp-main.320/",
            "- Search-Oriented Conversational Query Editing (EdiRCS), overlap and search-oriented editing: https://aclanthology.org/2023.findings-acl.256/",
            "- ConvGQR, reformulation versus downstream search usefulness: https://aclanthology.org/2023.acl-long.274",
            "- CONQRR, length-aware conversational query rewriting: https://aclanthology.org/2022.emnlp-main.679.pdf",
            "- ReDI, decomposition and interpretation for search queries: https://arxiv.org/html/2509.06544v4",
            "- MiniELM, rewrite relevance/diversity evaluation: https://arxiv.org/html/2501.18056v2",
            "- Stanford IR ranked retrieval evaluation: https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-of-ranked-retrieval-results-1.html",
            "",
            "## Artifacts",
            "",
            f"- All input/rewrite text pairs: `{PAIR_PATH}`",
            f"- All unique input query rows: `{INPUT_PATH}`",
            f"- This report: `{REPORT_PATH}`",
            f"- Machine-readable summary: `{JSON_PATH}`",
        ]
    ) + "\n"


def main() -> None:
    """Load text, produce matched pairs, analyze content, and write artifacts."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    runs = pl.read_parquet(EXPORT_DIR / "search_runs.parquet").select(["run_key", "query"])
    inputs = runs.unique(subset=["run_key"], keep="first").rename({"query": "input_query"})
    variants = pl.read_parquet(EXPORT_DIR / "query_variants.parquet").select(
        ["variant_id", "run_key", "variant_role", "query_text", "executed"]
    )
    pairs = (
        variants.join(inputs, on="run_key", how="left")
        .rename({"query_text": "rewritten_query"})
        .select(["run_key", "variant_id", "variant_role", "executed", "input_query", "rewritten_query"])
    )
    if pairs.filter(pl.col("input_query").is_null() | pl.col("rewritten_query").is_null()).height:
        raise RuntimeError("At least one rewrite pair has missing input or rewritten text")
    inputs.write_parquet(INPUT_PATH, compression="zstd")
    pairs.write_parquet(PAIR_PATH, compression="zstd")
    pair_rows = [analyze_pair(row) for row in pairs.to_dicts()]
    role_rows, added_by_role = aggregate_pairs(pair_rows)
    phrase_rows = phrase_reuse(pair_rows)
    loss_examples = example_rows(pair_rows, lambda row: bool(row["dropped_anchor_terms"]), 25)
    pollution_examples = example_rows(pair_rows, lambda row: bool(row["repeated_phrases"]), 25)
    expansion_examples = example_rows(
        pair_rows,
        lambda row: row["text_class"] in {"focused_expansion", "broad_expansion"}
        and not row["dropped_anchor_terms"]
        and not row["repeated_phrases"],
        25,
    )
    conclusions = derive_conclusions(pair_rows, role_rows, phrase_rows)

    report = build_report(
        inputs,
        pairs,
        pair_rows,
        role_rows,
        added_by_role,
        phrase_rows,
        loss_examples,
        pollution_examples,
        expansion_examples,
        conclusions,
    )
    summary = {
        "input_rows": inputs.height,
        "inputs_with_rewrites": pairs.get_column("run_key").n_unique(),
        "inputs_without_rewrites": inputs.height - pairs.get_column("run_key").n_unique(),
        "pair_rows": pairs.height,
        "class_counts": dict(Counter(row["text_class"] for row in pair_rows)),
        "role_summary": role_rows,
        "top_added_terms": counter_rows(Counter(term for row in pair_rows for term in row["added_content_terms"]), "term", 100),
        "top_dropped_terms": counter_rows(Counter(term for row in pair_rows for term in row["dropped_content_terms"]), "term", 100),
        "reused_phrases": phrase_rows[:100],
        "anchor_loss_examples": loss_examples,
        "pollution_examples": pollution_examples,
        "focused_expansion_examples": expansion_examples,
        "conclusions": conclusions,
        "method": "direct query-text comparison; no judge scores, embeddings, provider data, or result metadata",
        "pair_artifact": str(PAIR_PATH),
        "input_artifact": str(INPUT_PATH),
    }
    REPORT_PATH.write_text(report, encoding="utf-8")
    JSON_PATH.write_text(json.dumps(jsonable(summary), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {REPORT_PATH}, {JSON_PATH}, {PAIR_PATH}, and {INPUT_PATH}")


if __name__ == "__main__":
    main()
