# Query Reformulation Deep Research Report

**Date**: 2026-05-12
**Context**: web-search-mcp query rewrite improvements
**Focus**: Web search queries, prompt templates, LLM guidelines for quality output

---

## Executive Summary

This research compiles evidence-based findings on query reformulation for web search, including:
- Actual prompt templates from production systems (QueryGym, Elastic Labs, HyDE, Query2Doc)
- Guidelines/rules that make LLMs output high-quality search queries
- Key patterns from 10+ research papers (Query2Doc, MuGI, GenQREnsemble, QA-Expand, Step-Back)

**Core Finding**: Template-based expansion with DSL integration beats free-form rewriting. The winning pattern is: **must clause (original query) + should clause (LLM expansion terms)**, NOT replacement.

---

## 1. Actual Prompt Templates from Production Systems

### 1.1 QueryGym Prompt Bank (Authoritative Source)

QueryGym is the standardized toolkit for LLM query reformulation (SIGIR 2026 reproducibility study). These are the actual prompts used:

#### GenQR (Keyword Expansion)
```yaml
# genqr.keywords.v1
system: |
  You output only comma-separated keywords. No sentences.
user: |
  Suggest keywords to improve retrieval for this query:
  "{query}"
  Return only keywords.
notes: Minimal keyword expansion; consumers split by comma.
```

#### GenQREnsemble (10 Instruction Variants)
```yaml
# inst1.v1
system: You output only comma-separated keywords. No sentences.
user: Improve the search effectiveness by suggesting expansion terms for the query: "{query}"

# inst2.v1
user: Recommend expansion terms for the query to improve search results: "{query}"

# inst3.v1
user: Improve the search effectiveness by suggesting useful expansion terms for the query: "{query}"

# inst4.v1
user: Maximize search utility by suggesting relevant expansion phrases for the query: "{query}"

# inst5.v1
user: Enhance search efficiency by proposing valuable terms to expand the query: "{query}"

# inst6.v1
user: Elevate search performance by recommending relevant expansion phrases for the query: "{query}"

# inst7.v1
user: Boost the search accuracy by providing helpful expansion terms to enrich the query: "{query}"

# inst8.v1
user: Increase the search efficacy by offering beneficial expansion keywords for the query: "{query}"

# inst9.v1
user: Optimize search results by suggesting meaningful expansion terms to enhance the query: "{query}"

# inst10.v1
user: Enhance search outcomes by recommending beneficial expansion terms to supplement the query: "{query}"
```

**Key Pattern**: All 10 variants use the same structure: `system` constrains output format (comma-separated keywords only), `user` paraphrases the instruction differently. Merged keywords provide diverse coverage.

#### Query2Doc (Pseudo-Document Generation)
```yaml
# query2doc.zeroshot.v1
user: |
  Write a passage that answers the given query:
  Query: {query}
  Passage:

# query2doc.fewshot.v1 (4 examples)
user: |
  Write a passage that answers the given query:
  
  {examples}
  
  Query: {query}
  Passage:

# query2doc.cot.v1 (Chain-of-Thought)
user: |
  Answer the following query:
  
  {query}
  
  Give the rationale before answering
```

#### QA-Expand (Question-Answer Pipeline)
```yaml
# qa_expand.subq.v1 (Sub-question generation)
system: |
  You are a helpful assistant. Based on the following query, generate 3 possible related questions that someone might ask.
user: |
  Break the query into sub-questions: "{query}"

# qa_expand.answer.v1 (Answer generation)
system: |
  You are a knowledgeable assistant. The user provides 3 questions in JSON format. For each question, produce a document style answer. Each answer must: Be informative regarding the question. Return all answers in JSON format with the keys answer1, answer2, and answer3.
user: |
  Questions to answer: "{questions}"

# qa_expand.refine.v1 (Filtering/refinement)
system: |
  You are an evaluation assistant. You have an initial query and answers provided in JSON format. Your role is to check how relevant and correct each answer is. Return only those answers that are relevant and correct to the initial query. Omit or leave blank any that are incorrect, irrelevant, or too vague.
```

#### MuGI (Multi-Granularity Integration)
```yaml
# mugi.zeroshot.v1
template: Generate 5 independent pseudo-documents from query
config:
  num_docs: 5
  adaptive_times: true
  temperature: 1.0
  max_tokens: 1024
```

