---
name: Query Reformulation Best Practices
description: Evidence-based best practices for web search query reformulation/fanout in MCP tools, synthesizing research from Elastic Labs, Jina, Firecrawl, Omnius, and current implementation analysis
type: reference
---

# Query Reformulation Best Practices for Web Search MCPs

## Executive Summary

Based on deep research across Elastic Labs, Jina AI, Firecrawl, Omnius, and analysis of the current Mistral-based implementation, this document establishes evidence-based best practices for query reformulation in agentic web search MCPs.

**Core Finding:** The 2026 agentic reality is that **conservative reformulation + multi-variant search behind the scenes** is the winning pattern. Agents hate noisy or over-rewritten results on technical queries (package versions, error traces, CLI flags). Deep-research workflows happen at the **agent level**, not inside the MCP tool.

---

## 1. Current Implementation Analysis

### Mistral Query Rewrite (`search/query_rewrite.py`)

**Model:** Mistral LLM (configurable via `settings.query_rewrite_model`)
**Temperature:** Configurable (default from settings)
**Max Variants:** 2-3 complementary queries

**Prompt Structure (MISTRAL_QUERY_REWRITE_SYSTEM_PROMPT):**
```
Hard rules:
1. Keep one query very close to the original intent.
2. Preserve exact technical literals when present:
   package names, versions, CLI flags, repo names, model names,
   function/class names, file paths, exact error fragments, quoted text.
3. Do not invent package names, versions, issue numbers, or APIs.
4. Do not over-interpret vague queries. Clean them up, but stay conservative.
5. Make the variants complementary, not near-duplicates.

Preferred variants:
- original: cleaned, minimal rewrite, closest to the raw query
- official_docs: docs / API reference / migration guide / release notes angle
- community_issues: GitHub issues / discussions / Stack Overflow / workaround angle
```

**Variant Kinds:**
| Kind | Purpose | Angle |
|------|---------|-------|
| `original` | Cleaned version | Minimal rewrite, closest to raw query |
| `official_docs` | Documentation angle | API reference, migration guides, release notes |
| `community_issues` | Community angle | GitHub issues, discussions, Stack Overflow |

### Query Policy (`search/query_policy.py`)

**Approach:** Precision signal detection (heuristic regex-based), **NO intent classification**

**Bypass Triggers (Preserve Exact):**
- URLs (`https?://`, `www.`)
- Quoted strings (4+ chars)
- Repo names (`owner/repo`)
- File paths (`/path/to/file`)
- Version numbers (`1.2.3`)
- Hex error codes (`0x1234`)
- Error codes (`E001`, `EINVAL`, `EBADF`)
- Method names (`Class::method`)
- Function calls (`Foo.bar`, `np.array`)
- Constants (`MAX_SIZE`, `DEFAULT_TIMEOUT`)
- CLI flags (`--verbose`, `-v`)
- UUIDs, Git hashes, IP addresses
- **Multiple search operators** (≥2): `site:`, `filetype:`, `inurl:`, `repo:`, etc.

**Policy Modes:**
| Mode | Trigger | Behavior |
|------|---------|----------|
| `bypass` | Precision signals detected | Return original query only, 2x results |
| `expand` | No precision signals | Mistral generates 2-3 variants, 3x results |

### Fanout Strategy (`search/orchestrator.py`)

```python
def _resolve_per_query_k(num_results: int, mode: RewriteMode) -> int:
    if mode == "bypass":
        return max(num_results * 2, 6)   # 2x for precision queries
    return max(num_results * 3, 9)       # 3x for variant queries
```

**Search Flow:**
```
Raw Query → Query Policy → (bypass | expand) → Final Queries (1-3)
→ Parallel Search (all providers) → RRF Merge → Rerank → Top N
```

---

## 2. Industry Best Practices Synthesis

### Elastic Search Labs Findings

**Key Insight:** Template-based expansion beats free-form rewriting.

| Strategy | NDCG@10 Improvement | Best For |
|----------|---------------------|----------|
| Lexical Keyword Enrichment (Prompt 2) | +1pt | Lexical search |
| Pseudo-Answer Generation (Prompt 4) | +1-3pt | Multi-stage retrieval |
| Model's Choice (Prompt 5) | +1pt | Flexible scenarios |

**Implementation Pattern (Elastic DSL):**
```json
{
  "bool": {
    "must": { "match": { "text": "ORIGINAL_QUERY" } },
    "should": [
      { "match": { "text": "QR_TERM_1" } },
      { "match": { "text": "QR_TERM_2" } },
      { "match": { "text": "QR_TERM_3" } }
    ]
  }
}
```

