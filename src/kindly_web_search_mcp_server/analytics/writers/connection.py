"""Shared connection helpers for DuckDB writers.

Single source of truth for opening the analytics DuckDB file, applying
schema migrations, and loading optional extensions (VSS, FlockMTL).

FlockMTL bootstrap is split across three functions:

  - `ensure_flockmtl_loaded(connection)` — `INSTALL` + `LOAD` only.
    Network-bound; does NOT touch the user database file; safe to call
    outside `_LOCK`.

  - `ensure_flockmtl_resources(connection)` — `CREATE MODEL`/`CREATE PROMPT`
    DDL + writes to the `flockmtl_resources` metadata table. Writes to the
    user database; call inside `_LOCK` alongside other DDL.

  - `ensure_flockmtl(connection)` — convenience wrapper that does both.
    For callers that don't hold `_LOCK` (e.g. `web-search-cli doctor`).

All MODEL and PROMPT DDL is inlined as Python constants (no SQL file
round-trip) so the prompt templates are greppable from Python and unit
testable. The corresponding SQL lives in `duckdb_data/flockmtl/setup.sql`
for reference / manual CLI replay.

Per-connection state: DuckDB extensions and the MODEL/PROMPT catalog are
per-connection, not process-global. Both functions must be called on the
specific `connection` that will use them.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import duckdb

from ...settings import settings

_LOCK = threading.Lock()

# Process-level install lock and success flag. INSTALL writes to
# DuckDB's local extension catalog (~/.duckdb/) which is shared across
# processes; running it concurrently from multiple threads in this
# process races on DuckDB's per-connection catalog lock. We serialize
# within the process and cache success. A failure is not cached, so
# a later caller can retry after the network becomes reachable.
_FLOCKMTL_INSTALL_LOCK = threading.Lock()
_FLOCKMTL_INSTALLED: bool = False

# FlockMTL community-extension name. v0.4.0 renamed `flockmtl` → `flock`
# (per https://github.com/dais-polymtl/flock README). Some forks still
# publish under the old name; override via settings if needed.
FLOCKMTL_EXTENSION_NAME = "flock"

logger = logging.getLogger(__name__)


def _install_flockmtl_once() -> bool:
    """INSTALL the flock extension exactly once per process.

    INSTALL writes to DuckDB's local extension catalog at `~/.duckdb/`,
    a file-based shared resource. Running INSTALL concurrently from
    multiple threads within this process races on DuckDB's per-
    connection catalog lock. We serialize at the process level via
    `_FLOCKMTL_INSTALL_LOCK` and cache success for the lifetime of
    this process. Cross-process safety relies on DuckDB's own file
    locking of the extension catalog.

    Uses a dedicated short-lived in-memory connection (`:memory:`) for
    the install, because install only writes to the local extension
    catalog, not to any user database. The caller passes nothing.

    A failure is NOT cached, so a later caller can retry once the
    network is reachable again.

    Returns True if INSTALL succeeded (or was previously successful in
    this process). Returns False if INSTALL failed on this call.
    """
    global _FLOCKMTL_INSTALLED
    if _FLOCKMTL_INSTALLED:
        return True
    with _FLOCKMTL_INSTALL_LOCK:
        if _FLOCKMTL_INSTALLED:
            return True
        connection = duckdb.connect(":memory:")
        try:
            connection.execute(f"INSTALL {FLOCKMTL_EXTENSION_NAME} FROM community")
            _FLOCKMTL_INSTALLED = True
            return True
        except Exception:
            logger.warning(
                "FlockMTL INSTALL failed (network unavailable?); later callers may retry",
                exc_info=True,
            )
            return False
        finally:
            try:
                connection.close()
            except Exception:
                pass


# Mistral models — differentiate fast vs quality judges
# Both use Mistral's OpenAI-compatible API endpoint
_MISTRAL_QUALITY_MODEL = "mistral-small-2506"
_MISTRAL_FAST_MODEL = "ministral-3b-2512"

# FlockMTL DDL syntax (verified against the installed extension via smoke
# test):
#   CREATE MODEL('name', 'underlying_model_id', 'provider')
#   CREATE PROMPT('name', 'template with {{placeholders}}')
# `IF NOT EXISTS` is NOT supported — the parser expects `(` immediately
# after `MODEL` / `PROMPT`. Idempotency is achieved by catching the
# `CatalogException` raised when the resource already exists.
_FLOCKMTL_MODEL_DDL: tuple[tuple[str, str, str], ...] = (
    # (model_name, underlying_model_id, provider)
    ("judge_fast", _MISTRAL_FAST_MODEL, "openai"),
    ("judge_quality", _MISTRAL_QUALITY_MODEL, "openai"),
)

_FLOCKMTL_PROMPTS: tuple[tuple[str, str], ...] = (
    (
        "judge_run_overview",
        """You are a fair judge assistant tasked with providing clear, objective feedback based on specific criteria, ensuring each assessment reflects the absolute standards set for performance.

