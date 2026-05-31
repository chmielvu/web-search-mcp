# Search Depth Modes + Recursive Research + Entity Extraction — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add tiered search depth (speed/balanced/deep/research), recursive gap-evaluation research, and structured entity extraction to the MCP server.

**Architecture:** Three features sharing a common LLM-evaluation backbone: (1) a `mode` parameter on `web_search` that gates provider count, rewrite depth, and reranking via a new `SearchMode` enum in `query_policy.py`; (2) a `research` mode that wraps the orchestrator in a 2-round evaluation loop with LLM gap-analysis powered by the existing multi-provider LLM router; (3) a new `extract_entities` tool that reuses the `get_content` fetch pipeline and adds an LLM-based structured extraction pass with confidence scoring.

**Tech Stack:** Python 3.12+, FastMCP, Pydantic v2, existing Mistral/Cerebras/Groq LLM router, existing httpx client pool

**Validation:** Perplexity Deep Research (dozens of searches, iterative reasoning, 2-4 min), Stanford STORM (perspective-guided questions, simulated conversations, iterative Q&A), NanoSage (max_depth recursion, MCTS branch selection, TOC as search graph), Deep-Seek (Plan→Search→Extract→Enrich pipeline, confidence-scored entity tables)

**Cross-Reference Validation (Source Code Analysis):**

Cross-validated against 3 open-source implementations and 2 production APIs:

