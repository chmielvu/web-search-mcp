"""Plain-English descriptions for the analytics explorer tabs and database objects."""

from __future__ import annotations

# ── Plain-English tab descriptions ───────────────────────────────────────────
# These are shown at the top of each tab so you always know what you're looking at.

_TAB_DESCRIPTIONS: dict[str, str] = {
    "events": (
        "Every event the server fires — searches, rewrites, reranks, fetches, and cache lookups. "
        "Sorted newest first (up to 200 rows). "
        "Use the search box to filter by query text, tool name, or provider."
    ),
    "cache": (
        "'Hits' = a cached answer was reused (fast, no API call). "
        "'Misses' = fresh results had to be fetched from a search provider. "
        "Higher hit rates mean lower latency and lower API costs. "
        "'Avg Similarity' shows how close the cached query was to the new one."
    ),
    "providers": (
        "Which search backends were queried (Brave, SearXNG, Tavily, Gemini, …), "
        "how many result rows they contributed, and their average quality score. "
        "'Provider Overlap' counts how many backends agreed on the same result — "
        "higher overlap generally means higher confidence."
    ),
    "errors": (
        "Failures, timeouts, and exceptions, grouped by error type so you can spot recurring problems. "
        "A high 'Occurrences' count on one error type means something needs attention. "
        "Click column headers to sort."
    ),
    "evals": (
        "Automated benchmark results. Each row is one eval suite run against a specific tool. "
        "'Passes' = the server met the expected behavior for that test case. "
        "'Avg Score' is 0.0–1.0 where 1.0 is a perfect result."
    ),
    "schema": (
        "Every table and view that exists in the analytics DuckDB file. "
        "Each row is one column. Use 'Type' to distinguish raw tables from derived views. "
        "The 'What it stores' column explains the purpose of the table or view in plain English."
    ),
}

# ── Human-readable descriptions for every object in the DB ───────────────────

_OBJECT_DESCRIPTIONS: dict[str, str] = {
    # Raw tables
    "search_events": (
        "The raw event log. Every telemetry event the MCP server records is "
        "appended here as a row with a JSON payload."
    ),
    "eval_runs": (
        "Metadata for each eval suite run — which suite was used, who ran it, "
        "which dataset, and any notes."
    ),
    "eval_cases": (
        "Individual test cases. Each row is one query with an expected behavior "
        "and a link to the search run that was evaluated."
    ),
    "eval_observations": (
        "The verdict (pass/fail) and numeric quality score for each test case. "
        "One observation per case per eval run."
    ),
    "llm_quality_scores": (
        "Quality scores produced by an LLM acting as a judge. "
        "One row per score dimension per eval case."
    ),
    "eval_tool_calls": ("Every tool call made during an eval case execution, with its payload."),
    "eval_candidate_sets": (
        "The set of candidate results recorded at each stage of the pipeline during an eval run."
    ),
    "eval_scores": ("Numeric metric scores per eval case — precision, recall, etc."),
    "eval_judge_calls": (
        "Individual LLM judge invocations. Each row captures the judge model, "
        "the score it gave, and the full payload."
    ),
    "eval_failures": (
        "Eval cases that failed outright (e.g. the tool threw an exception). "
        "Each row has a failure code explaining what went wrong."
    ),
    "analytics_sync_state": (
        "Tracks MotherDuck sync state — when the last sync happened and "
        "how many rows were transferred."
    ),
    # Derived views
    "vw_events": (
        "Enriched version of the raw event log. Provider name and run key are "
        "coalesced from multiple possible fields so they're always filled in."
    ),
    "vw_quality_events": (
        "Events with their JSON result blobs extracted into named columns. "
        "Use this view to inspect individual search results, rewrites, and sources."
    ),
    "vw_run_timeline": (
        "One row per search run. Shows how many events fired at each stage "
        "(rewrite, rerank, fetch, answer) so you can trace what happened."
    ),
    "vw_provider_results": (
        "One row per search result returned by a provider. "
        "Includes title, URL, snippet, domain, and quality score."
    ),
    "vw_branch_candidates": (
        "When the orchestrator runs multiple query branches in parallel, "
        "each branch's results are recorded here — one row per result per branch."
    ),
    "vw_cache_lookups": (
        "Every time the cache was consulted. Shows hit/miss, how similar the "
        "cached query was (similarity score), its age, and its TTL."
    ),
    "vw_cache_stores": (
        "Every time a result was written into the cache. Shows how large the "
        "stored response was and whether metadata was included."
    ),
    "vw_middleware_events": (
        "Rate limiting and expensive-tool gate events. Shows which tool was "
        "rate-limited, which bucket it hit, and how long it waited."
    ),
    "vw_session_activity": (
        "Session lifecycle events — when sessions start, what tools they use, and when they expire."
    ),
    "vw_content_events": (
        "Web page content extraction events. Shows whether each page was "
        "successfully classified, what extraction method was used, and how large the result was."
    ),
    "vw_error_events": (
        "All error, timeout, and failure events. Includes error type, "
        "HTTP status code if applicable, and the tool/provider that failed."
    ),
    "vw_eval_case_timeline": (
        "Each eval test case joined to its search run timeline. "
        "Shows how many events fired during the evaluated run."
    ),
    "vw_eval_candidate_survival": (
        "How many candidate result URLs survived each stage of the pipeline "
        "for a given eval case. Useful for spotting where results get dropped."
    ),
    "vw_eval_provider_quality": (
        "Quality metrics for each provider, aggregated per eval suite. "
        "Shows passes, fails, and average score per tool."
    ),
    "vw_eval_fetch_quality": (
        "Fetch and content quality per eval case — which fetch backend was used, "
        "whether it succeeded, and how much text was extracted."
    ),
    "vw_eval_pass_rate": (
        "Overall pass rate per eval suite. Also shows how many cases cleared "
        "the judge's 0.7 quality score threshold."
    ),
}