###Task Description:
An instruction and a whole-run digest of evidence are given.
1. Write a detailed feedback that assesses the run holistically, strictly based on the given score rubric. Cite the specific fields from the run digest (e.g. final_result_count, branch errors, provider overlap, rerank stage summaries) that drove your verdict.
2. After writing the feedback, emit a verdict as JSON.
3. The output format MUST be exactly: "Feedback: <your reasoning> [RESULT] <json>".
4. Do not generate any other opening, closing, or explanation.

###Score Rubrics:
- good: Final results directly address the research_goal across distinct sources with no obvious gaps, redundancy, or branch errors. The digest shows a coherent retrieval -> rerank -> final pipeline.
- mixed: Some relevant final results, but the digest shows at least one weak point (e.g. low final_result_count, partial branch errors, redundant rewrites, or a rerank stage that degraded) - the run partially succeeded.
- bad: Empty final results, off-topic results against the research_goal, critical branch/provider failures, or a rerank pipeline that destroyed relevant candidates. The digest shows the run did not deliver.

###Scope note:
NO numeric reranker scores (llm_raw_score, cross_encoder_raw, fused_score, hybrid_rrf_score, final_score) are provided - judge the run on structural / positional evidence (ranks, titles, links, counts, branch summaries, rerank stage names and counts) alone, not on whether the overview agrees with the reranker's own scores.
###Evidence:
<run_digest>
{{run_digest}}

###Feedback:
""",
    ),
    (
        "judge_intent_coherence",
        """You are a fair judge assistant tasked with providing clear, objective feedback based on specific criteria, ensuring each assessment reflects the absolute standards set for performance.

###Task Description:
An instruction and evidence about a single search request and its rewritten variants are given.
1. Write a detailed feedback that assesses whether the parsed intent (with confidence) is a faithful interpretation of the query AND the research_goal, and whether the rewrites are consistent with that intent.
2. After writing the feedback, emit a verdict as JSON.
3. The output format MUST be exactly: "Feedback: <your reasoning> [RESULT] <json>".
4. Do not generate any other opening, closing, or explanation.

###Score Rubrics:
- coherent: The parsed intent captures both the query's surface meaning and the research_goal's deeper purpose; the rewrites target that same intent.
- partially_coherent: The intent captures one of {query surface, research_goal} but not both, OR the rewrites drift from the intent (some target a different facet).
- incoherent: The intent misreads the query or contradicts the research_goal, OR the rewrites are unrelated to the stated intent.

###Evidence:
query: {{query}}
research_goal: {{research_goal}}
intent: {{intent}}
understanding_confidence: {{understanding_confidence}}
rewritten_queries:
{{rewrites}}

###Feedback:
""",
    ),
    (
        "judge_rewrite_coverage",
        """You are a fair judge assistant tasked with providing clear, objective feedback based on specific criteria, ensuring each assessment reflects the absolute standards set for performance.