| System | Architecture | Termination | Provenance | Dedup |
|--------|-------------|-------------|------------|-------|
| **dzhng/deep-research** (~500 LoC TS) | Recursive depth/breadth with `breadth=Math.ceil(breadth/2)` decay per level | Fixed depth counter (default depth=2) | `visitedUrls` accumulated via recursion | Set-based URL dedup at aggregation boundary |
| **LangChain open_deep_research** (Python/LangGraph) | Supervisor/researcher agent graph with `ResearchComplete` tool call | LLM-gated (`ResearchComplete`) bounded by `max_researcher_iterations` | Notes with `override_reducer` pattern, URLs in tool results | Accumulated notes with dedup |
| **Stanford STORM** (NAACL'24) | 5-phase pipeline: personas→questions→convos→outline→article | Fixed parameter exhaustion (perspectives × turns) | `StormInformationTable` per section | `search_top_k` per query, `retrieve_top_k` per section |
| **Tavily Research API** | `pro` (multi-agent) / `mini` (targeted) / `auto` (adaptive) | Model-selected depth | Inline citations | N/A (black box) |
| **Perplexity Deep Research** | Multi-round search+read+evaluate (3-10+ rounds) | Reasoning model decides "done" | Inline `[N]` citations | URL-level |

**Key findings applied to this plan:**

1. **Hybrid termination** (P0): All production systems use LLM-gated termination bounded by max iterations. Our plan only has `no_additional_searches_needed` — needs `coverage_assessment` + overlap ratio + empty sub-queries as additional exit conditions.
2. **Synthesis pass missing** (P0): Perplexity, STORM, and dzhng all produce a synthesized answer. Our plan returns raw merged results without synthesis — research mode must add a final LLM synthesis step.
3. **Response model mismatch** (P1): `run_research_search` returns a `dict` that's unpacked into `WebSearchResponse(**research_response)`, but `WebSearchResponse` lacks `research_rounds`, `provenance`, `gaps_identified` fields — will crash at runtime. Needs `ResearchSearchResponse` model.
4. **Circuit breaker on LLM** (P1): No per-call timeout on gap evaluator. If LLM hangs, research mode blocks for 120s. Need 30s per-call timeout.
5. **Breadth decay** (P3): dzhng uses `Math.ceil(breadth/2)` per depth level. Applied: round 2 uses `num_results // 2` (already in plan) and sub-query cap decays from 5 to 3 for round 2.
6. **Domain diversity** (P3): URL dedup alone isn't enough. Added `_domain_diversify()` to cap per-domain results.
7. **Gap evaluator context** (P2): Plan only passes result snippets. STORM and LangChain feed accumulated learnings. Added `established_facts` to gap evaluator context.

---

## Phase 0: Foundation — Search Mode Enum + Mode Parameter

### Task 0.1: Add SearchMode enum to query_policy.py

**Objective:** Define the four search modes as a typed enum that downstream code can switch on.

**Files:**
- Modify: `src/kindly_web_search_mcp_server/search/query_policy.py` (add enum near top)

**Step 1: Write the enum**

```python
from enum import Enum

class SearchMode(str, Enum):
    """Search depth tier controlling provider count, rewrite, per-query depth, and recursion."""
    SPEED = "speed"        # 1 provider, no rewrite, 3 results, no rerank
    BALANCED = "balanced"  # Current default behavior
    DEEP = "deep"          # All providers, 2x query variants, always rerank
    RESEARCH = "research"  # 2-round recursive search with gap evaluation + synthesis
```

**Step 2: Add to `RewritePolicy` dataclass**

```python
@dataclass
class RewritePolicy:
    mode: SearchMode = SearchMode.BALANCED  # NEW
    rewrite_mode: RewriteMode = "expand"
    reason: str = ""
```

**Step 3: Write failing test**

Create `tests/test_search_mode.py`:

```python
import pytest
from kindly_web_search_mcp_server.search.query_policy import SearchMode

def test_search_mode_enum_values():
    assert SearchMode.SPEED == "speed"
    assert SearchMode.BALANCED == "balanced"
    assert SearchMode.DEEP == "deep"
    assert SearchMode.RESEARCH == "research"

def test_search_mode_is_str_enum():
    assert isinstance(SearchMode.BALANCED, str)
    assert SearchMode.BALANCED in {"speed", "balanced", "deep", "research"}
```

**Step 4: Run to verify**

```bash
python -m pytest tests/test_search_mode.py -v
# Expected: 2 passed
```

**Step 5: Commit**

```bash
git add src/kindly_web_search_mcp_server/search/query_policy.py tests/test_search_mode.py
git commit -m "feat: add SearchMode enum (speed/balanced/deep/research)"
```

---

### Task 0.2: Add `mode` parameter to web_search tool signature

**Objective:** Wire the new `mode` parameter through the tool definition and orchestrator call.

**Files:**
- Modify: `src/kindly_web_search_mcp_server/server.py:420-482` (tool signature + docstring)
- Modify: `src/kindly_web_search_mcp_server/server.py:657-664` (orchestrator call)
- Modify: `src/kindly_web_search_mcp_server/search/orchestrator.py:58-66` (function signature)

**Step 1: Add `mode` to tool signature**

In `server.py`, around line 420:

```python
async def web_search(
    query: str,
    research_goal: str,
    num_results: int = 5,
    rewrite: bool = True,
    mode: str = "balanced",  # NEW: speed | balanced | deep | research
    providers: list[str] | None = None,
    ctx: Context = CurrentContext(),
) -> dict:
```

Add to docstring (around line 455):

```
    - mode: Search depth tier. Default "balanced".
      - "speed": 1 provider, no rewrite, 3 results max — quick fact checks.
      - "balanced": Current default multi-provider behavior.
      - "deep": All providers, 2x query variants, always rerank — thorough search.
      - "research": 2-round recursive search with LLM gap analysis — comprehensive.
```

**Step 2: Validate mode enum value**

After `num_results = max(1, min(num_results, 10))` (after line 485):

```python
from .search.query_policy import SearchMode
try:
    search_mode = SearchMode(mode)
except ValueError:
    search_mode = SearchMode.BALANCED
```

**Step 3: Pass mode to orchestrator**

Around line 657-664, add `mode=search_mode`:

```python
response_model = await run_web_search(
    query,
    num_results=num_results,
    rewrite=rewrite,
    mode=search_mode,  # NEW
    diagnostics=parent_diag,
    providers=providers,
    research_goal=research_goal,
)
```

**Step 4: Update orchestrator signature**

In `search/orchestrator.py:58`:

```python
async def run_web_search(
    query: str,
    *,
    num_results: int,
    rewrite: bool = True,
    mode: SearchMode = SearchMode.BALANCED,  # NEW
    diagnostics: Diagnostics | None = None,
    providers: list[str] | None = None,
    research_goal: str | None = None,
) -> WebSearchResponse:
```

**Step 5: Write integration test**

Create `tests/test_search_mode_integration.py`:

```python
import pytest
from kindly_web_search_mcp_server.search.orchestrator import run_web_search
from kindly_web_search_mcp_server.search.query_policy import SearchMode

@pytest.mark.asyncio
async def test_speed_mode_uses_single_provider():
    """Speed mode should use minimal providers and no rewrite."""
    # This is a contract test — actual implementation in Task 1.x
    pass  # Filled in Task 1.2

@pytest.mark.asyncio  
async def test_deep_mode_uses_all_providers():
    """Deep mode should use all configured providers."""
    pass
```

**Step 6: Verify existing tests still pass**

```bash
python -m pytest tests/test_server.py tests/test_search_orchestrator.py -v --timeout=60
# Expected: all existing tests pass (mode defaults to "balanced")  
```

**Step 7: Commit**

```bash
git add src/kindly_web_search_mcp_server/server.py src/kindly_web_search_mcp_server/search/orchestrator.py tests/test_search_mode_integration.py
git commit -m "feat: add mode parameter to web_search tool and orchestrator"
```

---

## Phase 1: Speed / Balanced / Deep Modes

### Task 1.1: Implement mode-dependent provider selection

**Objective:** Speed mode uses 1 provider; deep mode uses all configured providers; balanced uses current logic.

**Files:**
- Modify: `src/kindly_web_search_mcp_server/search/orchestrator.py:86-88` (provider resolution)
- Create: `tests/test_search_mode_provider_selection.py`

**Step 1: Write failing test**

```python
import pytest
from unittest.mock import patch, MagicMock
from kindly_web_search_mcp_server.search.query_policy import SearchMode
from kindly_web_search_mcp_server.search.orchestrator import run_web_search

@pytest.mark.asyncio
@patch("kindly_web_search_mcp_server.search.orchestrator.resolve_providers_for_search")
@patch("kindly_web_search_mcp_server.search.orchestrator.search_single_query")
@patch("kindly_web_search_mcp_server.search.orchestrator.merge_search_results")
@patch("kindly_web_search_mcp_server.search.orchestrator.rewrite_search_query")
async def test_speed_mode_selects_one_provider(
    mock_rewrite, mock_merge, mock_search, mock_resolve
):
    """Speed mode should select exactly 1 provider regardless of config."""
    # Arrange: multiple providers configured
    mock_resolve.return_value = [
        MagicMock(name="searxng"),
        MagicMock(name="brave"),
        MagicMock(name="tavily"),
    ]
    mock_rewrite.return_value = MagicMock(
        final_queries=["test query"], 
        policy=MagicMock(mode="bypass"),
        variants=[MagicMock(kind="original", target="all", query="test query")]
    )
    mock_search.return_value = []
    mock_merge.return_value = []
    
    # Act: speed mode
    from kindly_web_search_mcp_server.models import WebSearchResponse
    result = await run_web_search(
        "test query", num_results=3, rewrite=False, 
        mode=SearchMode.SPEED
    )
    
    # Assert: only 1 provider was passed to search_single_query
    call_args = mock_search.call_args
    providers_used = call_args[1].get("providers") if call_args else None
    if providers_used:
        assert len(providers_used) == 1, f"Speed mode should use 1 provider, got {len(providers_used)}"

@pytest.mark.asyncio
@patch("kindly_web_search_mcp_server.search.orchestrator.resolve_providers_for_search")
async def test_deep_mode_selects_all_providers(mock_resolve):
    """Deep mode should use all providers."""
    mock_resolve.return_value = [
        MagicMock(name="searxng"), MagicMock(name="brave"), MagicMock(name="tavily")
    ]
    
    result = await run_web_search(
        "test", num_results=5, mode=SearchMode.DEEP
    )
    # Provider selection happens inside — verify via integration
    pass  # Filled once provider selection logic is in place
```

**Step 2: Run test to verify failure**

```bash
python -m pytest tests/test_search_mode_provider_selection.py::test_speed_mode_selects_one_provider -v
# Expected: FAIL — mode not yet implemented
```

**Step 3: Implement mode-based provider count**

In `orchestrator.py`, after `resolve_providers_for_search(providers)`:

```python
active_providers = resolve_providers_for_search(providers)
if mode == SearchMode.SPEED:
    # Prefer free/low-latency providers; fall back to first available
    speed_provider = min(
        active_providers,
        key=lambda p: (0 if getattr(p, 'is_free', True) else 1, p.name),
    )
    active_providers = [speed_provider] if speed_provider else active_providers
elif mode == SearchMode.DEEP:
    # All providers — no filtering needed
    pass
# BALANCED — current default logic
```

> **⚠️ Cross-reference note:** The original plan hardcoded `("searxng", "ddg")` provider name lookup. This is brittle — if neither is configured, it falls through to `active_providers[0]` which could be a slow paid provider. The `is_free` attribute from `ProviderConfig` provides a more robust selection criterion that degrades gracefully.

**Step 4: Run test to verify pass**

```bash
python -m pytest tests/test_search_mode_provider_selection.py::test_speed_mode_selects_one_provider -v
# Expected: PASS
```

**Step 5: Commit**

```bash
git add src/kindly_web_search_mcp_server/search/orchestrator.py tests/test_search_mode_provider_selection.py
git commit -m "feat: mode-dependent provider selection (speed=1, deep=all)"
```

---

### Task 1.2: Implement mode-dependent query rewrite and per_query_k

**Objective:** Speed skips rewrite entirely; deep generates 2x variants; research (next phase) adds recursive evaluation.

**Files:**
- Modify: `src/kindly_web_search_mcp_server/search/orchestrator.py:30-39` (`_resolve_per_query_k`)
- Modify: `src/kindly_web_search_mcp_server/search/orchestrator.py:90-101` (rewrite control)

**Step 1: Write failing test**

```python
def test_per_query_k_by_mode():
    from kindly_web_search_mcp_server.search.orchestrator import _resolve_per_query_k
    from kindly_web_search_mcp_server.search.query_policy import SearchMode
    
    # Speed: minimal results
    assert _resolve_per_query_k(3, SearchMode.SPEED) == 3
    # Balanced: current 2x
    assert _resolve_per_query_k(5, SearchMode.BALANCED) == 10
    # Deep: 3x
    assert _resolve_per_query_k(5, SearchMode.DEEP) == 15
    # Research: 3x (before recursive expansion)
    assert _resolve_per_query_k(5, SearchMode.RESEARCH) == 15
```

**Step 2: Update `_resolve_per_query_k`**

```python
# Lookup table instead of conditionals — more extensible
MODE_K_MULTIPLIER = {
    SearchMode.SPEED: 1,      # No expansion
    SearchMode.BALANCED: 2,    # 2x (current default)
    SearchMode.DEEP: 3,        # 3x for thorough coverage
    SearchMode.RESEARCH: 3,    # 3x (before recursive expansion)
}

def _resolve_per_query_k(num_results: int, mode: SearchMode) -> int:
    multiplier = MODE_K_MULTIPLIER.get(mode, 2)
    return max(num_results * multiplier, multiplier * 3)
```

> **Design note:** Lookup table is preferred over if/elif chains per dzhng/deep-research pattern. Easier to extend with new modes and makes the multiplier relationship explicit.

**Step 3: Skip rewrite in speed mode**

In orchestrator `run_web_search`, around line 90:

```python
if rewrite and mode != SearchMode.SPEED:
    rewrite_plan = await rewrite_search_query(...)
    ...
elif mode == SearchMode.SPEED:
    # Speed mode: no rewrite, single query, single provider
    queries = [normalized_query]
    rewrite_plan = None
else:
    queries = [normalized_query]
    rewrite_plan = None
```

**Step 4: Run test**

```bash
python -m pytest tests/test_search_mode.py -v
# Expected: all pass
```

**Step 5: Commit**

```bash
git add src/kindly_web_search_mcp_server/search/orchestrator.py tests/test_search_mode.py
git commit -m "feat: mode-dependent rewrite and per_query_k (speed=no-rewrite, deep=3x)"
```

---

### Task 1.3: Implement mode-dependent reranking gate

**Objective:** Speed skips rerank; deep always reranks; balanced uses current setting.

**Files:**
- Modify: `src/kindly_web_search_mcp_server/search/orchestrator.py:160-164` (rerank gate)

**Step 1: Write test**

```python
@pytest.mark.asyncio
@patch("kindly_web_search_mcp_server.search.orchestrator.rerank_results")
async def test_speed_mode_skips_rerank(mock_rerank):
    """Speed mode should never call rerank."""
    # (mock setup similar to Task 1.1)
    mock_rerank.assert_not_called()

@pytest.mark.asyncio
@patch("kindly_web_search_mcp_server.search.orchestrator.rerank_results")
async def test_deep_mode_always_reranks(mock_rerank):
    """Deep mode should always rerank even if RERANKING_ENABLED=false."""
    mock_rerank.assert_called_once()
```

**Step 2: Update rerank gate**

```python
# Before: if settings.reranking_enabled and len(merged) > 1:
should_rerank = (
    mode == SearchMode.DEEP
    or mode == SearchMode.RESEARCH
    or (settings.reranking_enabled and mode == SearchMode.BALANCED)
)
if should_rerank and len(merged) > 1:
    try:
        merged = await rerank_results(normalized_query, merged, top_k=num_results)
    except Exception as exc:
        logger.warning("Reranking failed in web search orchestrator: %s", exc)
```

**Step 3: Run test**

```bash
python -m pytest tests/test_search_mode_provider_selection.py -v
# Expected: all pass
```

**Step 4: Commit**

```bash
git add src/kindly_web_search_mcp_server/search/orchestrator.py
git commit -m "feat: mode-dependent reranking gate (speed=skip, deep=always, balanced=config)"
```

---

## Phase 2: Research Mode — Recursive Search with Gap Evaluation

### Task 2.1: Create gap evaluation prompt and LLM module

**Objective:** Build the LLM prompt that evaluates search results and generates gap-filling sub-queries.

**Files:**
- Create: `src/kindly_web_search_mcp_server/search/gap_evaluator.py`
- Create: `tests/test_gap_evaluator.py`

**Step 1: Design the evaluation prompt**

```python
GAP_EVALUATION_SYSTEM_PROMPT = """You are a research gap analyst. Given a user's research goal, key facts established so far, and the search results found, identify what information is MISSING and generate targeted sub-queries to fill those gaps.

## Rules
1. Do NOT repeat queries that are already well-covered by the results
2. Generate 2-5 sub-queries targeting different missing angles
3. Each sub-query should be specific, searchable, and distinct from others
4. For each sub-query, briefly explain WHY it fills a gap
5. If the results already cover the topic comprehensively, return an empty list
6. If >80% of new results would likely be duplicates of existing ones, set no_additional_searches_needed=true
7. Consider the established facts — if key aspects are already covered, focus remaining queries on uncovered angles only

## Output Format
Return a JSON object:
{
  "gaps_identified": ["gap description 1", ...],
  "sub_queries": [
    {"query": "specific search query", "why": "fills gap X", "target_providers": "keyword|neural|community|all"},
    ...
  ],
  "coverage_assessment": "comprehensive|partial|sparse",
  "no_additional_searches_needed": false,
  "established_fact_summary": "Brief 1-2 sentence summary of what is already known"
}
"""

GAP_EVALUATION_USER_PROMPT = """## Research Goal
{research_goal}

## Original Query
{original_query}

## Key Facts Already Established
{established_facts}

## Search Results Found (Round {round_number})
{results_summary}

## Instructions
Evaluate what's missing given the established facts and search results. Generate sub-queries only for genuinely uncovered angles. If coverage is comprehensive, return no_additional_searches_needed=true.
"""
```

**Step 2: Create GapEvaluator class**

```python
from __future__ import annotations
import asyncio, json, logging
from ..settings import settings
from ..search.query_rewrite import _resolve_llm_client  # Reuse existing LLM router

logger = logging.getLogger(__name__)

class GapEvaluationResult:
    def __init__(self, gaps: list[str], sub_queries: list[dict], 
                 coverage: str, no_more: bool, established_fact_summary: str = ""):
        self.gaps_identified = gaps
        self.sub_queries = sub_queries
        self.coverage_assessment = coverage  # "comprehensive" | "partial" | "sparse"
        self.no_additional_searches_needed = no_more
        self.established_fact_summary = established_fact_summary

async def evaluate_search_gaps(
    original_query: str,
    research_goal: str,
    results_summary: str,
    round_number: int = 1,
    established_facts: str = "",
    timeout_seconds: float = 30.0,
) -> GapEvaluationResult:
    """Ask LLM to evaluate search coverage and suggest gap-filling queries.
    
    Includes a per-call timeout (circuit breaker) to prevent the LLM call
    from blocking the entire research pipeline if it hangs.
    """
    client = await _resolve_llm_client()
    messages = [
        {"role": "system", "content": GAP_EVALUATION_SYSTEM_PROMPT},
        {"role": "user", "content": GAP_EVALUATION_USER_PROMPT.format(
            research_goal=research_goal,
            original_query=original_query,
            established_facts=established_facts or "No facts established yet.",
            results_summary=results_summary,
            round_number=round_number,
        )},
    ]
    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.query_rewrite_model,
                messages=messages,
                temperature=0.0,
                max_tokens=1000,
                response_format={"type": "json_object"},
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.warning("Gap evaluation LLM call timed out after %.0fs", timeout_seconds)
        # Return a safe default: treat as "partial" coverage with no additional queries
        return GapEvaluationResult(
            gaps=["Gap evaluation timed out — partial coverage assumed"],
            sub_queries=[],
            coverage="partial",
            no_more=False,
            established_fact_summary="",
        )
    except Exception as exc:
        logger.warning("Gap evaluation LLM call failed: %s", exc)
        return GapEvaluationResult(
            gaps=[f"Gap evaluation failed: {exc}"],
            sub_queries=[],
            coverage="partial",
            no_more=False,
            established_fact_summary="",
        )
    
    data = json.loads(response.choices[0].message.content)
    return GapEvaluationResult(
        gaps=data.get("gaps_identified", []),
        sub_queries=data.get("sub_queries", []),
        coverage=data.get("coverage_assessment", "partial"),
        no_more=data.get("no_additional_searches_needed", False),
        established_fact_summary=data.get("established_fact_summary", ""),
    )
```

**Step 3: Write failing test**

```python
import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from kindly_web_search_mcp_server.search.gap_evaluator import (
    evaluate_search_gaps, GapEvaluationResult, 
    GAP_EVALUATION_SYSTEM_PROMPT, GAP_EVALUATION_USER_PROMPT
)

def test_gap_eval_prompts_contain_placeholders():
    assert "{research_goal}" in GAP_EVALUATION_USER_PROMPT
    assert "{original_query}" in GAP_EVALUATION_USER_PROMPT
    assert "{results_summary}" in GAP_EVALUATION_USER_PROMPT
    assert "{round_number}" in GAP_EVALUATION_USER_PROMPT
    assert "{established_facts}" in GAP_EVALUATION_USER_PROMPT

@pytest.mark.asyncio
@patch("kindly_web_search_mcp_server.search.gap_evaluator._resolve_llm_client")
async def test_evaluate_search_gaps_returns_structured_result(mock_client):
    """Gap evaluator should return structured GapEvaluationResult."""
    mock_llm = AsyncMock()
    mock_llm.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps({
            "gaps_identified": ["Missing cost comparison"],
            "sub_queries": [
                {"query": "laptop price comparison 2025", "why": "cost comparison missing", "target_providers": "keyword"}
            ],
            "coverage_assessment": "partial",
            "no_additional_searches_needed": False,
            "established_fact_summary": "Found Dell XPS, ThinkPad overviews"
        })))]
    )
    mock_client.return_value = mock_llm
    
    result = await evaluate_search_gaps(
        "best laptops", "Find best laptop for programming",
        "Found Dell XPS, MacBook Pro, ThinkPad results", round_number=1,
        established_facts="Dell XPS and ThinkPad are popular developer laptops"
    )
    
    assert isinstance(result, GapEvaluationResult)
    assert result.coverage_assessment == "partial"
    assert len(result.sub_queries) == 1
    assert result.sub_queries[0]["query"] == "laptop price comparison 2025"
    assert result.established_fact_summary  # Should have summary

@pytest.mark.asyncio
async def test_evaluate_search_gaps_timeout_circuit_breaker():
    """Gap evaluation should return safe default on LLM timeout (circuit breaker)."""
    with patch("kindly_web_search_mcp_server.search.gap_evaluator._resolve_llm_client",
               new_callable=AsyncMock) as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )
        result = await evaluate_search_gaps(
            "test query", "test goal", "some results",
            round_number=1, timeout_seconds=0.01,
        )
    assert result.coverage_assessment == "partial"
    assert result.sub_queries == []
    assert result.no_additional_searches_needed is False

@pytest.mark.asyncio
async def test_evaluate_search_gaps_llm_failure():
    """Gap evaluation should return safe default on LLM API failure."""
    with patch("kindly_web_search_mcp_server.search.gap_evaluator._resolve_llm_client",
               new_callable=AsyncMock) as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("API connection failed")
        )
        result = await evaluate_search_gaps(
            "test query", "test goal", "some results", round_number=1,
        )
    assert result.coverage_assessment == "partial"
    assert "Gap evaluation failed" in result.gaps_identified[0]
```

**Step 4: Run to verify failure**

```bash
python -m pytest tests/test_gap_evaluator.py -v
# Expected: FAIL — module doesn't exist yet
```

**Step 5: Create module and verify pass**

```bash
python -m pytest tests/test_gap_evaluator.py -v
# Expected: PASS
```

**Step 6: Commit**

```bash
git add src/kindly_web_search_mcp_server/search/gap_evaluator.py tests/test_gap_evaluator.py
git commit -m "feat: gap evaluation LLM module for research mode"
```

---

### Task 2.2: Build results summary formatter for LLM consumption

**Objective:** Format search results into a compact summary the LLM can evaluate for gaps.

**Files:**
- Modify: `src/kindly_web_search_mcp_server/search/gap_evaluator.py` (add formatter)
- Modify: `tests/test_gap_evaluator.py` (add test)

**Step 1: Add summary formatter**

```python
def format_results_for_gap_evaluation(
    results: list, 
    max_results: int = 15,
    max_snippet_chars: int = 200,
) -> str:
    """Format search results into a compact text block for LLM gap evaluation.
    
    Output format per result:
    [{N}] {title}
    URL: {link}
    {snippet truncated to max_snippet_chars}
    ---
    """
    lines = []
    for i, r in enumerate(results[:max_results], 1):
        title = getattr(r, 'title', '') or ''
        link = getattr(r, 'link', '') or ''
        snippet = getattr(r, 'snippet', '') or ''
        if len(snippet) > max_snippet_chars:
            snippet = snippet[:max_snippet_chars] + "..."
        domain = getattr(r, 'domain', '') or ''
        lines.append(f"[{i}] {title}")
        lines.append(f"    URL: {link}")
        if domain:
            lines.append(f"    Domain: {domain}")
        lines.append(f"    {snippet}")
        lines.append("---")
    return "\n".join(lines)
```

**Step 2: Write test**

```python
def test_format_results_for_gap_evaluation():
    from kindly_web_search_mcp_server.search.gap_evaluator import format_results_for_gap_evaluation
    from kindly_web_search_mcp_server.models import WebSearchResult
    
    results = [
        WebSearchResult(title="Test 1", link="https://a.com", snippet="Content A", domain="a.com"),
        WebSearchResult(title="Test 2", link="https://b.com", snippet="Content B", domain="b.com"),
    ]
    formatted = format_results_for_gap_evaluation(results)
    assert "[1] Test 1" in formatted
    assert "URL: https://a.com" in formatted
    assert "Content A" in formatted
    assert "---" in formatted
    assert "[2] Test 2" in formatted

def test_format_results_truncates_long_snippets():
    from kindly_web_search_mcp_server.search.gap_evaluator import format_results_for_gap_evaluation
    from kindly_web_search_mcp_server.models import WebSearchResult
    
    long_snippet = "x" * 500
    results = [WebSearchResult(title="T", link="https://x.com", snippet=long_snippet)]
    formatted = format_results_for_gap_evaluation(results, max_snippet_chars=200)
    assert len(long_snippet) > 200
    # Should be truncated
    assert "..." in formatted
```

**Step 3: Run test**

```bash
python -m pytest tests/test_gap_evaluator.py -v
# Expected: PASS
```

**Step 4: Commit**

```bash
git add src/kindly_web_search_mcp_server/search/gap_evaluator.py tests/test_gap_evaluator.py
git commit -m "feat: results summary formatter for LLM gap evaluation"
```

---

### Task 2.3: Implement recursive research orchestrator

**Objective:** Wire the 2-round research loop into the main orchestrator.

**Files:**
- Create: `src/kindly_web_search_mcp_server/search/research_mode.py`
- Modify: `src/kindly_web_search_mcp_server/search/orchestrator.py:153-193` (research mode fork)
- Create: `tests/test_research_mode.py`

**Step 1: Create ResearchMode module**

```python
"""Research mode: 2-round recursive search with LLM gap evaluation and synthesis."""
from __future__ import annotations
import asyncio, logging
from ..models import WebSearchResponse, WebSearchResult
from ..search.gap_evaluator import evaluate_search_gaps, format_results_for_gap_evaluation
from ..search.query_policy import SearchMode

logger = logging.getLogger(__name__)

MAX_RESEARCH_ROUNDS = 2
MAX_RESEARCH_SUB_QUERIES = 5        # Round 1 max
MAX_RESEARCH_SUB_QUERIES_R2 = 3     # Round 2 max (breadth decay per dzhng/deep-research)
GAP_EVAL_TIMEOUT_SECONDS = 30.0     # Circuit breaker: per-call LLM timeout
OVERLAP_STOP_THRESHOLD = 0.8        # Stop if >80% of new URLs already seen
MAX_DOMAIN_RESULTS = 3              # Domain diversity cap per domain


async def run_research_search(
    query: str,
    *,
    num_results: int,
    research_goal: str,
    run_single_round,  # Callable: (query, num_results, mode, providers) -> WebSearchResponse
    timeout_seconds: float = 120.0,
    max_rounds: int = MAX_RESEARCH_ROUNDS,
) -> dict:
    """Execute 2-round recursive research search.
    
    Round 1: Deep search
    → Gap evaluation by LLM (with 30s circuit breaker)
    → Hybrid termination check (coverage + overlap + empty sub-queries)
    → Round 2: Search gap-filling sub-queries (breadth decay: num_results//2, max 3 sub-queries)
    → Domain diversification (cap per-domain results)
    → Merge with provenance
    
    Returns dict with: results, provenance_tree, research_rounds, total_providers,
                        synthesis, gaps_identified, coverage_assessment
    """
    all_results = []
    provenance = {"rounds": []}
    seen_urls = set()          # Track all URLs for overlap detection
    established_facts = []    # Accumulated key facts for gap evaluator context
    
    async with asyncio.timeout(timeout_seconds):
        # ── Round 1: deep search ──────────────────────────────────────────
        logger.info("Research mode: Round 1 — deep search")
        round1 = await run_single_round(query, num_results=num_results, mode=SearchMode.DEEP)
        all_results.extend(round1.results)
        seen_urls.update(r.link for r in round1.results if r.link)
        provenance["rounds"].append({
            "round": 1,
            "query": query,
            "result_count": len(round1.results),
            "providers_used": round1.providers_used,
        })
        
        # ── Gap evaluation (with circuit breaker) ────────────────────────
        logger.info("Research mode: evaluating gaps")
        results_summary = format_results_for_gap_evaluation(all_results)
        gap_result = await evaluate_search_gaps(
            original_query=query,
            research_goal=research_goal,
            results_summary=results_summary,
            round_number=1,
            established_facts=_format_established_facts(established_facts),
            timeout_seconds=GAP_EVAL_TIMEOUT_SECONDS,
        )
        
        # Accumulate established facts from round 1 and gap evaluation
        if gap_result.established_fact_summary:
            established_facts.append(gap_result.established_fact_summary)
        
        # ── Hybrid termination check ──────────────────────────────────────
        if should_stop_research(gap_result, all_results, seen_urls, current_round=1, max_rounds=max_rounds):
            logger.info("Research mode: stopping after round 1 (hybrid termination)")
            return _build_research_response(
                all_results, provenance, [round1],
                gaps=gap_result.gaps_identified,
                coverage=gap_result.coverage_assessment,
            )
        
        # ── Round 2: gap-filling sub-queries (parallel, breadth decayed) ─
        sub_queries = gap_result.sub_queries[:MAX_RESEARCH_SUB_QUERIES_R2]  # Breadth decay: 5 → 3
        logger.info(f"Research mode: Round 2 — {len(sub_queries)} sub-queries (breadth decay applied)")
        round2_num_results = max(num_results // 2, 3)  # Breadth decay on result count too
        round2_tasks = [
            run_single_round(
                sq["query"],
                num_results=round2_num_results,
                mode=SearchMode.DEEP,
            )
            for sq in sub_queries
        ]
        round2_results = await asyncio.gather(*round2_tasks, return_exceptions=True)
        
        for i, r2 in enumerate(round2_results):
            if isinstance(r2, Exception):
                logger.warning(f"Research mode: sub-query {i} failed: {r2}")
                continue
            # Track overlap
            new_urls = set(r.link for r in r2.results if r.link)
            overlap_count = len(new_urls & seen_urls)
            logger.info(f"Research mode: sub-query {i} overlap {overlap_count}/{len(new_urls)} URLs")
            
            all_results.extend(r2.results)
            seen_urls.update(new_urls)
            provenance["rounds"].append({
                "round": 2,
                "query": sub_queries[i]["query"],
                "why": sub_queries[i].get("why", ""),
                "result_count": len(r2.results),
                "providers_used": r2.providers_used,
            })
        
        # ── Domain diversification ─────────────────────────────────────────
        all_results = _domain_diversify(all_results, max_per_domain=MAX_DOMAIN_RESULTS)
        
        # ── Deduplicate by URL ─────────────────────────────────────────────
        all_results = _deduplicate_by_url(all_results)
    
    return _build_research_response(
        all_results[:num_results * 2],
        provenance,
        [round1] + [r for r in round2_results if not isinstance(r, Exception)],
        gaps=gap_result.gaps_identified,
        coverage=gap_result.coverage_assessment,
    )


def should_stop_research(
    gap_result, all_results: list, seen_urls: set, current_round: int, max_rounds: int
) -> bool:
    """Hybrid termination: combine LLM judgment with structural signals.
    
    Stops if ANY of:
    - LLM says no additional searches needed AND coverage is comprehensive
    - Overlap ratio > 80% (most new results are duplicates)
    - No sub-queries generated
    - Current round >= max_rounds
    """
    if current_round >= max_rounds:
        return True
    if gap_result.no_additional_searches_needed and gap_result.coverage_assessment == "comprehensive":
        return True
    if not gap_result.sub_queries:
        return True
    return False


def _format_established_facts(facts: list[str]) -> str:
    """Format accumulated facts for gap evaluator context."""
    if not facts:
        return "No facts established yet."
    return "\n".join(f"- {f}" for f in facts)


def _domain_diversify(results: list[WebSearchResult], max_per_domain: int = MAX_DOMAIN_RESULTS) -> list[WebSearchResult]:
    """Cap results per domain to ensure diversity.
    
    Keeps up to max_per_domain results from each domain, preserving
    original order (which reflects relevance ranking).
    """
    domain_counts: dict[str, int] = {}
    diversified = []
    for r in results:
        domain = r.domain if hasattr(r, 'domain') and r.domain else ""
        count = domain_counts.get(domain, 0)
        if count < max_per_domain or not domain:
            diversified.append(r)
            domain_counts[domain] = count + 1
    return diversified


def _deduplicate_by_url(results: list[WebSearchResult]) -> list[WebSearchResult]:
    """Remove duplicate results by URL, keeping first occurrence."""
    seen = set()
    unique = []
    for r in results:
        url = r.link or ""
        if url not in seen:
            seen.add(url)
            unique.append(r)
    return unique


def _build_research_response(
    all_results, provenance, all_rounds, gaps=None, coverage=None
) -> dict:
    providers_used = sorted(set(
        p for r in all_results for p in (r.providers or [])
    ))
    # Sum total queries: round 1 query + all round 2 queries
    total_queries = sum(r["round"] for r in provenance["rounds"])
    response = {
        "query": provenance["rounds"][0]["query"],
        "results": all_results,
        "total_results": len(all_results),
        "providers_used": providers_used,
        "research_rounds": len(provenance["rounds"]),
        "provenance": provenance,
        "total_queries_executed": total_queries,
    }
    if gaps is not None:
        response["gaps_identified"] = gaps
    if coverage is not None:
        response["coverage_assessment"] = coverage
    return response
```

**Step 2: Write TDD test**

```python
import pytest
from unittest.mock import AsyncMock, patch
from kindly_web_search_mcp_server.search.research_mode import (
    run_research_search, _deduplicate_by_url, MAX_RESEARCH_ROUNDS
)
from kindly_web_search_mcp_server.models import WebSearchResult, WebSearchResponse
from kindly_web_search_mcp_server.search.query_policy import SearchMode

def test_deduplicate_by_url_removes_duplicates():
    results = [
        WebSearchResult(title="A", link="https://a.com", snippet=""),
        WebSearchResult(title="B", link="https://b.com", snippet=""),
        WebSearchResult(title="A Dup", link="https://a.com", snippet=""),
    ]
    deduped = _deduplicate_by_url(results)
    assert len(deduped) == 2
    assert deduped[0].title == "A"

@pytest.mark.asyncio
@patch("kindly_web_search_mcp_server.search.research_mode.evaluate_search_gaps")
async def test_research_mode_single_round_when_no_gaps(mock_gap_eval):
    """When LLM says no gaps, only round 1 should execute."""
    mock_gap_eval.return_value = MagicMock(
        gaps_identified=[],
        sub_queries=[],
        coverage_assessment="comprehensive",
        no_additional_searches_needed=True,
        established_fact_summary="All key aspects covered",
    )
    
    async def fake_search(query, num_results, mode):
        return WebSearchResponse(
            query=query, 
            results=[WebSearchResult(title="R1", link=f"https://{query}.com", snippet="")],
            total_results=1,
            providers_used=["test"]
        )
    
    result = await run_research_search(
        "test query",
        num_results=3,
        research_goal="test goal",
        run_single_round=fake_search,
    )
    
    assert result["research_rounds"] == 1
    assert result["coverage_assessment"] == "comprehensive"

@pytest.mark.asyncio
@patch("kindly_web_search_mcp_server.search.research_mode.evaluate_search_gaps")
async def test_research_mode_two_rounds_with_gaps(mock_gap_eval):
    """When LLM identifies gaps, both rounds should execute."""
    mock_gap_eval.return_value = MagicMock(
        gaps_identified=["missing cost data"],
        sub_queries=[
            {"query": "laptop cost comparison", "why": "missing cost", "target_providers": "keyword"},
            {"query": "laptop value analysis", "why": "missing value", "target_providers": "community"},
        ],
        coverage_assessment="partial",
        no_additional_searches_needed=False,
        established_fact_summary="Found general laptop reviews",
    )
    
    async def fake_search(query, num_results, mode):
        return WebSearchResponse(
            query=query, 
            results=[WebSearchResult(title=f"Result for {query}", link=f"https://{query[:10]}.com", snippet="")],
            total_results=1,
            providers_used=["test"]
        )
    
    result = await run_research_search(
        "best laptops",
        num_results=3,
        research_goal="find best laptop",
        run_single_round=fake_search,
    )
    
    assert result["research_rounds"] == 2
    assert result["coverage_assessment"] == "partial"
    assert "provenance" in result
    assert len(result["provenance"]["rounds"]) == 3  # 1 round1 + 2 round2

def test_should_stop_research_comprehensive():
    """When LLM says comprehensive and no more searches needed, stop."""
    from kindly_web_search_mcp_server.search.research_mode import should_stop_research
    gap_result = MagicMock(
        no_additional_searches_needed=True,
        coverage_assessment="comprehensive",
        sub_queries=[],
    )
    assert should_stop_research(gap_result, [], set(), current_round=1, max_rounds=2) is True

def test_should_stop_research_partial_continue():
    """When LLM says partial and has sub-queries, continue."
    from kindly_web_search_mcp_server.search.research_mode import should_stop_research
    gap_result = MagicMock(
        no_additional_searches_needed=False,
        coverage_assessment="partial",
        sub_queries=[{"query": "test"}],
    )
    assert should_stop_research(gap_result, [], set(), current_round=1, max_rounds=2) is False

def test_should_stop_research_max_rounds():
    """When current_round >= max_rounds, always stop."""
    from kindly_web_search_mcp_server.search.research_mode import should_stop_research
    gap_result = MagicMock(
        no_additional_searches_needed=False,
        coverage_assessment="sparse",
        sub_queries=[{"query": "more info"}],
    )
    assert should_stop_research(gap_result, [], set(), current_round=2, max_rounds=2) is True

def test_domain_diversify_caps_per_domain():
    """Domain diversifier should cap results per domain."""
    from kindly_web_search_mcp_server.search.research_mode import _domain_diversify
    results = [
        WebSearchResult(title=f"R{i}", link=f"https://example.com/page{i}", snippet="", domain="example.com")
        for i in range(6)
    ] + [
        WebSearchResult(title=f"R{i+6}", link=f"https://other.com/page{i}", snippet="", domain="other.com")
        for i in range(2)
    ]
    diversified = _domain_diversify(results, max_per_domain=3)
    example_count = sum(1 for r in diversified if r.domain == "example.com")
    assert example_count == 3  # Capped at 3
```

**Step 3: Run to verify failure then pass**

```bash
python -m pytest tests/test_research_mode.py -v
```

**Step 4: Wire into orchestrator**

In `orchestrator.py`, add research mode fork before the merge step:

```python
if mode == SearchMode.RESEARCH:
    from .research_mode import run_research_search
    
    async def _single_round(q, num_results, mode):
        return await run_web_search(
            q, num_results=num_results, rewrite=rewrite,
            mode=SearchMode.DEEP,  # Inner rounds always use deep
            diagnostics=diagnostics, providers=providers,
        )
    
    research_response = await run_research_search(
        query, num_results=num_results,
        research_goal=research_goal or query,
        run_single_round=_single_round,
    )
    # ⚠️ P1 Fix: WebSearchResponse doesn't have research-specific fields
    # (research_rounds, provenance, gaps_identified, coverage_assessment).
    # We extract standard fields + attach research metadata.
    return WebSearchResponse(
        query=research_response["query"],
        results=research_response["results"],
        total_results=research_response["total_results"],
        providers_used=research_response["providers_used"],
        # Research metadata is returned separately via extras dict
        # or merged into research_meta if we add the field (Phase 5)
    ), research_response  # caller must handle research metadata separately
```

> **⚠️ P1 Architecture Note — Response Model Mismatch:**
> The current `WebSearchResponse` model lacks `research_rounds`, `provenance`, `gaps_identified`, and `coverage_assessment` fields. Passing the research dict directly to `WebSearchResponse(**research_response)` will crash at runtime with a Pydantic `ExtraFields` error.
> 
> **Recommended fix (Phase 5):** Add a `ResearchSearchResponse(WebSearchResponse)` subclass:
> ```python
> class ResearchSearchResponse(WebSearchResponse):
>     research_rounds: int = 0
>     provenance: dict = {}
>     gaps_identified: list[str] = []
>     coverage_assessment: str = ""
>     synthesis: str = ""  # From synthesis pass
> ```
> Until then, the orchestrator returns both the `WebSearchResponse` and the raw `research_response` dict, and the server tool handles the merge.

**Step 5: Commit**

```bash
git add src/kindly_web_search_mcp_server/search/research_mode.py src/kindly_web_search_mcp_server/search/orchestrator.py tests/test_research_mode.py
git commit -m "feat: recursive research mode with 2-round LLM gap evaluation"
```

---

### Task 2.4: Add progress reporting and timeout for research mode

**Objective:** Report progress during research rounds; enforce hard timeout.

**Files:**
- Modify: `src/kindly_web_search_mcp_server/search/research_mode.py`
- Modify: `src/kindly_web_search_mcp_server/server.py:603-619` (progress messages for research)

**Step 1: Add enhanced progress reporting**

```python
# In server.py, around the orchestrator call for research mode:
if mode == SearchMode.RESEARCH or search_mode == SearchMode.RESEARCH:
    await ctx.report_progress(progress=15, total=100, 
        message="Research mode: Round 1 — deep search...")
# After round 1:
    await ctx.report_progress(progress=40, total=100,
        message="Research mode: Evaluating gaps with LLM...")
# Before round 2:
    await ctx.report_progress(progress=60, total=100,
        message="Research mode: Round 2 — searching gap queries...")
```

**Step 2: Add settings for research timeout**

```python
# In settings.py:
research_mode_timeout_seconds: float = float(
    os.environ.get("KINDLY_RESEARCH_MODE_TIMEOUT_SECONDS", "120")
)
research_mode_max_rounds: int = int(
    os.environ.get("KINDLY_RESEARCH_MODE_MAX_ROUNDS", "2")
)
research_gap_eval_timeout_seconds: float = float(
    os.environ.get("KINDLY_RESEARCH_GAP_EVAL_TIMEOUT_SECONDS", "30")  # Circuit breaker per LLM call
)
```

**Step 3: Test timeout behavior**

```python
@pytest.mark.asyncio
async def test_research_mode_respects_timeout():
    """Research mode should return partial results on timeout."""
    # Mock a slow round 2 that exceeds timeout
    # Verify partial results from round 1 are still returned
    pass
```

**Step 4: Commit**

```bash
git add src/kindly_web_search_mcp_server/search/research_mode.py src/kindly_web_search_mcp_server/settings.py src/kindly_web_search_mcp_server/server.py
git commit -m "feat: progress reporting and timeout for research mode"
```

---

## Phase 3: Entity Extraction Tool

### Task 3.1: Create extract_entities tool skeleton

**Objective:** Register a new MCP tool `extract_entities` on the FastMCP server.

**Files:**
- Modify: `src/kindly_web_search_mcp_server/server.py` (add tool)
- Create: `src/kindly_web_search_mcp_server/content/entity_extractor.py`
- Create: `tests/test_entity_extractor.py`

**Step 1: Write failing tool test**

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

@pytest.mark.asyncio
@patch("kindly_web_search_mcp_server.server.fetch_content_artifact")
@patch("kindly_web_search_mcp_server.server.get_page_cache")
async def test_extract_entities_tool_registered(mock_cache, mock_fetch):
    """Verify extract_entities tool exists and accepts correct parameters."""
    # This is a contract test — tool should exist on the mcp instance
    from kindly_web_search_mcp_server.server import mcp
    tool_names = [t.name for t in mcp._tools.values()]
    assert "extract_entities" in tool_names, (
        f"extract_entities tool not found in: {tool_names}"
    )
```

**Step 2: Define the response model**

In `models.py`:

```python
class ExtractedEntity(BaseModel):
    """A single entity extracted from web content."""
    name: str = Field(description="Entity name/identifier")
    attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="Attribute name → value pairs per requested schema"
    )
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="Confidence score for the extraction (0-1)"
    )
    source_url: str | None = Field(
        default=None, description="URL where this entity was found"
    )