**Key**: MuGI generates 5 pseudo-references (optimal per research), then adaptively concatenates.

### 1.2 Elastic Labs Template-Based Expansion

Elastic Labs research shows template-based expansion beats free-form:

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

**Critical Rules**:
- `must` clause: Hard requirement, original query must match
- `should` clause: Score booster, matching documents get higher rank but aren't excluded
- **NEVER replace original query** - always combine

#### Elastic Labs Prompts

**Prompt 2 (Best Performer - +1pt NDCG@10)**:
```
Extract the most important keywords from the query.
If the query is too short or missing information, add relevant entities/synonyms.
```

**Prompt 4 (Pseudo-Answer Generation)**:
```
Generate hypothetical answers to the question.
Provide 3-5 answer-like passages.
```

**Prompt 5 (Model's Choice)**:
```
Choose the best method(s) for this query:
- keyword extraction
- keyword enrichment
- pseudo-answer generation
Explain your rationale.
```

### 1.3 Chain-of-Thought Prompts (Google Research)

**CoT Prompt (Best for verbose keyword generation)**:
```
Answer the following question:
{query}

Let's think step by step:
```

**Why it works**: CoT instructs model to break answer into steps, generating many related keywords naturally.

### 1.4 HyDE Prompt (Hypothetical Document Embeddings)

```python
# Zero-shot HyDE
prompt = "Write a paragraph that answers the question: [query]"

# Domain-specific HyDE
prompt = "As a board-certified ophthalmologist, explain: [query]"

# Developer support variant
prompt = "Answer succinctly: " + query
```

**Key Pattern**: Role conditioning critical for domain-specific retrieval.

### 1.5 Query Reformulation Prompt (RAG-Corrective)

```python
SYS_PROMPT_RW = """Act as a question re-writer and perform the following task:
- Convert the following input question to a better version that is optimized for web search.
- When re-writing, look at the input question and try to reason about the underlying semantic intent / meaning."""
```

---

## 2. Guidelines/Rules for LLM Quality Query Output

### 2.1 Output Format Constraints (CRITICAL)

| Constraint | Reason | Example |
|------------|--------|---------|
| **Comma-separated keywords only** | Deterministic parsing | `react, hooks, state management` |
| **No sentences in keyword mode** | Prevents verbose drift | ❌ "React is a framework for..." |
| **JSON structured output** | Schema validation | `{"keywords": ["term1", "term2"]}` |
| **Max tokens: 256 for keywords** | Cost/latency control | Prevents runaway generation |
| **Temperature: 0.8-0.92** | Balance diversity/consistency | Higher for ensemble, lower for single |

### 2.2 Hard Rules from Research Papers

#### Query2Doc (Microsoft Research)
1. **Query repetition trick**: `q × n` before concatenation (n=5 for BM25)
2. Formula: `q' = concat({q} × 5, pseudo_document)`
3. **NEVER use pseudo-doc alone** - combination performs substantially better

#### Elastic Labs
1. **Template-based beats free-form**: Guided prompts reduce drift
2. **Must + Should structure**: Original query is hard requirement, LLM output boosts scores
3. **Small models viable**: Haiku performs similarly to Sonnet for QR tasks
4. **PRF documents help**: Including top-3 docs in prompt improves top-heavy metrics

#### QueryGym Reproducibility Study
1. **Reformulation gains are conditional**: Depend on retrieval paradigm
2. **Lexical gains don't transfer to neural retrievers**: BM25 improvements may not help dense retrievers
3. **Larger LLMs don't uniformly outperform**: Scale effects depend on method and domain
4. **Unified decoding configs essential**: Temperature, max_tokens must be identical across comparisons

### 2.3 Anti-Hallucination Rules

```yaml
# From current web-search-mcp MISTRAL_QUERY_REWRITE_SYSTEM_PROMPT
hard_rules:
  - Keep one query very close to original intent
  - Preserve exact technical literals:
    - package names, versions, CLI flags
    - repo names, file paths
    - error codes, method names
    - quoted text, URLs
  - Do not invent package names/versions
  - Do not over-interpret vague queries
  - Make variants complementary, not near-duplicates
```

### 2.4 Precision Preservation Rules (from query_policy.py)

**Bypass Triggers** (preserve exact, no rewriting):
| Signal | Pattern | Example |
|--------|---------|---------|
| URLs | `https?://`, `www.` | `https://github.com` |
| Quoted strings | 4+ chars in quotes | `"exact match"` |
| Repo names | `owner/repo` | `facebook/react` |
| File paths | `/path/to/file` | `src/utils.ts` |
| Version numbers | `1.2.3`, `@18.2.0` | `react 18.2.0` |
| Error codes | `0x1234`, `EINVAL` | `TypeError: x` |
| CLI flags | `--verbose`, `-v` | `git --no-verify` |
| Multiple search operators | ≥2: `site:`, `filetype:` | `site:github.com filetype:py` |

---

## 3. Key Patterns from Research Papers

### 3.1 Query2Doc (Wang et al., 2023)

**Formula**: `q' = concat(q × n, pseudo_document)`
- n=5 for BM25 (boosts query term weights)
- `[SEP]` separator for dense retrieval
- **Results**: +3-15% BM25 improvement on MS-MARCO, TREC DL

**Prompt**: "Write a passage that answers the given query:" + 4-shot examples

**Critical Finding**: Pseudo-doc + original query = substantially better than pseudo-doc alone.

### 3.2 MuGI (Zhang et al., 2024)

**Approach**: Generate 5 pseudo-references, adaptive concatenation
- 5 is optimal (per MuGI paper research)
- Adaptive weighting: balance lexical emphasis
- **Key**: Diversity from multiple generations mitigates single-generation noise

### 3.3 GenQREnsemble (Dhole & Agichtein, 2024)

**Approach**: 10 paraphrased instructions → merged keyword sets
- Exploits prompt diversity for complementary terms
- Temperature: 0.92 (higher than single GenQR)
- **Result**: Ensemble outperforms single instruction significantly

### 3.4 QA-Expand (Seo & Lee, 2025)

**Pipeline**:
1. Generate 3 sub-questions
2. Produce pseudo-answers for each
3. Feedback-driven filtering (retain only informative)
4. Concatenate filtered answers with original query

**Key**: Multi-stage refinement reduces noise.

### 3.5 Step-Back Prompting (Google DeepMind)

**Approach**: Abstract to higher-level concept before retrieval

```
Original: "Which team did Thierry Audel play for from 2007 to 2008?"
Step-Back: "Which teams did Thierry Audel play for in his career?"
```

**Results**: +7-27% accuracy on reasoning tasks (MMLU Physics, TimeQA)

**Prompt Template**:
```
You are an expert of world knowledge.
Your task is to step back and paraphrase a question to a more generic step-back question.
Original Question: {question}
Step-Back Question:
```

---

## 4. Recommended Improvements for web-search-mcp

### 4.1 Prompt Template Improvements

**Current**: 106-line Mistral prompt (over-engineered vs industry 4-7 lines)

**Recommended**:
```python
CONSERVATIVE_REWRITE_PROMPT = """
Generate {max_variants} complementary web search queries.

Rules:
- Preserve exact literals: packages, versions, errors, paths, flags
- Never invent facts, versions, or APIs
- Each variant targets different source types
- Output: comma-separated keywords only

Query: {query}
"""

# Alternative: DSL integration
DSL_EXPANSION_PROMPT = """
Extract keywords to boost search for: "{query}"

Return JSON: {"keywords": ["term1", "term2", ...]}
"""
```

### 4.2 Fanout Strategy Improvements

| Query Type | Recommended Fanout | Reasoning |
|------------|-------------------|-----------|
| Precision (bypass) | 2x results | Exact match matters, don't dilute |
| Broad/informational | 3x results | Coverage matters, multiple angles |
| Comparative | 4x results | Need both sides of comparison |
| Multi-hop | 5x results | Complex reasoning needs more sources |

### 4.3 Client Control Parameters

**Missing in current implementation** (per query-fanout-analysis.md):

```python
class FanoutMode(Enum):
    NONE = "none"       # Single query
    LIGHT = "light"     # 2 variants
    FULL = "full"       # 3-5 variants
    DECOMPOSE = "decompose"  # Sub-query decomposition

# Tool parameters:
fanout_mode: FanoutMode = FanoutMode.LIGHT
max_variants: int = 3  # 1-5, client-controlled
variant_types: list[str] | None = None  # Override default
```

### 4.4 DSL Integration Pattern

**From Elastic Labs**:
```python
def build_expanded_query(original: str, expansion_terms: list[str]) -> dict:
    return {
        "bool": {
            "must": {"match": {"text": original}},
            "should": [{"match": {"text": term}} for term in expansion_terms]
        }
    }
```

**For SearXNG**: Construct query with boosted original + expansion terms.

### 4.5 Temperature by Mode

| Mode | Temperature | Max Tokens | Reasoning |
|------|-------------|------------|-----------|
| Keyword extraction | 0.8 | 256 | Diversity without drift |
| Ensemble (10 variants) | 0.92 | 256 | Higher diversity for coverage |
| Pseudo-document | 1.0 | 1024 | Creative generation needed |
| Chain-of-thought | 0.7 | 512 | Reasoning focus |

---

## 5. What Agents Hate vs What Agents Need

### 5.1 Noise Sources (Avoid)

| Source | Why Agents Hate | Example |
|--------|----------------|---------|
| Over-rewritten queries | Loses precision on literals | `npm install react` → "how to install React" |
| Invented terms | Hallucinated packages/versions | Adds "React 19.5" when query said "React" |
| Too many variants | Context pollution | 10 variants for simple package lookup |
| Lost search operators | Ignores explicit filters | `site:github.com` removed |
| Semantic drift | Changes intent | Error trace → generic troubleshooting |

### 5.2 What Agents Need

| Query Type | Desired Behavior | Example |
|------------|-----------------|---------|
| Package versions | Exact match, bypass | `react 18.2.0 changelog` |
| Error traces | Preserve exact text | `TypeError: Cannot read property 'x'` |
| CLI flags | Keep flags intact | `git commit --no-verify` |
| Broad questions | Conservative expansion | "best payment gateway" → docs + community variants |
| Comparative | Add comparative terms | "Stripe vs PayPal" → also "Stripe pricing", "PayPal fees" |

---

## 6. Implementation Priority

### Phase 1: Quick Wins (1-2 days)

1. Add temperature control by mode (0.8 keyword, 1.0 pseudo-doc)
2. Add client parameters: `fanout_mode`, `max_variants`
3. Use QueryGym prompt templates (shorter, validated)
4. Implement must+should DSL pattern

### Phase 2: Medium Enhancements (3-5 days)

1. Implement GenQREnsemble (10 paraphrased instructions)
2. Add heuristic fallback when LLM fails
3. Implement comparative/multi-hop detection
4. Add PRF document integration option

### Phase 3: Strategic (1-2 weeks)

1. Template-based expansion system (YAML prompt bank)
2. Task-specific prompt templates
3. Multi-stage QA-Expand pipeline
4. Cross-paradigm retrieval testing

---

## 7. Key Research Papers Referenced

1. **Query2Doc** (Wang et al., 2023) - arxiv:2303.07678
2. **MuGI** (Zhang et al., 2024) - arxiv:2401.06311
3. **GenQREnsemble** (Dhole & Agichtein, 2024) - arxiv:2404.03746
4. **QA-Expand** (Seo & Lee, 2025) - arxiv:2502.08557
5. **Step-Back Prompting** (Google DeepMind) - arxiv:2310.06117
6. **Query Expansion by Prompting LLMs** (Jagerman et al., 2023) - arxiv:2305.03653
7. **Elastic Labs Query Rewriting** - elastic.co/search-labs/blog
8. **QueryGym Reproducibility Study** (Bigdeli et al., 2026) - SIGIR 2026
9. **HyDE** (Gao et al., 2022) - arxiv:2212.10496
10. **LameR** (Shen et al., 2024) - corpus-grounded expansion

---

## 8. Summary: 10 Key Takeaways

1. **Template-based beats free-form**: Guided prompts reduce drift
2. **Must + Should DSL**: Never replace original query, always combine
3. **Comma-separated keywords**: Deterministic parsing, no sentences
4. **Query × n trick**: Boost original query weights before concatenation
5. **5 pseudo-references optimal**: MuGI research finding
6. **10 instruction variants**: GenQREnsemble diversity pattern
7. **Temperature by mode**: 0.8 keywords, 1.0 pseudo-docs
8. **Precision preservation**: Bypass on technical literals
9. **Client control parameters**: `fanout_mode`, `max_variants`
10. **Cross-paradigm testing**: Lexical gains may not transfer to neural retrievers