###Task Description:
An instruction and evidence about an original query and its four rewritten variants are given.
1. Write a detailed feedback that assesses how many distinct retrieval FACETS (semantic angles, not just paraphrases) the four variants cover collectively.
2. After writing the feedback, emit a verdict as JSON.
3. The output format MUST be exactly: "Feedback: <your reasoning> [RESULT] <json>".
4. Do not generate any other opening, closing, or explanation.

###Score Rubrics:
- 4: All four variants target clearly distinct facets (e.g. brand, spec, comparison, tutorial).
- 3: Three distinct facets, one variant redundant with another.
- 2: Two distinct facets; the other two are paraphrases or near-duplicates.
- 1: Redundant - most or all variants are paraphrases of the original; no new retrieval angle is opened.

###Evidence:
query: {{query}}
research_goal: {{research_goal}}
variants (with intended strategy):
{{variants}}

###Feedback:
""",
    ),
    (
        "judge_rerank_improvement",
        """You are a fair judge assistant tasked with providing clear, objective feedback based on specific criteria, ensuring each assessment reflects the absolute standards set for performance.

###Task Description:
An instruction and positional evidence about a single rerank stage are given (ranks before vs after; survival flags). NO numeric reranker scores (llm_raw_score, cross_encoder_raw, fused_score, hybrid_rrf_score, final_score) are provided - you must judge reordering on positional / semantic merit alone, not on whether it agrees with the reranker's own scores.
1. Write a detailed feedback that assesses whether the reordering for this stage improved topical alignment with the query, neutral (no measurable effect), or degraded it (relevant candidates pushed down or dropped).
2. After writing the feedback, emit a verdict as JSON.
3. The output format MUST be exactly: "Feedback: <your reasoning> [RESULT] <json>".
4. Do not generate any other opening, closing, or explanation.

###Score Rubrics:
- improved: Top-of-list after the stage contains candidates more on-topic than the top before; relevant links survived; ordering makes semantic sense.
- neutral: Order changed little for relevance (a few shuffles, no meaningful reordering); or the surviving set is comparable quality to before.
- degraded: Top-of-list after contains less relevant candidates than before; a relevant survivor was pushed below off-topic candidates.

###Evidence:
query: {{query}}
stage: {{stage}}
ranks before:
{{before}}
ranks after:
{{after}}

###Feedback:
""",
    ),
    (
        "judge_result_quality",
        """You are a fair judge assistant tasked with providing clear, objective feedback based on specific criteria, ensuring each assessment reflects the absolute standards set for performance.

###Task Description:
An instruction and evidence about a single search result are given. NO numeric reranker scores (final_score, llm_raw_score, cross_encoder_raw, fused_score, hybrid_rrf_score) are provided - you must judge quality on the title/snippet text alone, not on whether the reranker agreed with you.
1. Write a detailed feedback that assesses (a) whether the result matches the search intent and research_goal, and (b) how informative the snippet is.
2. After writing the feedback, emit a verdict as JSON.
3. The output format MUST be exactly: "Feedback: <your reasoning> [RESULT] <json>".
4. Do not generate any other opening, closing, or explanation.

###Score Rubrics:
- intent_match YES: The title+snippet indicate the result directly addresses the query's intent AND research_goal.
- intent_match NO: The result is off-topic, or addresses only a tangentially related sub-topic.
- informativeness 4: Snippet alone conveys a concrete answer or a specific factual claim.
- informativeness 3: Snippet conveys a clear partial answer.
- informativeness 2: Snippet hints at relevance but is too vague to extract a fact.
- informativeness 1: Snippet is boilerplate, navigation, or empty of factual content.

###Evidence:
query: {{query}}
research_goal: {{research_goal}}
intent: {{intent}}
rank: {{rank}}
title: {{title}}
snippet: {{snippet}}

###Feedback:
""",
    ),
    (
        "judge_failure_cause",
        """You are a fair judge assistant tasked with providing clear, objective feedback based on specific criteria, ensuring each assessment reflects the absolute standards set for performance.