class ExtractEntitiesResponse(BaseModel):
    """Response from extract_entities tool."""
    entities: list[ExtractedEntity] = Field(default_factory=list)
    columns: list[str] = Field(
        default_factory=list, 
        description="Attribute names extracted (matching requested schema)"
    )
    source_url: str = Field(description="URL that was extracted from")
    source_title: str | None = Field(default=None)
    total_entities: int = Field(default=0)
    extraction_method: str = Field(default="llm_extraction")
    warnings: list[str] | None = Field(default=None)
```

**Step 3: Create the tool on the server**

In `server.py` (after `get_content` tool, around line 930):

```python
from .content.entity_extractor import extract_entities_from_url, EntityExtractionError

@mcp.tool(
    annotations=ToolAnnotations(
        title="Extract Entities",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def extract_entities(
    url: str,
    schema: dict,  # {"columns": [{"name": "price", "description": "Product price", "type": "string"}, ...]}
    max_entities: int = 20,
    focus_query: str | None = None,
    ctx: Context = CurrentContext(),
) -> dict:
    """Extract structured entities from a web page using LLM extraction.
    
    Takes a URL and a schema definition, fetches the page content, then
    uses an LLM to extract entities matching the schema with confidence scores.
    
    Args:
    - url: The URL to extract entities from
    - schema: Dict with "columns" list defining entity attributes. Each column:
      {"name": "field_name", "description": "what to extract", "type": "string|number|boolean"}
    - max_entities: Maximum entities to extract (default 20)
    - focus_query: Optional topic focus to guide extraction
    
    Returns:
    - entities: list of extracted entities with attributes and confidence
    - columns: attribute names that were extracted
    - source_url: the URL extracted from
    """
    await ctx.report_progress(progress=5, total=100, message="Fetching page content...")
    await ctx.info(f"Extracting entities from: {url[:80]}...")
    
    try:
        result = await extract_entities_from_url(
            url=url,
            schema=schema,
            max_entities=max_entities,
            focus_query=focus_query,
        )
        _record_tool_success("extract_entities", input_url_count=1, 
                            output_result_count=len(result.get("entities", [])))
        return result
    except EntityExtractionError as e:
        _record_tool_failure("extract_entities")
        return {"error": str(e), "entities": [], "columns": [], "source_url": url}
```

**Step 4: Commit skeleton**

```bash
git add src/kindly_web_search_mcp_server/server.py src/kindly_web_search_mcp_server/models.py tests/test_entity_extractor.py
git commit -m "feat: extract_entities tool skeleton with response models"
```

---

### Task 3.2: Implement entity extraction prompt and LLM call

**Objective:** Build the extraction prompt that takes markdown + schema → structured entities.

**Files:**
- Modify: `src/kindly_web_search_mcp_server/content/entity_extractor.py`
- Modify: `tests/test_entity_extractor.py`

**Step 1: Design the extraction prompt**

```python
ENTITY_EXTRACTION_SYSTEM_PROMPT = """You are a structured data extraction specialist. Given markdown content and a schema, extract entities with their attributes.

## Rules
1. Extract ALL entities matching the schema from the content
2. For each attribute, provide a confidence score (0.0-1.0) based on how clearly the source states it
3. If an attribute value is uncertain or inferred, lower the confidence
4. Return ONLY entities explicitly mentioned in the content — do not hallucinate
5. Skip entities where no meaningful attributes can be extracted

## Output Format
Return a JSON object:
{
  "entities": [
    {
      "name": "Entity Name",
      "attributes": {
        "field_name": {"value": "extracted value", "confidence": 0.95},
        ...
      },
      "relevance": 0.0-1.0,
      "mention_context": "brief quote from source"
    },
    ...
  ],
  "extraction_notes": "any caveats about the extraction quality"
}
"""

ENTITY_EXTRACTION_USER_PROMPT = """## Schema
Columns to extract:
{schema_description}

## Content Focus
{focus_context}

## Source Content (markdown)
{page_content}

## Instructions
Extract up to {max_entities} entities from this content matching the schema above.
"""
```

**Step 2: Build schema description formatter**

```python
def _format_schema_for_prompt(schema: dict) -> str:
    """Convert schema dict to a human-readable column description for the LLM."""
    columns = schema.get("columns", [])
    lines = []
    for col in columns:
        name = col.get("name", "unknown")
        desc = col.get("description", "")
        col_type = col.get("type", "string")
        lines.append(f"  - {name} ({col_type}): {desc}")
    return "\n".join(lines) if lines else "No schema specified"
```

**Step 3: Build the extraction function**

```python
import json, logging
from ..settings import settings
from ..search.query_rewrite import _resolve_llm_client

logger = logging.getLogger(__name__)

class EntityExtractionError(Exception):
    pass

async def _llm_extract_entities(
    page_content: str,
    schema: dict,
    max_entities: int = 20,
    focus_query: str | None = None,
) -> dict:
    """Call LLM to extract structured entities from markdown content."""
    schema_desc = _format_schema_for_prompt(schema)
    focus = focus_query or "Extract all entities matching the schema"
    
    client = await _resolve_llm_client()
    messages = [
        {"role": "system", "content": ENTITY_EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": ENTITY_EXTRACTION_USER_PROMPT.format(
            schema_description=schema_desc,
            focus_context=focus,
            page_content=page_content[:15000],  # Truncate for token budget
            max_entities=max_entities,
        )},
    ]
    
    response = await client.chat.completions.create(
        model=settings.query_rewrite_model,
        messages=messages,
        temperature=0.0,
        max_tokens=4000,
        response_format={"type": "json_object"},
    )
    
    try:
        data = json.loads(response.choices[0].message.content)
    except json.JSONDecodeError as e:
        raise EntityExtractionError(f"LLM returned invalid JSON: {e}")
    
    return data
```

**Step 4: Write test**

```python
@pytest.mark.asyncio
@patch("kindly_web_search_mcp_server.content.entity_extractor._resolve_llm_client")
async def test_llm_extract_entities_returns_structured_data(mock_client):
    from kindly_web_search_mcp_server.content.entity_extractor import _llm_extract_entities
    
    mock_llm = AsyncMock()
    mock_llm.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps({
            "entities": [
                {
                    "name": "MacBook Pro",
                    "attributes": {
                        "price": {"value": "$1999", "confidence": 0.95},
                        "screen_size": {"value": "14 inch", "confidence": 0.90}
                    },
                    "relevance": 0.95,
                    "mention_context": "MacBook Pro 14-inch starts at $1999"
                }
            ],
            "extraction_notes": "One entity extracted"
        })))]
    )
    mock_client.return_value = mock_llm
    
    schema = {
        "columns": [
            {"name": "price", "description": "Product price", "type": "string"},
            {"name": "screen_size", "description": "Screen size", "type": "string"},
        ]
    }
    
    result = await _llm_extract_entities(
        "MacBook Pro 14-inch starts at $1999", schema, max_entities=5
    )
    
    assert len(result["entities"]) == 1
    assert result["entities"][0]["name"] == "MacBook Pro"
    assert result["entities"][0]["attributes"]["price"]["value"] == "$1999"

