# Query Reformulation Implementation Plan for web-search-mcp

**Date**: 2026-05-12
**Based on**: query-reformulation-deep-research-2026-05-12.md
**Target**: BM25 lexical search via SearXNG (NOT vector/RAG)

---

## Executive Summary

Current implementation has **5 critical gaps** vs research-backed best practices:

| Gap | Current | Research | Impact |
|-----|---------|----------|--------|
| Prompt length | 106 lines | 4-7 lines | Instruction dilution, drift |
| Client control | `rewrite: bool` only | `fanout_mode`, `max_variants` | No tuning for query type |
| Temperature | Single setting | Mode-specific (0.8/0.92/1.0) | Wrong creativity for task |
| DSL pattern | Separate queries | must + should | No score boosting |
| Query boost | None | Query × 5 for BM25 | Original query weight lost |

---

## Current Implementation Analysis

### Strengths (KEEP)

1. **Precision preservation** (`query_policy.py`)
   - Comprehensive regex bypass triggers
   - URLs, versions, error codes, CLI flags, quoted strings
   - Multiple search operators → bypass
   - ✅ Matches research: "preserve exact technical literals"

2. **JSON structured output**
   - `response_format={"type": "json_object"}`
   - Pydantic validation (`QueryVariant`, `QueryRewriteOutput`)
   - ✅ Matches QueryGym: "JSON structured output for schema validation"

3. **Chain-of-thought in prompt**
   - "CHAIN-OF-THINK PROCESS: Analyze → Strip → Generate"
   - ✅ Matches Query2Doc CoT: "Give the rationale before answering"

4. **Intent types** (code, general_research, comparison)
   - ✅ Omnius pattern: "Different sub-query types for different intents"

5. **research_goal parameter**
   - ✅ Jina pattern: "Task-specific prompts improve results"

### Gaps (FIX)

#### Gap 1: Over-engineered Prompt (106 lines)

**Current** (lines 112-219):
```python
MISTRAL_QUERY_REWRITE_SYSTEM_PROMPT = """
You are a query optimizer for a coding assistant's web search tool.

CORE TASK: Take an over-keyworded or messy query and produce 3 concise, diverse search queries.
...[106 lines of verbose examples]...
"""
```

**Research Standard** (QueryGym GenQR):
```yaml
system: You output only comma-separated keywords. No sentences.
user: Suggest keywords to improve retrieval for this query: "{query}"
```

**Elastic Labs Prompt 2** (best performer):
```
Extract the most important keywords from the query.
If the query is too short or missing information, add relevant entities/synonyms.
```

**Impact**: Long prompts cause instruction dilution. Model focuses on examples rather than core rules.

**Fix**: Reduce to 10-15 lines with declarative constraints (see Phase 1).

---

#### Gap 2: No Client Control Parameters

**Current** (`server.py`):
```python
async def web_search(
    query: str,
    num_results: int = 5,
    rewrite: bool = True,  # Only on/off, no mode selection
    providers: list[str] | None = None,
    research_goal: str | None = None,
) -> dict:
```

**Research Standard** (Tavily):
```python
search_depth: Literal["basic", "advanced"] = "basic"  # Client control
```

**Research Standard** (LangChain MultiQuery):
```python
num_queries: int = 3  # Configurable variant count
```

**Impact**: Agent cannot tune fanout for query complexity. Simple package lookup gets 3 variants (overkill). Complex multi-hop gets same 3 variants (under-coverage).

**Fix**: Add `fanout_mode` and `max_variants` parameters (see Phase 1).

---

#### Gap 3: Single Temperature Setting

**Current** (`settings.py`):
```python
query_rewrite_temperature: float = 0.7  # Single value for all modes
```

**Research Standard** (QueryGym):
| Mode | Temperature | Reasoning |
|------|-------------|-----------|
| Keyword extraction | 0.8 | Diversity without drift |
| Ensemble (10 variants) | 0.92 | Higher diversity for coverage |
| Pseudo-document | 1.0 | Creative generation needed |

**Impact**: Current 0.7 is too deterministic for coverage-focused expansion, potentially too random for precision queries.

**Fix**: Add mode-specific temperature (see Phase 1).

---

#### Gap 4: No DSL Integration (must + should)

**Current** (`orchestrator.py`):
```python
# Separate queries sent to providers
for q in queries:
    search_single_query(q, num_results=per_query_k, ...)
# Then merged via RRF
merged = merge_search_results(result_lists)
```

**Research Standard** (Elastic Labs):
```json
{
  "bool": {
    "must": { "match": { "text": "ORIGINAL_QUERY" } },
    "should": [
      { "match": { "text": "QR_TERM_1" } },
      { "match": { "text": "QR_TERM_2" } }
    ]
  }
}
```

**Key Insight**: `must` = hard requirement (original must match), `should` = score booster (matching documents rank higher). **Never replace original query**.

**Impact**: Current approach sends variants as separate queries, then merges. This loses the "original query is hard requirement" semantic. Documents matching variants but NOT original can still appear.