###Task Description:
An instruction and evidence about a failed or empty search run are given.
1. Write a detailed feedback that triages the failure: which stage broke (retrieval / rerank / provider), and what category of root cause fits best.
2. After writing the feedback, emit a verdict as JSON.
3. The output format MUST be exactly: "Feedback: <your reasoning> [RESULT] <json>".
4. Do not generate any other opening, closing, or explanation.

###Score Rubrics:
- no_results: Providers returned an empty candidate set for all branches.
- irrelevant_sources: Candidates were returned but on a different topic than the query/research_goal.
- rerank_error: A rerank stage errored or dropped too many relevant candidates (input_count high, output_count low, or degraded ordering).
- provider_timeout: One or more providers timed out (error_type indicates a network or provider timeout).
- other: Caller cancelled, unknown exception, or none of the above.

###Worked examples (the rubric in action - examples 1 and 2 were mined from real failure rows; examples 3-5 are placeholders to be replaced when real failures of those modes accumulate):

###Example 1 (real - provider_timeout):
Query: python
Intent: ai_coding_and_infrastructure
Error type: TimeoutError
Providers attempted: brave, ddg, degoog, searxng
Branch errors: brave: 0 results / TimeoutError; ddg: 0 results / -
[RESULT] {"root_cause":"provider_timeout","stage":"retrieval","suggested_fix":"shorten per-provider timeout; fall back to a faster provider; or skip network branches for known-slow intents","confidence":4,"reasoning":"Both providers returned no results; one errored with TimeoutError, indicating provider-side latency rather than query or rerank issues."}

###Example 2 (real - other):
Query: query rewrite prompt best practices LLM search retrieval
Intent: ai_coding_and_infrastructure
Error type: caller cancelled
Providers attempted: (none recorded)
Branch errors: (no branch rows)
[RESULT] {"root_cause":"other","stage":"retrieval","suggested_fix":"check caller cancellation policy; partial results may have been available if the call had completed","confidence":3,"reasoning":"The run was cancelled by the caller before any provider returned results - not a retrieval or rerank fault."}

###Example 3 (placeholder - replace when a real no_results failure accumulates):
Query: obscure internal-tool error code XYZ-9001
Intent: troubleshooting
Error type:
Providers attempted: brave, ddg, searxng
Branch errors: brave: 0 results; ddg: 0 results; searxng: 0 results
[RESULT] {"root_cause":"no_results","stage":"retrieval","suggested_fix":"try broader terms; surface related error-code pages; or admit the query is unanswerable in the current corpus","confidence":3,"reasoning":"All three providers returned zero candidates across all branches - the corpus likely does not contain this niche error code."}

###Example 4 (placeholder - replace when a real irrelevant_sources failure accumulates):
Query: best laptop for coding
Intent: buying_guide
Error type:
Providers attempted: brave, ddg
Branch errors: brave: 8 results / off-topic; ddg: 12 results / on-topic
[RESULT] {"root_cause":"irrelevant_sources","stage":"retrieval","suggested_fix":"tighten the rewrite toward laptop models + coding workload; drop branches returning generic electronics pages","confidence":3,"reasoning":"Most results from the dominant branch were off-topic relative to the research_goal of choosing a coding laptop."}

###Example 5 (placeholder - replace when a real rerank_error failure accumulates):
Query: kubernetes service mesh comparison
Intent: technical_comparison
Error type:
Providers attempted: brave, ddg
Branch errors: brave: 25 results; ddg: 18 results
Rerank stage errors: cross_encoder in=43 out=7 err=RuntimeError
[RESULT] {"root_cause":"rerank_error","stage":"rerank","suggested_fix":"fall back to RRF-only ordering; or retry the cross_encoder stage; or cap candidates lower","confidence":4,"reasoning":"Retrieval produced 43 merged candidates but the cross_encoder rerank stage dropped them to 7 with an error, killing result quality."}

###Evidence:
query: {{query}}
intent: {{intent}}
error_type: {{error_type}}
providers attempted: {{providers}}
branch errors:
{{branch_errors}}