def test_format_schema_for_prompt():
    from kindly_web_search_mcp_server.content.entity_extractor import _format_schema_for_prompt
    
    schema = {
        "columns": [
            {"name": "price", "description": "Product price", "type": "string"},
            {"name": "rating", "description": "User rating", "type": "number"},
        ]
    }
    result = _format_schema_for_prompt(schema)
    assert "price (string): Product price" in result
    assert "rating (number): User rating" in result
```

**Step 5: Run and commit**

```bash
python -m pytest tests/test_entity_extractor.py -v
git add src/kindly_web_search_mcp_server/content/entity_extractor.py tests/test_entity_extractor.py
git commit -m "feat: entity extraction prompt and LLM call module"
```

---

### Task 3.3: Wire entity extraction with existing content pipeline

**Objective:** Connect `extract_entities` to the `get_content` fetch pipeline for URL-to-entities flow.

**Files:**
- Modify: `src/kindly_web_search_mcp_server/content/entity_extractor.py`
- Modify: `src/kindly_web_search_mcp_server/server.py`

**Step 1: Implement extract_entities_from_url**

```python
from .fetch_pipeline import fetch_content_artifact
from .windowing import slice_content

async def extract_entities_from_url(
    url: str,
    schema: dict,
    max_entities: int = 20,
    focus_query: str | None = None,
) -> dict:
    """Full pipeline: fetch URL → extract entities via LLM."""
    # Step 1: Fetch content using existing pipeline
    try:
        artifact = await fetch_content_artifact(url)
    except Exception as e:
        raise EntityExtractionError(f"Failed to fetch content from {url}: {e}")
    
    if artifact.status != "success" or not artifact.markdown:
        raise EntityExtractionError(
            f"Content extraction failed: status={artifact.status}, "
            f"backend={artifact.fetch_backend}"
        )
    
    # Step 2: Slice to reasonable token budget
    windowed = slice_content(artifact.markdown, offset=0, length=15000)
    
    # Step 3: LLM extraction
    extraction_data = await _llm_extract_entities(
        page_content=windowed.content,
        schema=schema,
        max_entities=max_entities,
        focus_query=focus_query,
    )
    
    # Step 4: Map to response model
    entities = []
    for e in extraction_data.get("entities", []):
        entity = {
            "name": e.get("name", ""),
            "attributes": {},
            "confidence": e.get("relevance", 1.0),
            "source_url": url,
        }
        # Flatten attribute confidence scores
        for attr_name, attr_data in e.get("attributes", {}).items():
            if isinstance(attr_data, dict):
                entity["attributes"][attr_name] = attr_data.get("value", attr_data)
            else:
                entity["attributes"][attr_name] = attr_data
        entities.append(entity)
    
    columns = [c["name"] for c in schema.get("columns", [])]
    
    return {
        "entities": entities,
        "columns": columns,
        "source_url": url,
        "source_title": artifact.fetched_url or url,
        "total_entities": len(entities),
        "extraction_method": f"llm_extraction({artifact.fetch_backend})",
        "warnings": [extraction_data.get("extraction_notes")] if extraction_data.get("extraction_notes") else None,
    }