**SearXNG Adaptation**: SearXNG doesn't support Elasticsearch DSL, but we can construct queries that boost original:
```python
# Option A: Query string with boosted original
searxng_query = f'"{original_query}" OR ({variant_1} OR {variant_2})'

# Option B: Multiple engine queries with weight hints
queries = [
    (original_query, weight=2.0),  # Higher weight
    (variant_1, weight=1.0),
    (variant_2, weight=1.0),
]
```

**Fix**: Implement weighted query construction for SearXNG (see Phase 2).

---

#### Gap 5: No Query Repetition for BM25

**Current**: No query boosting

**Research Standard** (Query2Doc):
```python
q_prime = concat(q × 5, pseudo_document)  # Repeat original 5 times
```

**Why**: BM25 term frequency weighting. Repeating original query 5x increases its term weights, ensuring original intent dominates.

**Impact**: In current RRF merge, original query variant has same weight as expansion variants. Original intent can be diluted.

**Fix**: Boost original query weight in merge (Phase 2).

---

## Implementation Roadmap

### Phase 1: Quick Wins (1-2 days)

**Priority**: Address prompt bloat and client control

#### P1.1: Simplified Prompt Template

Replace 106-line prompt with QueryGym-style declarative prompt:

```python
CONSERVATIVE_QUERY_REWRITE_PROMPT = """
Generate {max_variants} web search queries from: "{query}"

HARD CONSTRAINTS:
- Preserve verbatim: package names, versions, error codes, CLI flags, URLs, repo names, quoted text
- NEVER invent: versions, issue numbers, APIs, package names not in query
- Output: JSON only, schema: {"variants": [{"kind": "...", "query": "...", "why": "..."}]}

VARIANT STRATEGY (intent={intent}):
- original: Minimal cleanup, preserve exact literals
- docs: Add "documentation", "API", "guide" terms
- community: Add "GitHub issue", "Stack Overflow" terms

Research goal: {research_goal}
"""
```

**Lines**: 12 (vs 106). Declarative constraints, minimal examples.

**Temperature**: 0.8 for keyword mode.

---

#### P1.2: Client Control Parameters

Add to `web_search` tool:

```python
async def web_search(
    query: str,
    num_results: int = 5,
    fanout_mode: Literal["none", "light", "full"] = "light",
    max_variants: int = 3,
    providers: list[str] | None = None,
    research_goal: str | None = None,
) -> dict:
```

**Mapping**:
| fanout_mode | Variants | Fanout | Use Case |
|-------------|----------|--------|----------|
| `none` | 1 (original only) | 2x results | Precision queries (versions, errors) |
| `light` | 2 | 2x results | Simple package lookup |
| `full` | 3-5 | 3x results | Broad research, multi-hop |

**Remove `rewrite: bool`**: Replace with `fanout_mode` which provides finer control.

---

#### P1.3: Mode-Specific Temperature

```python
# In query_rewrite.py
def _resolve_temperature(fanout_mode: str) -> float:
    if fanout_mode == "none":
        return 0.0  # Deterministic, no expansion
    elif fanout_mode == "light":
        return 0.8  # Keyword extraction standard
    else:  # full
        return 0.85  # Balanced diversity
```

---

#### P1.4: Variant Count Control

```python
# In orchestrator.py
def _resolve_variant_count(fanout_mode: str, max_variants: int) -> int:
    if fanout_mode == "none":
        return 1
    elif fanout_mode == "light":
        return min(2, max_variants)
    else:  # full
        return min(5, max_variants)

def _resolve_per_query_k(num_results: int, fanout_mode: str) -> int:
    if fanout_mode == "none":
        return max(num_results * 2, 6)  # 2x for precision
    elif fanout_mode == "light":
        return max(num_results * 2, 6)
    else:  # full
        return max(num_results * 3, 9)  # 3x for coverage
```

---

### Phase 2: DSL Integration (3-5 days)

**Priority**: Ensure original query dominates results

#### P2.1: Weighted Query Construction for SearXNG

Since SearXNG doesn't support Elasticsearch DSL, construct weighted queries:

```python
def build_weighted_search_query(original: str, variants: list[str], mode: str) -> list[tuple[str, float]]:
    """Build queries with weight hints for RRF merge."""
    if mode == "none":
        return [(original, 1.0)]

    # Original query gets higher weight (must clause analog)
    weighted = [(original, 2.0)]  # Double weight

    # Variants get standard weight (should clause analog)
    for variant in variants[:max_variants - 1]:
        weighted.append((variant, 1.0))

    return weighted
```

**RRF Modification**: Weighted RRF formula
```python
def weighted_rrf_merge(result_lists: list, weights: list[float], k: int = 60) -> list:
    """RRF with per-query weights."""
    scores = defaultdict(float)
    for results, weight in zip(result_lists, weights):
        for rank, result in enumerate(results, start=1):
            scores[result.link] += weight / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
```

---

#### P2.2: Query Repetition for BM25 Boost

For SearXNG, construct query string that repeats original:

