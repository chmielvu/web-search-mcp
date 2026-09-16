"""FlockMTL-backed semantic judgment orchestrator.

Runs FlockMTL prompts on each completed search run and persists
verdicts to the `llm_judgments` table. This is the "judge every search
automatically" path the user asked for — no per-row LLM calls from
views, no surprise API costs on dashboard refresh.

Per-search judge pass fires six facet-decomposed judgments, all through
the SAME two-stage chain (Gemini/Gemma stage 1 -> NanoGPT/DeepSeek-thinking
stage 2); the `judge_quality` / `judge_fast` alias is provenance-only.

  a. `judge_run_overview`     -- 1 call/run; holistic good/mixed/bad
                                + analysis + recommendations
  b. `judge_intent_coherence` -- 1 call/run; intent matches query+goal
  c. `judge_rewrite_coverage` -- 1 call/run; if rewrite_enabled &
                                non-empty rewrites; counts distinct facets
  d. `judge_rerank_improvement` -- 1 call per rerank_stages row;
                                positional only (no reranker scores)
  e. `judge_result_quality`   -- 1 call per final_results row (≤ 15/run);
                                snippet-only, NO reranker scores
  f. `judge_failure_cause`    -- 1 call/run if status != 'success' OR
                                final_count == 0; few-shot triage

Each call to `judge_search_run(run_key)` opens its own short-lived
DuckDB connection (LOADs FlockMTL on it, registers the
`__default_openai` secret), runs the prompts, INSERTs the verdicts,
and closes. Failures are caught and persisted as `status='error'` rows
so the orchestrator never crashes the calling search pipeline.

Cost guard: skips entirely if `settings.flockmtl_enabled` is False.

For search-pipeline callers, use `schedule_judge_search_run(run_key)` --
fire-and-forget on a thread pool, never blocks the search response.

Public surface (preserved verbatim from the pre-split module):

  - ``judge_search_run``
  - ``schedule_judge_search_run``
  - ``shutdown_judge_executor``
  - ``drain_judges``
  - ``_parse_result``
  - ``_build_run_digest``
  - ``_store_judgment_row``
  - ``_BANNED_RERANK_SCORES``
"""

from __future__ import annotations

import logging

from .digest import _BANNED_RERANK_SCORES, _build_run_digest
from .executor import drain_judges, shutdown_judge_executor
from .persistence import _store_judgment_row
from .run import judge_search_run, schedule_judge_search_run
from .stages import _parse_result

logger = logging.getLogger(__name__)

__all__ = [
    "_BANNED_RERANK_SCORES",
    "_build_run_digest",
    "_parse_result",
    "_store_judgment_row",
    "drain_judges",
    "judge_search_run",
    "schedule_judge_search_run",
    "shutdown_judge_executor",
]