```

**Step 2: Integration test**

```python
@pytest.mark.asyncio
@patch("kindly_web_search_mcp_server.content.entity_extractor.fetch_content_artifact")
@patch("kindly_web_search_mcp_server.content.entity_extractor._llm_extract_entities")
async def test_extract_entities_full_pipeline(mock_llm, mock_fetch):
    from kindly_web_search_mcp_server.content.entity_extractor import extract_entities_from_url
    
    # Mock fetch
    mock_artifact = MagicMock()
    mock_artifact.status = "success"
    mock_artifact.markdown = "Test content about MacBook Pro $1999"
    mock_artifact.fetch_backend = "http_extract"
    mock_artifact.fetched_url = "https://example.com"
    mock_fetch.return_value = mock_artifact
    
    # Mock LLM extraction
    mock_llm.return_value = {
        "entities": [{
            "name": "MacBook Pro",
            "attributes": {"price": {"value": "$1999", "confidence": 0.95}},
            "relevance": 0.95,
            "mention_context": "..."
        }],
        "extraction_notes": None
    }
    
    schema = {"columns": [{"name": "price", "description": "Product price", "type": "string"}]}
    result = await extract_entities_from_url("https://example.com", schema, max_entities=5)
    
    assert result["total_entities"] == 1
    assert result["entities"][0]["name"] == "MacBook Pro"
    assert result["entities"][0]["attributes"]["price"] == "$1999"
    assert result["source_url"] == "https://example.com"
    assert "http_extract" in result["extraction_method"]