###Feedback:
""",
    ),
)


def _db_path(db_path: str | None = None) -> Path:
    """Resolve the DuckDB file path, falling back to the configured default."""
    return Path(db_path or settings.analytics_duckdb_path)


def _ensure_columns(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
    additions: dict[str, str],
) -> None:
    """Add missing columns to an existing table (idempotent ALTERs)."""
    existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()}
    for column, column_type in additions.items():
        if column not in existing:
            connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column} {column_type}")


def _ensure_flockmtl_resources_table(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the catalog-tracking table for FlockMTL resources.

    FlockMTL has no built-in catalog introspection (`duckdb_models()` and
    `duckdb_prompts()` do not exist), so we persist our own registry of
    what we registered. This table backs `vw_flockmtl_resources` and is
    the single source of truth for what the extension knows about.
    """
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS flockmtl_resources (
            kind         VARCHAR NOT NULL,
            name         VARCHAR NOT NULL,
            definition   VARCHAR NOT NULL,
            registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (kind, name)
        )
        """
    )


def _ensure_flockmtl_secret(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the `__default_openai` FlockMTL secret on this connection.

    FlockMTL's `llm_complete` requires a named secret with provider credentials.
    Uses Mistral's OpenAI-compatible API:
      - name: hardcoded `__default_openai` (matches FlockMTL's lookup)
      - API_KEY: `MISTRAL_API_KEY` env var
      - BASE_URL: `https://api.mistral.ai/v1` (Mistral's OpenAI-compatible endpoint)

    Idempotent: drops any existing `__default_openai` first, then creates.
    Errors are caught and logged — secrets are optional, and the worst case
    is that `llm_complete` returns a runtime error per call.

    Must be called AFTER the flock extension is loaded on this connection,
    because `LOAD flock` registers the `OPENAI` secret type. Calling
    `CREATE SECRET` before LOAD yields `InvalidInputException:
    Secret type 'openai' not found`.
    """
    import os

    api_key = os.environ.get("MISTRAL_API_KEY", "")
    base_url = "https://api.mistral.ai/v1"
    if not api_key:
        logger.debug("MISTRAL_API_KEY not set — FlockMTL llm_complete calls will fail")
        return
    try:
        connection.execute("DROP SECRET IF EXISTS __default_openai")
    except duckdb.Error:
        pass
    safe_key = api_key.replace("'", "''")
    safe_url = base_url.replace("'", "''")
    connection.execute(
        f"CREATE SECRET __default_openai (TYPE OPENAI, API_KEY '{safe_key}', BASE_URL '{safe_url}')"
    )


def ensure_flockmtl_loaded(connection: duckdb.DuckDBPyConnection) -> bool:
    """Load the FlockMTL extension on the given connection (LOAD only).

    INSTALL is intentionally NOT done here: it writes to DuckDB's local
    extension catalog at `~/.duckdb/`, which is a file-based shared
    resource. Running INSTALL from the per-judge thread races with the
    writer thread's INSTALL during schema bootstrap, causing
    `TransactionContext Error: Catalog write-write conflict on create
    with "__default_openai"` and similar catalog-lock contention.

    INSTALL is performed exactly once by `ensure_flockmtl_resources`
    during the schema bootstrap path (which holds `_LOCK`). Subsequent
    connections only need LOAD, which is connection-local and safe.

    Returns True if LOAD succeeded (or the extension was already loaded).
    Returns False when:
      - `settings.flockmtl_enabled` is False
      - LOAD fails (extension not installed yet — caller should run
        `ensure_flockmtl_resources` once first)
    """
    if not settings.flockmtl_enabled:
        return False
    try:
        connection.execute(f"LOAD {FLOCKMTL_EXTENSION_NAME}")
        return True
    except duckdb.Error:
        # LOAD may fail if the extension was never installed. Log and
        # return False so the caller can fall back to ensure_flockmtl_resources.
        logger.debug(
            "FlockMTL LOAD failed (extension not installed?); "
            "caller should run ensure_flockmtl_resources first",
            exc_info=True,
        )
        return False