**Critical Finding:** The `must` clause enforces hard requirements (original query must match). The `should` clause boosts scores for documents matching expansion terms. **Never rely solely on LLM output** - always include original query.

**Elastic Labs Recommendations:**
1. **Guided prompts over free-form:** Provide strict templates for what LLM should output
2. **Combine, not replace:** Use QR to boost existing scores, not replace original query
3. **Small models viable:** Claude 3.5 Haiku performs similarly to Sonnet for QR tasks
4. **Pseudo-answers for recall:** Generate hypothetical answers to maximize recall in multi-stage pipelines

### Jina AI Query Expansion Research

**Key Insight:** LLM-based query expansion improves smaller embedding models more than larger ones.

| Model | Average Improvement (100 words) | Robustness |
|-------|--------------------------------|------------|
| Jina V3 (large) | +1.02 | High (consistent gains) |
| MiniLM (small) | +6.51 | Variable (can decrease on some datasets) |

**Task-Specific Prompting:** Customizing prompts to the retrieval task improves results significantly (+0.40 to +2.46 additional improvement).

**Jina Recommendations:**
1. **Longer expansions for large models:** Jina V3 benefits from 250-word expansions
2. **Shorter expansions for small models:** MiniLM performs better with 100-word expansions
3. **Task-specific prompts beat generic:** Customize prompts for domain/task
4. **When to use:** If model handles long queries well AND recall matters more than speed

### Firecrawl Deep Research Analysis

**Key Insight:** Web search and deep research are two ends of a spectrum, both share the same retrieval layer.

| Dimension | Web Search | Deep Research |
|-----------|------------|---------------|
| Query strategy | 1-3 queries | Dozens of adaptive queries |
| Sources | Few dozen | Hundreds of pages |
| Reasoning | Single-pass | Search-reason loops |
| Where | MCP tool level | Agent orchestration level |

**Three-Layer Architecture:**
1. **Retrieval Layer:** Search APIs, scrapers, content extractors (Firecrawl lives here)
2. **Orchestration Layer:** Agent frameworks (LangGraph, CrewAI, AutoGen) - decides when/what to search
3. **Reasoning Layer:** LLM - reads results, draws conclusions, tells orchestration if done

**Firecrawl Recommendations:**
1. **Single-call efficiency:** MCP tools should return useful results in one call
2. **Agent handles deep-research:** Multi-step reasoning happens in agent, not inside MCP
3. **Clean separation:** Swap search provider without touching agent framework

### Omnius Query Fan-Out Analysis

**Key Insight:** AI search engines (ChatGPT, Perplexity, Google AI Mode) break queries into multiple sub-queries invisibly.

**Sub-Query Types:**
| Type | Purpose | Example for "payment gateway for SaaS" |
|------|---------|----------------------------------------|
| Reformulation | Rephrase main query | "SaaS billing platforms" |
| Comparative | Compare options | "Stripe vs PayPal for SaaS" |
| Related | Expand context | "Best invoicing software for SaaS" |
| Implicit | User didn't ask but needs | "Payment gateway fees and hidden costs" |
| Entity Expansion | Add related entities | Add "Square", "Recurly", "Zoho Subscriptions" |
| Personalized | User context | "Best EU-friendly SaaS payment gateways" |

**Omnius Recommendations:**
1. **Main keyword rankings aren't enough:** Must rank for dozens of related sub-queries
2. **Comparison content wins:** "X vs Y" pages are prime AI fodder
3. **Freshness matters:** Recently updated content with "best" or "vs" in title ranks higher
4. **LLMs predict next search:** Fanout anticipates user's follow-up questions

---

## 3. 2026 Agentic Web-Search Reality

### The Paradigm Shift

**Old Rule:** Rank #1 for target keyword = guaranteed visibility
**New Rule:** Ranking for main keyword doesn't guarantee appearance in AI results

**MCP Design Reality:**
- Claude's built-in search and third-party MCPs favor **single-call efficiency**
- Internal expansion (multi-variant search) happens **behind the scenes**
- Agents see final merged/reranked results, not the fanout process
- **Deep-research workflows happen at agent level**, not inside MCP tool

### What Agents Hate

**Noise Sources (Bad for Technical Queries):**
| Source | Why Agents Hate It | Example |
|--------|-------------------|---------|
| Over-rewritten queries | Loses precision on literals | `npm install react` → "how to install React framework" |
| Invented terms | Hallucinated packages/versions | Adds "React 19.5" when query said "React" |
| Too many variants | Context pollution | 10 variants for a simple package lookup |
| Lost search operators | Ignores user's explicit filters | `site:github.com` removed from query |
| Semantic drift | Changes intent completely | Error trace → generic troubleshooting |