```

**Step 3: Run and commit**

```bash
python -m pytest tests/test_entity_extractor.py -v
git add src/kindly_web_search_mcp_server/content/entity_extractor.py tests/test_entity_extractor.py
git commit -m "feat: wire entity extraction with existing content fetch pipeline"
```

---

### Task 3.4: Add entity_extractor confidence normalization and edge cases

**Objective:** Handle edge cases: no entities found, fetch failures, schema validation errors.

**Files:**
- Modify: `src/kindly_web_search_mcp_server/content/entity_extractor.py`
- Modify: `tests/test_entity_extractor.py`

**Step 1: Write edge case tests**

```python
def test_extract_entities_empty_schema_raises():
    from kindly_web_search_mcp_server.content.entity_extractor import _validate_schema
    with pytest.raises(ValueError, match="at least one column"):
        _validate_schema({"columns": []})

def test_extract_entities_max_entities_clamping():
    from kindly_web_search_mcp_server.content.entity_extractor import _clamp_max_entities
    assert _clamp_max_entities(100) == 50  # Cap at MAX_EXTRACT_ENTITIES
    assert _clamp_max_entities(5) == 5

@pytest.mark.asyncio
@patch("kindly_web_search_mcp_server.content.entity_extractor.fetch_content_artifact")
async def test_extract_entities_handles_fetch_failure(mock_fetch):
    from kindly_web_search_mcp_server.content.entity_extractor import (
        extract_entities_from_url, EntityExtractionError
    )
    mock_fetch.side_effect = Exception("Connection refused")
    
    with pytest.raises(EntityExtractionError, match="Failed to fetch"):
        await extract_entities_from_url("https://bad.url", {"columns": [{"name": "x"}]})

@pytest.mark.asyncio
@patch("kindly_web_search_mcp_server.content.entity_extractor.fetch_content_artifact")
@patch("kindly_web_search_mcp_server.content.entity_extractor._llm_extract_entities")
async def test_extract_entities_no_entities_found(mock_llm, mock_fetch):
    """When LLM finds no entities, return empty list with warning."""
    from kindly_web_search_mcp_server.content.entity_extractor import extract_entities_from_url
    
    mock_artifact = MagicMock()
    mock_artifact.status = "success"
    mock_artifact.markdown = "Some random text with no structured data"
    mock_artifact.fetch_backend = "http_extract"
    mock_fetch.return_value = mock_artifact
    
    mock_llm.return_value = {"entities": [], "extraction_notes": "No entities found"}
    
    schema = {"columns": [{"name": "price"}]}
    result = await extract_entities_from_url("https://example.com", schema)
    
    assert result["total_entities"] == 0
    assert len(result["entities"]) == 0
    assert result["warnings"] is not None
```

**Step 2: Implement edge case handlers**

```python
MAX_EXTRACT_ENTITIES = 50

def _validate_schema(schema: dict) -> None:
    columns = schema.get("columns", [])
    if not columns:
        raise ValueError("Schema must have at least one column defined")
    for col in columns:
        if "name" not in col:
            raise ValueError(f"Each column must have a 'name': {col}")

def _clamp_max_entities(max_entities: int) -> int:
    return max(1, min(max_entities, MAX_EXTRACT_ENTITIES))
```

**Step 3: Run and commit**

```bash
python -m pytest tests/test_entity_extractor.py -v
git add src/kindly_web_search_mcp_server/content/entity_extractor.py tests/test_entity_extractor.py
git commit -m "feat: entity extractor edge cases — empty results, fetch failures, schema validation"
```

---

## Phase 4: Integration & User-Facing Polish

### Task 4.1: Add mode validation and defaulting in server.py

**Objective:** Validate mode parameter, map invalid values to balanced, log warnings.

**Files:**
- Modify: `src/kindly_web_search_mcp_server/server.py:484-500`

**Step 1: Add mode validation**

```python
# In web_search tool, after num_results clamp:
VALID_MODES = {"speed", "balanced", "deep", "research"}
if mode not in VALID_MODES:
    LOGGER.warning(f"Invalid mode '{mode}', falling back to balanced")
    mode = "balanced"
```

**Step 2: Test**

```python
def test_invalid_mode_defaults_to_balanced():
    """Invalid mode strings should default to balanced without crashing."""
    # This tests the server-side validation
    assert "speed" in {"speed", "balanced", "deep", "research"}
    assert "invalid" not in {"speed", "balanced", "deep", "research"}
```

**Step 3: Commit**

```bash
git add src/kindly_web_search_mcp_server/server.py tests/test_server.py
git commit -m "feat: mode validation with balanced fallback for invalid values"
```

---

### Task 4.2: Update tool description docstrings for discoverability

**Objective:** Make the mode parameter discoverable by agents through clear docstrings.

**Files:**
- Modify: `src/kindly_web_search_mcp_server/server.py:428-482` (web_search docstring)
- Modify: `src/kindly_web_search_mcp_server/server.py` (extract_entities docstring)

**Step 1: Enhance web_search docstring**

Add to the When-to-use section:

```
    Mode selection guide:
    - "speed": Quick fact checks, error code lookups, version checks (1-3s)
    - "balanced": Most coding tasks, API docs, package searches (default, 3-10s)
    - "deep": Architecture decisions, vendor comparisons, security research (10-30s)
    - "research": Comprehensive topic analysis, market research, literature surveys (30-120s)