def ensure_flockmtl_resources(connection: duckdb.DuckDBPyConnection) -> bool:
    """Register FlockMTL MODELs and PROMPTs in the given connection.

    Writes to the user database file (`CREATE MODEL`, `CREATE PROMPT`,
    and the `flockmtl_resources` metadata table). Call this inside the
    `_LOCK` block alongside other DDL.

    Assumes `ensure_flockmtl_loaded(connection)` has already been called
    on this connection — calling this on a connection where the extension
    is not loaded will fail with `Catalog Error: llm_complete does not exist`.

    Returns True on success, False on any failure (extension not loaded,
    DDL error, etc.). Idempotent: re-running on a DB that already has
    the resources is a no-op (caught `CatalogException`).
    """
    if not settings.flockmtl_enabled:
        return False
    try:
        _ensure_flockmtl_resources_table(connection)
        # LOAD is connection-local and idempotent. INSTALL is the
        # bootstrap responsibility (see _install_flockmtl_once) so we
        # don't block here on a cold community catalog. If LOAD fails
        # (extension not installed yet), log and abort — the resource
        # DDL below requires the extension to be loaded.
        try:
            connection.execute(f"LOAD {FLOCKMTL_EXTENSION_NAME}")
        except duckdb.Error as exc:
            logger.warning(
                "FlockMTL LOAD failed during ensure_flockmtl_resources; "
                "resource registration aborted: %s",
                exc,
            )
            return False
        _ensure_flockmtl_secret(connection)
        for name, model_id, provider in _FLOCKMTL_MODEL_DDL:
            safe_id = model_id.replace("'", "''")
            safe_provider = provider.replace("'", "''")
            try:
                connection.execute(f"CREATE MODEL('{name}', '{safe_id}', '{safe_provider}')")
            except duckdb.Error:
                # Model already registered — DuckDB raises a generic
                # `duckdb.Error: Model 'X' already exist.` (not a
                # CatalogException). Catch the broader class.
                pass
            connection.execute(
                "INSERT OR REPLACE INTO flockmtl_resources(kind, name, definition) "
                "VALUES (?, ?, ?)",
                ["model", name, f"{model_id} via {provider}"],
            )
        for name, template in _FLOCKMTL_PROMPTS:
            safe_template = template.replace("'", "''")
            try:
                connection.execute(f"CREATE PROMPT('{name}', '{safe_template}')")
            except duckdb.Error:
                # Prompt already registered — see MODEL note above.
                pass
            connection.execute(
                "INSERT OR REPLACE INTO flockmtl_resources(kind, name, definition) "
                "VALUES (?, ?, ?)",
                ["prompt", name, template],
            )
        logger.info(
            "FlockMTL resources registered: %d models, %d prompts",
            len(_FLOCKMTL_MODEL_DDL),
            len(_FLOCKMTL_PROMPTS),
        )
        return True
    except Exception:
        logger.warning(
            "FlockMTL resource registration failed",
            exc_info=True,
        )
        return False


def ensure_flockmtl(connection: duckdb.DuckDBPyConnection) -> bool:
    """INSTALL once, LOAD on this connection, then register resources.

    Convenience wrapper for ad-hoc callers (e.g. `web-search-cli doctor`)
    that don't hold `_LOCK`. Production callers using schema bootstrap
    should call the helpers individually so the network install runs
    OUTSIDE the writer lock.
    """
    if not _install_flockmtl_once():
        return False
    if not ensure_flockmtl_loaded(connection):
        return False
    return ensure_flockmtl_resources(connection)


__all__ = [
    "_LOCK",
    "_db_path",
    "_ensure_columns",
    "ensure_flockmtl",
    "ensure_flockmtl_loaded",
    "ensure_flockmtl_resources",
    "duckdb",
    "FLOCKMTL_EXTENSION_NAME",
]