### What Agents Need

| Query Type | Desired Behavior | Example |
|------------|-----------------|---------|
| Package versions | Exact match, bypass rewrite | `react 18.2.0 changelog` |
| Error traces | Preserve exact error text | `TypeError: Cannot read property 'x' of undefined` |
| CLI flags | Keep flags intact | `git commit --no-verify` |
| Repo/file paths | Exact path matching | `facebook/react/src/React.js` |
| Broad questions | Conservative expansion | "best payment gateway for SaaS" |
| Comparative | Add comparative variants | "Stripe vs PayPal" → also "Stripe pricing", "PayPal fees" |

---

## 4. Recommended Best Practices

### 4.1 Precision Preservation (Highest Priority)

**Rule:** When precision signals detected, **bypass all rewriting**.

**Current Implementation Assessment:** ✅ GOOD - Already implemented via `query_policy.py`

**Enhancement Opportunities:**
```python
# Current: Multiple search operators (≥2 triggers bypass)
# Enhancement: Single search operator with high-value target

BYPASS_SINGLE_OPERATOR_PATTERNS = [
    r'site:github\.com',  # GitHub-specific search
    r'site:stackoverflow\.com',  # Stack Overflow-specific
    r'filetype:pdf',  # Document type specific
    r'language:\w+',  # Code language specific
]

# Current: Version numbers trigger bypass
# Enhancement: Semantic version patterns with context

SEMANTIC_VERSION_CONTEXT = [
    r'\w+\s+v?\d+\.\d+\.\d+',  # "react v18.2.0"
    r'\w+\s+\d+\.\d+',  # "python 3.11"
    r'@\d+\.\d+\.\d+',  # npm style "@18.2.0"
]
```

### 4.2 Conservative Expansion (Medium Priority)

**Rule:** When expanding, generate **complementary variants**, not near-duplicates.

**Current Implementation Assessment:** ✅ GOOD - 3 distinct angles (original, docs, community)

**Enhancement from Research:**
- **Elastic Labs:** Use structured output for specific elements (keywords, pseudo-answers)
- **Jina:** Task-specific prompts improve performance
- **Omnius:** Sub-query types guide variant generation

**Proposed Prompt Enhancement:**
```python
CONSERVATIVE_REWRITE_PROMPT = """
You are a query optimizer for a coding assistant's web search tool.

INPUT: A raw search query that may be messy or poorly phrased.
OUTPUT: JSON with exactly 2-3 complementary variants.

CRITICAL RULES:
1. PRESERVE exact technical literals verbatim:
   - Package names: react, numpy, langchain
   - Versions: 18.2.0, 3.11, v4.0
   - CLI flags: --no-verify, -v, --force
   - Error codes: EINVAL, E001, 0x1234
   - File paths: src/index.ts, /etc/config
   - Quoted text: "exact phrase"
   - URLs and repo names: github.com/user/repo

2. NEVER invent:
   - Package versions not in the query
   - Issue numbers or PR numbers
   - API endpoints or function names not mentioned

3. VARIANT STRATEGY:
   - original: Clean punctuation, fix typos, minimal changes
   - docs: Add "documentation", "API", "guide", "reference" context
   - community: Add "issue", "discussion", "Stack Overflow" context

4. QUERY TYPE ADAPTATION:
   - Error/bug queries: Focus on "error", "fix", "solution", "workaround"
   - How-to queries: Focus on "tutorial", "example", "guide"
   - Comparison queries: Keep both entities, add "vs", "difference"
   - Version queries: Preserve exact version, add "changelog", "release"

Return JSON: {"variants": [{"kind": "...", "query": "...", "why": "..."}]}
"""
```

### 4.3 Fanout Strategy (Medium Priority)

**Rule:** Adjust fanout based on query type and policy mode.

**Current Implementation Assessment:** ✅ GOOD - 2x bypass, 3x expand

**Research-Based Enhancements:**

| Query Type | Fanout | Reasoning |
|------------|--------|-----------|
| Precision (bypass) | 2x results | Exact match matters, don't dilute |
| Broad/informational | 3x results | Coverage matters, multiple angles |
| Comparative | 4x results | Need both sides of comparison |
| Multi-hop | 5x results | Complex reasoning needs more sources |