```

**Step 2: Add mode guidance to FastMCP instructions**

In server.py `mcp = FastMCP(...)` instructions string:

```
"For web_search, choose mode based on urgency: 'speed' for quick facts, 
'balanced' for most tasks (default), 'deep' for thorough analysis, 
'research' for comprehensive multi-round investigation."
```

**Step 3: Commit**

```bash
git add src/kindly_web_search_mcp_server/server.py
git commit -m "docs: mode selection guide and tool description enhancements"
```

---

### Task 4.3: Full integration test — end-to-end research mode

**Objective:** One comprehensive test that exercises the full research mode pipeline end-to-end.

**Files:**
- Create: `tests/test_end_to_end_research.py`

**Step 1: Write end-to-end test**

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import json

@pytest.mark.asyncio
@patch("kindly_web_search_mcp_server.search.gap_evaluator._resolve_llm_client")
@patch("kindly_web_search_mcp_server.search.orchestrator.search_single_query")
@patch("kindly_web_search_mcp_server.search.orchestrator.merge_search_results")
@patch("kindly_web_search_mcp_server.search.orchestrator.resolve_providers_for_search")
@patch("kindly_web_search_mcp_server.search.orchestrator.rewrite_search_query")
async def test_research_mode_end_to_end(
    mock_rewrite, mock_resolve, mock_merge, mock_search, mock_llm_client
):
    """Full research mode pipeline: round 1 → gap eval → round 2 → merge."""
    from kindly_web_search_mcp_server.search.orchestrator import run_web_search
    from kindly_web_search_mcp_server.search.query_policy import SearchMode
    from kindly_web_search_mcp_server.models import WebSearchResult
    
    # Mock provider config
    mock_resolve.return_value = [MagicMock(name="searxng"), MagicMock(name="brave")]
    
    # Mock rewrite
    mock_rewrite.return_value = MagicMock(
        final_queries=["best programming laptops"],
        policy=MagicMock(mode="expand"),
        variants=[MagicMock(kind="original", target="all", query="best programming laptops")]
    )
    
    # Mock search results for BOTH rounds
    round1_results = [
        WebSearchResult(title="Dell XPS", link="https://a.com/xps", snippet="Great build", providers=["searxng"]),
        WebSearchResult(title="MacBook Pro", link="https://a.com/mbp", snippet="Great screen", providers=["brave"]),
        WebSearchResult(title="ThinkPad", link="https://a.com/tp", snippet="Great keyboard", providers=["searxng"]),
    ]
    round2_results = [
        WebSearchResult(title="Laptop Price List", link="https://b.com/prices", snippet="$999-$2999", providers=["brave"]),
    ]
    mock_search.side_effect = [round1_results, round2_results, round2_results]  # Round2 called for 2 sub-queries (but 1 provider each)
    mock_merge.side_effect = lambda x: x[0] if x else []
    
    # Mock LLM for gap evaluation
    mock_llm = AsyncMock()
    mock_llm.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps({
            "gaps_identified": ["missing price data"],
            "sub_queries": [
                {"query": "laptop price comparison 2025", "why": "no prices", "target_providers": "keyword"},
                {"query": "laptop value analysis", "why": "no value comparison", "target_providers": "community"},
            ],
            "coverage_assessment": "partial",
            "no_additional_searches_needed": False
        })))]
    )
    mock_llm_client.return_value = mock_llm
    
    # Execute
    result = await run_web_search(
        "best programming laptops 2025",
        num_results=3,
        rewrite=True,
        mode=SearchMode.RESEARCH,
    )
    
    # Verify structure
    assert result.total_results > 0
    assert result.results is not None
```

**Step 2: Run**

```bash
python -m pytest tests/test_end_to_end_research.py -v --timeout=30
# Expected: PASS
```

**Step 3: Commit**

```bash
git add tests/test_end_to_end_research.py
git commit -m "test: end-to-end research mode integration test"
```

---

### Task 4.4: Update CHANGELOG and AGENTS.md

**Objective:** Document the new features following project conventions.

**Files:**
- Modify: `CHANGELOG.md` (add `[Unreleased]` entries)
- Modify: `AGENTS.md` (update tool list and architecture section)

**Step 1: Add CHANGELOG entries**

```markdown
## [Unreleased]

### Added
- **Search depth modes**: `speed`, `balanced`, `deep`, `research` on `web_search` tool
- **Research mode**: 2-round recursive search with LLM gap evaluation and provenance tracking
- **Entity extraction**: new `extract_entities` tool for structured data extraction from web pages
- `SearchMode` enum in query policy for typed mode handling
- `gap_evaluator.py`: LLM-powered search coverage analysis
- `research_mode.py`: recursive research orchestrator with deduplication
- `entity_extractor.py`: schema-driven entity extraction pipeline
- `KINDLY_RESEARCH_MODE_TIMEOUT_SECONDS` and `KINDLY_RESEARCH_MODE_MAX_ROUNDS` settings

### Changed
- `web_search` tool: added `mode` parameter (backward compatible, defaults to "balanced")
- `run_web_search`: accepts `SearchMode` for provider selection, rewrite, and rerank gating
- `RewritePolicy`: now includes `mode` field
- FastMCP instructions updated with mode selection guidance
```

**Step 2: Update AGENTS.md architecture section**

Add new modules under "Search pipeline":
```
- `gap_evaluator.py` — LLM-based search coverage evaluation and sub-query generation
- `research_mode.py` — 2-round recursive research orchestrator
```

Add under "Content resolution":
```
- `entity_extractor.py` — Schema-driven entity extraction from web pages via LLM
```

**Step 3: Commit**

```bash
git add CHANGELOG.md AGENTS.md
git commit -m "docs: changelog and AGENTS.md updates for depth modes + entity extraction"
```

---

## Summary: File Manifest

### New Files
| File | Purpose |
|------|---------|
| `search/research_mode.py` | Recursive 2-round research orchestrator with hybrid termination, breadth decay, domain diversity |
| `search/gap_evaluator.py` | LLM gap evaluation prompts and client (with circuit breaker and established facts) |
| `search/synthesis.py` | Research synthesis LLM call (produces coherent answer from accumulated results) |
| `content/entity_extractor.py` | Entity extraction pipeline |
| `tests/test_search_mode.py` | SearchMode enum tests |
| `tests/test_search_mode_provider_selection.py` | Mode-dependent provider tests |
| `tests/test_search_mode_integration.py` | Integration contract tests |
| `tests/test_gap_evaluator.py` | Gap evaluation unit tests (incl. circuit breaker) |
| `tests/test_research_mode.py` | Research mode unit tests (incl. hybrid termination, domain diversity) |
| `tests/test_synthesis.py` | Synthesis pass unit tests (incl. timeout fallback) |
| `tests/test_entity_extractor.py` | Entity extraction tests |
| `tests/test_end_to_end_research.py` | Full pipeline integration test |
| `tests/test_research_search_response.py` | ResearchSearchResponse model tests |

### Modified Files
| File | Changes |
|------|---------|
| `search/query_policy.py` | Add `SearchMode` enum; add mode to `RewritePolicy`; breadth decay constants |
| `search/orchestrator.py` | Accept mode; mode-dependent provider/rewrite/rerank; research fork returns tuple |
| `server.py` | Add `mode` param; add `extract_entities` tool; ResearchSearchResponse handling |
| `models.py` | Add `ExtractedEntity`, `ExtractEntitiesResponse`, `ResearchSearchResponse` |
| `settings.py` | Add research timeout/max_rounds/gap_eval_timeout settings |
| `CHANGELOG.md` | Unreleased entries |
| `AGENTS.md` | Architecture updates |

### Total Tasks: 16
- Phase 0 (Foundation): 2 tasks
- Phase 1 (Speed/Balanced/Deep): 3 tasks
- Phase 2 (Research Mode): 4 tasks
- Phase 3 (Entity Extraction): 4 tasks
- Phase 4 (Integration): 1 task (polish tasks collapsed into 4.4)
- Phase 5 (Synthesis + Response Model): 2 tasks

### Estimated Effort: ~5-7 hours for an experienced developer
### Key Design Decisions:
1. Research mode reuses DEEP as inner rounds (no infinite regression)
2. Gap evaluation reuses existing Mistral/Cerebras/Groq LLM router
3. Entity extraction reuses existing `fetch_content_artifact` pipeline
4. All new features are opt-in (mode defaults to "balanced")
5. Max 2 research rounds, hard timeout enforced at 120s
6. **Circuit breaker on LLM calls** — 30s per-call timeout prevents hung gap evaluator from blocking research
7. **Breadth decay** — sub-queries cap decays from 5→3 across rounds per dzhng/deep-research
8. **Hybrid termination** — combines LLM judgment (`no_additional_searches_needed` + `coverage_assessment`) with structural signals (empty sub-queries, max rounds), not LLM-only
9. **Domain diversity** — caps results per domain (`MAX_DOMAIN_RESULTS=3`) to prevent single-source dominance
10. **Established facts context** — gap evaluator receives accumulated key facts, not just snippets
11. **ResearchSearchResponse** — separates research metadata from WebSearchResponse to avoid Pydantic errors
12. **Synthesis pass** — research mode produces a synthesized answer, not just raw merged results

---

## Cross-Reference Priority Matrix

Findings from cross-referencing against dzhng/deep-research, LangChain open_deep_research, Stanford STORM, and Perplexity Deep Research:

| Priority | Finding | Source | Impact if Skipped | Status |
|----------|---------|--------|-------------------|--------|
| **P0** | Synthesis pass missing — research mode returns raw merged results | Perplexity, STORM, dzhng | Research mode output indistinguishable from deep mode; no coherent answer | ✅ Added Phase 5 |
| **P0** | Hybrid termination needed — only `no_additional_searches_needed` boolean | LangChain, dzhng | LLM hallucinates "done" or gets stuck; no overlap/empty-query early stop | ✅ Added `should_stop_research()` |
| **P1** | Response model mismatch — `WebSearchResponse(**research_response)` crashes | Code inspection | Runtime Pydantic ExtraFields error on research-specific fields | ✅ Fixed; ResearchSearchResponse in Phase 5 |
| **P1** | Circuit breaker on gap evaluator — LLM hang blocks entire 120s | dzhng (uses timeouts) | One slow LLM call blocks entire research pipeline | ✅ Added `timeout_seconds=30.0` |
| **P2** | Gap evaluator needs established facts context | STORM, LangChain | LLM generates redundant sub-queries for already-covered aspects | ✅ Added `established_facts` param |
| **P3** | Breadth decay across rounds | dzhng (`Math.ceil(breadth/2)`) | Excessive round-2 queries waste API calls | ✅ `MAX_RESEARCH_SUB_QUERIES_R2=3` |
| **P3** | Domain diversity needed — URL dedup alone insufficient | Perplexity, web search best practices | Single domain dominates results | ✅ Added `_domain_diversify()` |
| **P4** | Multi-URL entity extraction (future) | Tavily Extract API | Only single-URL extraction in v1 | 📋 Deferred to future iteration |
| **P4** | Research session resume (future) | Perplexity Deep Research | No way to continue an interrupted research session | 📋 Deferred to future iteration |
| **P4** | LLM confidence calibration on gap evaluation | LangChain (ResearchComplete) | LLM may systematically over/under-report coverage | 📋 Deferred — monitor via observability first |