```python
def build_bm25_boosted_query(original: str, expansion_terms: list[str]) -> str:
    """Query × 5 trick for BM25 term frequency boosting."""
    # Repeat original 5x (Query2Doc pattern)
    repeated_original = " ".join([original] * 5)

    # Add expansion terms as OR clauses
    expansion = " OR ".join(expansion_terms)

    return f"({repeated_original}) OR ({expansion})"
```

**Note**: This may not work with all SearXNG engines. Test empirically.

---

### Phase 3: Ensemble Expansion (1-2 weeks)

**Priority**: GenQREnsemble pattern for diverse coverage

#### P3.1: Multi-Prompt Ensemble

Implement GenQREnsemble pattern with 10 paraphrased prompts:

```python
ENSEMBLE_PROMPTS = [
    "Suggest keywords to improve retrieval for: {query}",
    "Extract important terms from: {query}",
    "What keywords would help find: {query}",
    "Identify search terms for: {query}",
    "Recommend expansion terms for: {query}",
    # ... 6 more variants
]

async def ensemble_keyword_expansion(query: str) -> list[str]:
    """Run 10 paraphrased prompts, merge keywords."""
    results = await asyncio.gather([
        mistral_generate(prompt.format(query=query), temperature=0.92)
        for prompt in ENSEMBLE_PROMPTS
    ])
    all_keywords = [parse_keywords(r) for r in results]
    return dedupe_merge(all_keywords)
```

**Temperature**: 0.92 (ensemble standard)

---

## Testing Strategy

### Unit Tests

```python
def test_simplified_prompt_length():
    assert len(CONSERVATIVE_QUERY_REWRITE_PROMPT.split('\n')) < 20

def test_fanout_mode_none():
    result = web_search(query="react 18.2.0", fanout_mode="none")
    assert result["variants"] == ["react 18.2.0"]

def test_weighted_rrf():
    results = weighted_rrf_merge(
        [r1, r2, r3],
        weights=[2.0, 1.0, 1.0],
        k=60
    )
    # Original query results should dominate
    assert results[0].source_query == "original"

def test_precision_bypass_preserved():
    # Existing tests in test_query_policy.py should pass unchanged
    policy = classify_search_query("TypeError: Cannot read property 'x'")
    assert policy.mode == "bypass"
```

### Integration Tests

```python
async def test_web_search_fanout_modes():
    # none: single query
    result_none = await web_search("react changelog", fanout_mode="none")
    assert len(result_none["final_queries"]) == 1

    # light: 2 variants
    result_light = await web_search("react hooks", fanout_mode="light")
    assert len(result_light["final_queries"]) <= 2

    # full: 3-5 variants
    result_full = await web_search("best payment gateway SaaS", fanout_mode="full")
    assert len(result_full["final_queries"]) <= 5
```

---

## Metrics to Track

1. **Keyword pile-on detection** (already implemented: `record_query_length`)
2. **Variant diversity**: How different are generated variants?
3. **Original query weight**: Does original dominate RRF merge?
4. **Precision query recall**: Do bypass queries find exact matches?

---

## Implementation Priority

| Change | Impact | Effort | Phase |
|--------|--------|--------|-------|
| Simplified prompt | High (reduces drift) | Low | P1.1 |
| fanout_mode parameter | High (client control) | Low | P1.2 |
| max_variants parameter | Medium | Low | P1.2 |
| Mode-specific temperature | Medium | Low | P1.3 |
| Weighted RRF merge | High (original dominates) | Medium | P2.1 |
| Query × 5 BM25 boost | Medium (experimental) | Medium | P2.2 |
| Ensemble expansion | High (coverage) | High | P3.1 |

---

## Files to Modify

| File | Changes |
|------|---------|
| `server.py` | Add `fanout_mode`, `max_variants` parameters to `web_search` |
| `query_rewrite.py` | Simplified prompt, mode-specific temperature |
| `orchestrator.py` | Variant count resolution, weighted merge |
| `merge.py` | Add weighted RRF function |
| `settings.py` | Remove `query_rewrite_temperature` (mode-specific now) |
| `tests/test_query_rewrite.py` | New tests for fanout modes |

---

## Risk Mitigation

1. **Prompt simplification may reduce quality**
   - Mitigation: A/B test old vs new prompt with real queries
   - Metric: Variant diversity and retrieval quality

2. **Weighted RRF may skew results**
   - Mitigation: Start with 1.5x weight, tune empirically
   - Metric: Original query result count in top-N

3. **Query × 5 may not work with SearXNG**
   - Mitigation: Test with specific engines, fallback to standard query
   - Metric: Retrieval quality with vs without repetition

---

## Summary

**Keep**: Precision preservation, JSON output, CoT, intent types, research_goal
**Fix**: Prompt length (106→12 lines), client control (add fanout_mode), temperature (mode-specific), DSL (weighted merge), BM25 boost (query repetition)

**Estimated effort**: 3-5 days for Phase 1, 1 week for Phase 2, 2 weeks for Phase 3

**Expected outcome**: More deterministic query reformulation, better original query preservation, client-tunable fanout for different query complexities.