**Proposed Enhancement:**
```python
def _resolve_per_query_k(num_results: int, policy: RewritePolicy) -> int:
    base_multiplier = {
        "bypass": 2,
        "expand": 3,
        "comparative": 4,  # NEW: detected comparative intent
        "multi_hop": 5,    # NEW: detected multi-hop intent
    }
    return max(num_results * base_multiplier[policy.mode], MIN_RESULTS[policy.mode])
```

### 4.4 Intent Classification (Optional Enhancement)

**Current:** No intent classification, only precision signal detection

**Research Suggestion:** Light intent classification could improve variant selection

| Intent | Variant Strategy | Fanout |
|--------|-----------------|--------|
| `factual` | docs + community | 3x |
| `navigational` | bypass (single URL expected) | 2x |
| `troubleshooting` | community + docs + workaround | 4x |
| `comparative` | both entities + comparative terms | 4x |
| `multi_hop` | decompose + related topics | 5x |

**Implementation Note:** This adds latency (LLM call for classification). Consider:
1. Heuristic classification for common patterns (regex-based)
2. LLM classification only for ambiguous queries
3. Cache intent for repeated query patterns

### 4.5 Template-Based Expansion (Elastic Labs Pattern)

**Rule:** Use structured templates instead of free-form rewriting.

**Pattern:**
```python
EXPANSION_TEMPLATES = {
    "keyword_extraction": {
        "prompt": "Extract 3-5 most relevant keywords from: {query}",
        "dsl": "should clause with extracted keywords"
    },
    "pseudo_answer": {
        "prompt": "Generate a hypothetical answer to: {query}",
        "dsl": "should clause with pseudo-answer keywords"
    },
    "entity_enrichment": {
        "prompt": "Add synonyms and related terms for entities in: {query}",
        "dsl": "should clause with enriched entities"
    }
}
```

**Why This Works (Elastic Labs Evidence):**
- Reduces LLM scope → more deterministic output
- Prevents drift from original intent
- Structured output fits cleanly into search pipeline

---

## 5. MCP Tool Contract Recommendations

### 5.1 Single-Call Efficiency

**Rule:** MCP tool should return useful results in one call, with internal fanout invisible to agent.

**Current Contract (web_search):**
- Returns: `title`, `link`, `snippet` (lightweight)
- Internal: Multi-provider search, RRF merge, optional rerank
- Fanout: Invisible to agent

**Enhancement:** Add optional `fanout_depth` parameter for agents that want deeper coverage:

```python
class WebSearchParams(BaseModel):
    query: str
    num_results: int = 5
    rewrite: bool = True  # Enable/disable internal rewrite
    providers: list[str] | None = None
    fanout_depth: Literal["light", "medium", "deep"] = "medium"  # NEW
```

| Fanout Depth | Variants | Per-Query K | Total Fetch |
|--------------|----------|-------------|-------------|
| light | 1 (bypass only) | num_results * 2 | ~10 |
| medium | 2-3 (default) | num_results * 3 | ~15-30 |
| deep | 4-5 | num_results * 4 | ~40-50 |

### 5.2 Diagnostics Transparency

**Rule:** Show agents what happened without polluting context.

**Current Implementation:** ✅ GOOD - Diagnostics emitted via `Diagnostics` object

**Recommended Diagnostics:**
```python
diagnostics = {
    "web_search.rewrite_plan": {
        "policy": "bypass" | "expand",
        "reason": "precision signals detected" | "no signals",
        "final_queries": ["..."],
    },
    "web_search.providers_used": ["searxng", "brave", "jina"],
    "web_search.merge_strategy": "RRF",
    "web_search.rerank_enabled": True | False,
}
```

### 5.3 Error Handling

**Rule:** Graceful degradation - always return results, even if rewrite fails.

**Current Implementation:** ✅ GOOD - Fallback to original query on error

**Enhancement:** Add retry with different strategy:
```python
async def rewrite_with_fallback(query: str) -> RewritePlan:
    try:
        return await mistral_rewrite(query)
    except (APIError, Timeout, InvalidJSON):
        # Fallback 1: Heuristic expansion (no LLM)
        return heuristic_expand(query)
    except Exception:
        # Fallback 2: Original query only
        return RewritePolicy(mode="bypass", reason="rewrite_failed", ...)
```

---

## 6. Prompt Engineering Recommendations

### 6.1 Mistral Prompt Optimization

**Current Prompt Strengths:**
- ✅ Hard rules for literal preservation
- ✅ Complementary variant strategy
- ✅ Structured JSON output

**Enhancement Opportunities:**

**1. Add Query Type Detection:**
```
First, classify the query type:
- version_query: Contains version numbers
- error_query: Contains error messages/traces
- package_query: Contains package/module names
- comparison_query: Contains "vs", "versus", "difference"
- howto_query: Contains "how to", "tutorial", "example"

Adapt variant strategy based on type.
```