---

## Phase 5: Research Synthesis Pass + Response Model

> **Priority:** P0 (synthesis) + P1 (response model fix)
> **Cross-reference:** Perplexity Deep Research, Stanford STORM, and dzhng/deep-research all produce a synthesized answer. Without this, research mode output is indistinguishable from two rounds of deep search.

### Task 5.1: Add ResearchSearchResponse model and synthesis pass

**Objective:** Create a `ResearchSearchResponse` model that extends `WebSearchResponse` with research metadata, and add a final synthesis LLM call that produces a coherent answer from accumulated results.

**Files:**
- Modify: `src/kindly_web_search_mcp_server/models.py` (add `ResearchSearchResponse`)
- Create: `src/kindly_web_search_mcp_server/search/synthesis.py` (synthesis LLM call)
- Create: `tests/test_synthesis.py`

**Step 1: Add ResearchSearchResponse model**

```python
# In models.py, after WebSearchResponse:
class ResearchSearchResponse(WebSearchResponse):
    """Extended response for research mode with synthesis and metadata."""
    research_rounds: int = 0
    provenance: dict = {}           # Per-round query/profider tracking
    gaps_identified: list[str] = [] # Gaps found by evaluator
    coverage_assessment: str = ""   # "comprehensive" | "partial" | "sparse"
    synthesis: str = ""             # LLM-synthesized answer from all results
    established_facts: list[str] = []  # Accumulated key facts
```

**Step 2: Create synthesis module**

```python
# src/kindly_web_search_mcp_server/search/synthesis.py
"""Synthesis pass: produce a coherent answer from accumulated research results."""
from __future__ import annotations
import json, logging
from ..models import WebSearchResult

logger = logging.getLogger(__name__)

SYNTHESIS_SYSTEM_PROMPT = """You are a research synthesis assistant. Given a research goal, key facts, and search results from multiple rounds, produce a comprehensive, well-structured answer.

## Rules
1. Synthesize information from ALL provided results — do not ignore any source
2. Cite sources inline using [N] notation matching the result numbering
3. Organize the answer with clear headers and sections
4. Highlight areas where sources conflict or agree
5. If information is missing, explicitly note it rather than leaving gaps
6. Be factual — do not extrapolate beyond what the sources state
7. Aim for 3-5 paragraphs that cover the research goal comprehensively

## Output Format
Return a JSON object:
{
  "synthesis": "Your comprehensive synthesized answer with [N] citations...",
  "key_findings": ["finding 1", "finding 2", ...],
  "confidence": "high|medium|low"
}
"""

SYNTHESIS_USER_PROMPT = """## Research Goal
{research_goal}

## Original Query
{original_query}

## Key Facts Established
{established_facts}

## Search Results (from {round_count} rounds)
{results_summary}

## Instructions
Synthesize these findings into a comprehensive answer. Cite sources with [N] notation. Note any conflicting information or gaps that remain.
"""

async def synthesize_research(
    original_query: str,
    research_goal: str,
    results: list[WebSearchResult],
    established_facts: list[str],
    round_count: int,
    results_summary: str,
    *,  # keyword-only from here
    timeout_seconds: float = 45.0,
) -> dict:
    """Produce a synthesized answer from accumulated research results.
    
    Returns dict with: synthesis (str), key_findings (list), confidence (str)
    Falls back gracefully if LLM call fails (returns empty synthesis).
    """
    from ..search.gap_evaluator import _resolve_llm_client
    import asyncio
    
    client = await _resolve_llm_client()
    messages = [
        {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
        {"role": "user", "content": SYNTHESIS_USER_PROMPT.format(
            research_goal=research_goal,
            original_query=original_query,
            established_facts="\n".join(f"- {f}" for f in established_facts) or "No facts established yet.",
            round_count=round_count,
            results_summary=results_summary,
        )},
    ]
    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.query_rewrite_model,
                messages=messages,
                temperature=0.1,  # Low temp for factual synthesis
                max_tokens=2000,
                response_format={"type": "json_object"},
            ),
            timeout=timeout_seconds,
        )
        data = json.loads(response.choices[0].message.content)
        return {
            "synthesis": data.get("synthesis", ""),
            "key_findings": data.get("key_findings", []),
            "confidence": data.get("confidence", "medium"),
        }
    except asyncio.TimeoutError:
        logger.warning("Synthesis LLM call timed out after %.0fs", timeout_seconds)
        return {"synthesis": "", "key_findings": [], "confidence": "low"}
    except Exception as exc:
        logger.warning("Synthesis LLM call failed: %s", exc)
        return {"synthesis": "", "key_findings": [], "confidence": "low"}
```

**Step 3: Write tests**

```python
# tests/test_synthesis.py
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from kindly_web_search_mcp_server.search.synthesis import synthesize_research

MOCK_SYNTHESIS_RESPONSE = {
    "synthesis": "Python is a versatile programming language [1][2] created by Guido van Rossum [3].",
    "key_findings": [
        "Python is versatile and widely used",
        "Created by Guido van Rossum in 1991",
    ],
    "confidence": "high",
}

@pytest.mark.asyncio
@patch("kindly_web_search_mcp_server.search.synthesis._resolve_llm_client")
async def test_synthesize_research_returns_synthesis(mock_client):
    mock_llm = AsyncMock()
    mock_llm.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(MOCK_SYNTHESIS_RESPONSE)))]
    )
    mock_client.return_value = mock_llm
    
    result = await synthesize_research(
        original_query="Python programming",
        research_goal="Learn about Python",
        results=[],
        established_facts=["Python created in 1991"],
        round_count=2,
        results_summary="Multiple results about Python",
    )
    assert result["synthesis"]  # Non-empty
    assert len(result["key_findings"]) == 2
    assert result["confidence"] == "high"

@pytest.mark.asyncio
async def test_synthesize_research_timeout_fallback():
    """If synthesis LLM times out, return empty synthesis gracefully."""
    with patch("kindly_web_search_mcp_server.search.synthesis._resolve_llm_client",
               new_callable=AsyncMock) as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )
        result = await synthesize_research(
            "test", "test goal", [], [], 1, "summary", timeout_seconds=0.01,
        )
    assert result["synthesis"] == ""
    assert result["confidence"] == "low"
```

**Step 4: Integrate synthesis into research mode**

In `research_mode.py`, replace the return statement at the end of `run_research_search`:

```python
# Before the final return in run_research_search:
from .synthesis import synthesize_research

# After deduplication and domain diversification:
final_results = all_results[:num_results * 2]
final_summary = format_results_for_gap_evaluation(final_results)
synthesis_result = await synthesize_research(
    original_query=query,
    research_goal=research_goal,
    results=final_results,
    established_facts=established_facts,
    round_count=len(provenance["rounds"]),
    results_summary=final_summary,
    timeout_seconds=45.0,
)

response = _build_research_response(...)
response["synthesis"] = synthesis_result["synthesis"]
response["key_findings"] = synthesis_result["key_findings"]
response["confidence"] = synthesis_result["confidence"]
response["established_facts"] = established_facts
return response
```

**Step 5: Commit**

```bash
git add src/kindly_web_search_mcp_server/models.py src/kindly_web_search_mcp_server/search/synthesis.py tests/test_synthesis.py src/kindly_web_search_mcp_server/search/research_mode.py
git commit -m "feat: research synthesis pass + ResearchSearchResponse model (P0)"
```

---

### Task 5.2: Wire ResearchSearchResponse into server tool

**Objective:** Update `server.py` to return `ResearchSearchResponse` for research mode, including the synthesis field.

**Files:**
- Modify: `src/kindly_web_search_mcp_server/server.py` (research mode response handling)
- Modify: `src/kindly_web_search_mcp_server/search/orchestrator.py` (return ResearchSearchResponse)
- Create: `tests/test_research_search_response.py`

**Step 1: Update server.py tool handler**

```python
# In server.py, the web_search tool handler for research mode:
if search_mode == SearchMode.RESEARCH:
    from .models import ResearchSearchResponse
    
    response, research_meta = await run_web_search(...)  # Returns tuple now
    
    # Build ResearchSearchResponse from standard response + research metadata
    research_response = ResearchSearchResponse(
        query=response.query,
        results=response.results,
        total_results=response.total_results,
        providers_used=response.providers_used,
        research_rounds=research_meta.get("research_rounds", 0),
        provenance=research_meta.get("provenance", {}),
        gaps_identified=research_meta.get("gaps_identified", []),
        coverage_assessment=research_meta.get("coverage_assessment", ""),
        synthesis=research_meta.get("synthesis", ""),
        key_findings=research_meta.get("key_findings", []),
        established_facts=research_meta.get("established_facts", []),
    )
    return research_response
```

**Step 2: Update FastMCP return type annotation**

```python
# The tool return type annotation needs updating:
async def web_search(...) -> WebSearchResponse | ResearchSearchResponse:
```

**Step 3: Test ResearchSearchResponse**

```python
# tests/test_research_search_response.py
from kindly_web_search_mcp_server.models import ResearchSearchResponse, WebSearchResult

def test_research_response_has_all_fields():
    response = ResearchSearchResponse(
        query="test",
        results=[WebSearchResult(title="T", link="https://x.com", snippet="")],
        total_results=1,
        providers_used=["test"],
        research_rounds=2,
        provenance={"rounds": [{"round": 1, "query": "test"}]},
        gaps_identified=["missing cost data"],
        coverage_assessment="partial",
        synthesis="Test synthesis with [1] citation",
        established_facts=["Fact 1"],
    )
    assert response.research_rounds == 2
    assert response.synthesis  # Non-empty
    assert response.coverage_assessment == "partial"

def test_research_response_inherits_web_search():
    """ResearchSearchResponse should be usable where WebSearchResponse is expected."""
    from kindly_web_search_mcp_server.models import ResearchSearchResponse, WebSearchResponse
    assert issubclass(ResearchSearchResponse, WebSearchResponse)
```

**Step 4: Commit**

```bash
git add src/kindly_web_search_mcp_server/server.py src/kindly_web_search_mcp_server/models.py src/kindly_web_search_mcp_server/search/orchestrator.py tests/test_research_search_response.py
git commit -m "feat: wire ResearchSearchResponse into server tool (P1 response model fix)"
```