**2. Add Temperature Control by Mode:**
```python
TEMPERATURE_BY_MODE = {
    "bypass": 0.0,  # No rewriting, exact match
    "expand": 0.3,  # Conservative expansion
    "creative": 0.7,  # For broad informational queries
}
```

**3. Add Few-Shot Examples:**
```
Examples:
Input: "react 18.2 changelog"
Output: {"variants": [
  {"kind": "original", "query": "react 18.2 changelog", "why": "exact version preserved"},
  {"kind": "official_docs", "query": "React 18.2 release notes changelog", "why": "docs angle"},
  {"kind": "community_issues", "query": "React 18.2 breaking changes GitHub issue", "why": "migration context"}
]}

Input: "TypeError Cannot read property x of undefined"
Output: {"variants": [
  {"kind": "original", "query": "TypeError: Cannot read property 'x' of undefined", "why": "exact error preserved"},
  {"kind": "community_issues", "query": "TypeError Cannot read property undefined Stack Overflow", "why": "solution search"},
  {"kind": "official_docs", "query": "JavaScript TypeError property access documentation", "why": "understanding error"}
]}
```

### 6.2 Task-Specific Prompt Templates (Jina Pattern)

**Pattern:** Customize prompt based on retrieval task

```python
TASK_PROMPTS = {
    "code_search": """
    Focus on:
    - Function/method names
    - Parameter signatures
    - Error types and messages
    - GitHub issue patterns
    """,
    "docs_search": """
    Focus on:
    - Official documentation sites
    - API reference patterns
    - Version-specific docs
    - Migration guides
    """,
    "general_search": """
    Focus on:
    - Broad topic coverage
    - Multiple perspectives
    - Current information
    """,
}
```

---

## 7. Actionable Implementation Roadmap

### Phase 1: Quick Wins (1-2 days)

| Change | Impact | Effort |
|--------|--------|--------|
| Add single operator bypass patterns | Higher precision | Low |
| Add temperature control by mode | Better consistency | Low |
| Add query type examples to prompt | Better variant selection | Low |

### Phase 2: Medium Enhancements (3-5 days)

| Change | Impact | Effort |
|--------|--------|--------|
| Implement fanout_depth parameter | Agent control | Medium |
| Add heuristic fallback expansion | Reliability | Medium |
| Add comparative/multi_hop detection | Better coverage | Medium |

### Phase 3: Strategic Enhancements (1-2 weeks)

| Change | Impact | Effort |
|--------|--------|--------|
| Light intent classification | Adaptive variants | High |
| Template-based expansion (Elastic pattern) | Structured outputs | High |
| Task-specific prompt templates | Domain optimization | High |

---

## 8. Metrics to Track

### Quality Metrics

| Metric | How to Measure | Target |
|--------|----------------|--------|
| Precision preservation rate | % queries bypassed correctly | >95% |
| Variant quality score | Manual eval / click-through | >80% |
| Rewrite error rate | % fallbacks triggered | <5% |
| Agent satisfaction | Feedback / implicit signals | TBD |

### Performance Metrics

| Metric | How to Measure | Target |
|--------|----------------|--------|
| Rewrite latency | Mistral call duration | <500ms |
| Total search latency | End-to-end duration | <3s |
| Provider diversity | % providers contributing results | >2 |

---

## References

1. **Elastic Search Labs:** "Query rewriting strategies for LLMs & search engines" - Template-based expansion, DSL integration, pseudo-answers
2. **Jina AI:** "LLM Query Expansion" - Task-specific prompts, model size effects
3. **Firecrawl:** "Deep Research for AI Agents" - Three-layer architecture, agent vs MCP separation
4. **Omnius:** "Query Fan-Out" - Sub-query types, 2026 AI search paradigm
5. **Current Implementation:** `query_rewrite.py`, `query_policy.py`, `orchestrator.py`

---

## Appendix: Current Implementation Code Locations

| Component | File | Key Functions |
|-----------|------|---------------|
| Mistral rewrite | `search/query_rewrite.py` | `rewrite_query()`, `QueryRewriteOutput` |
| Policy classification | `search/query_policy.py` | `classify_search_query()`, `RewritePolicy` |
| Policy resolver | `search/query_policy_resolver.py` | `resolve_query_routing()` |
| Orchestrator | `search/orchestrator.py` | `run_web_search()`, `_resolve_per_query_k()` |
| RRF merge | `search/merge.py` | `merge_search_results()` |