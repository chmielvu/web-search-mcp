# Query Reformulation: Production Rules vs Current Implementation

**Date**: 2026-05-12
**Sources**: Actual production implementations from GitHub (NOT academic papers)

---

## Production Prompts Found

### 1. Google Gemini (Production)
**Repo**: `google-gemini/gemini-fullstack-langgraph-quickstart`
**File**: `backend/src/agent/prompts.py`

```python
query_writer_instructions = """Your goal is to generate sophisticated and diverse web search queries.

Instructions:
- Always prefer a single search query, only add another query if the original question requests multiple aspects or elements and one query is not enough.
- Each query should focus on one specific aspect of the original question.
- Don't produce more than {number_queries} queries.
- Queries should be diverse, if the topic is broad, generate more than 1 query.
- Don't generate multiple similar queries, 1 is enough.
- Query should ensure that the most current information is gathered. The current date is {current_date}.

Format:
Format your response as a JSON object with ALL two of these exact keys:
   - "rationale": Brief explanation of why these queries are relevant
   - "query": A list of search queries
"""
```

**RULES FOUND:**
| Rule | Purpose |
|------|---------|
| Prefer single query | Don't over-expand simple queries |
| Each query = one aspect | Avoid mixed-intent queries |
| Don't generate similar queries | Prevent duplicate effort |
| Queries should be diverse | Cover different angles |
| Ensure current information | Time context matters |

---

### 2. Sydekx (Production)
**Repo**: `cognizhi/sydekx`
**File**: `backend/app/agent/tools/query_expansion_tool.py`

```python
_EXPANSION_PROMPT = """\
You are a query expansion expert. Given the user's search query, generate 4 semantically varied reformulations that will improve document retrieval coverage.

Rules:
1. Paraphrase — rewrite with different words but same intent
2. Specific — drill down to a more specific aspect or detail
3. Broad — widen to related concepts or parent topics
4. Keywords — extract the most important keywords as a terse search phrase (no filler words)

Respond ONLY with a JSON array of exactly 4 strings. No commentary, no markdown fences.

User query: {query}
"""
```

**RULES FOUND:**
| Rule | Purpose |
|------|---------|
| Paraphrase = different words, same intent | Vocabulary diversity |
| Specific = drill down | Targeted retrieval |
| Broad = widen to parent topics | Context expansion |
| Keywords = terse, NO FILLER WORDS | Clean BM25-friendly query |

---

### 3. MemBrain (Production)
**Repo**: `czxxing/learn`
**File**: `membrain/search_stages/02_query_expansion.md`

#### Query Rewrite Prompt (Keyword Extraction):
```python
_SYSTEM = (
    "Extract 3-6 search keywords from the question. "
    "Keep proper nouns exactly as written. "
    "Use base/infinitive verb forms (e.g. 'research' not 'researching'). "
    "Remove question words (what/when/did/who/how/is/are). "
    "Output only the keywords, space-separated, no punctuation."
)
```

**Parameters**: Temperature=0.0, max_tokens=40

#### Multi-Query Prompt:
```python
_SYSTEM = """\
You are an expert at query reformulation for long-term conversational memory retrieval.
Generate EXACTLY 3 complementary search queries. Each query has a fixed role:

Query 1 — Event-focused (for embedding):
  For temporal questions ("when did X?", "how long ago?"), drop the time aspect
  entirely and focus on the EVENT itself.
  Example: "When did Caroline have a picnic?" → "What did Caroline do with friends outdoors?"
  For non-temporal questions, write a specific direct question as-is.

Query 2 — HyDE declarative (for embedding):
  Write the sentence that WOULD appear verbatim in a memory record if the answer existed.
  Example: "Caroline and her friends had a picnic together."

Query 3 — BM25 keyword strip (for keyword search):
  Keep ONLY entity names + core noun/verb base forms.
  Example: "When did Caroline have a picnic?" → "Caroline friends picnic"

Output ONLY valid JSON: {"queries": ["...", "...", "..."]}
"""
```

**RULES FOUND:**
| Rule | Purpose |
|------|---------|
| Keep proper nouns exactly as written | Precision preservation |
| Use base/infinitive verb forms | Normalization for matching |
| Remove question words | Strip noise words |
| Output only keywords, space-separated | Clean format |
| Event-focused vs HyDE vs BM25 | Role-specific variants |
| Temperature 0.0 | Deterministic output |

---

### 4. LangChain MultiQueryRetriever (Production)
**Repo**: `langchain-ai/langchain`
**File**: `libs/langchain/langchain_classic/retrievers/multi_query.py`

```python
DEFAULT_QUERY_PROMPT = PromptTemplate(
    input_variables=["question"],
    template="""You are an AI language model assistant. Your task is
    to generate 3 different versions of the given user
    question to retrieve relevant documents from a vector database.
    By generating multiple perspectives on the user question,
    your goal is to help the user overcome some of the limitations
    of distance-based similarity search. Provide these alternative
    questions separated by newlines. Original question: {question}""",
)
```

**RULES FOUND:**
| Rule | Purpose |
|------|---------|
| Generate 3 different versions | Fixed count |
| Multiple perspectives | Diversity |
| Overcome similarity search limitations | Why this helps |

---

## Current web-search-mcp Prompt

**File**: `query_rewrite.py` (lines 112-219)

```python
MISTRAL_QUERY_REWRITE_SYSTEM_PROMPT = """
You are a query optimizer for a coding assistant's web search tool.

CORE TASK: Take an over-keyworded or messy query and produce 3 concise, diverse search queries.

Return JSON only.
Follow the schema exactly.

CRITICAL INSTRUCTION FROM RESEARCH:
"Strip out all information that is not relevant for the retrieval task"
- Reduce keyword pile-on (agents dump 10+ keywords)
- Keep only terms that meaningfully impact search results
- Preserve exact technical literals verbatim: package names, versions, CLI flags, repo names, model names, function/class names, file paths, exact error fragments, quoted text.

CHAIN-OF-THINK PROCESS (think before generating):
1. Analyze: What is the core intent? Identify key technical terms vs filler keywords.
2. Strip: Remove redundant keywords, fix typos, normalize library names.
3. Generate: Produce 3 variants with different vocabulary/perspective/source focus.

Generate 3 diverse search queries focusing on:
- Different vocabulary: Use synonyms, related technical terms
- Different perspectives: User language vs expert/documentation language
- Different sources: Target docs sites, GitHub issues, tutorials

Query types (for intent="code"):
- original: Strip irrelevant keywords, fix typos, restructure for clarity
- official_docs: Target documentation sites (docs.*, API references)
- community_issues: Target GitHub issues, Stack Overflow, discussions

[... 100+ lines of examples ...]
"""
```

---

## Comparison: What Rules Are Missing

| Rule | Google Gemini | Sydekx | MemBrain | LangChain | Current web-search-mcp | STATUS |
|------|--------------|--------|----------|-----------|----------------------|--------|
| Prefer single query unless needed | ✅ | ❌ | ❌ | ❌ | ❌ | **MISSING** |
| Each query = one specific aspect | ✅ | ✅ | ✅ | ❌ | ❌ (implicit) | **WEAK** |
| Don't generate similar queries | ✅ | ❌ | ❌ | ❌ | ✅ ("not near-duplicates") | ✅ HAS |
| Queries should be diverse | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ HAS |
| Ensure current information | ✅ | ❌ | ❌ | ❌ | ❌ | **MISSING** (has date context) |
| Keywords = NO FILLER WORDS | ❌ | ✅ | ✅ | ❌ | ❌ ("strip irrelevant keywords" similar) | **WEAK** |
| Keep proper nouns exactly | ❌ | ❌ | ✅ | ❌ | ✅ | ✅ HAS |
| Use base/infinitive verb forms | ❌ | ❌ | ✅ | ❌ | ❌ | **MISSING** |
| Remove question words | ❌ | ❌ | ✅ | ❌ | ❌ | **MISSING** |
| Output only keywords | ❌ | ❌ | ✅ | ❌ | ❌ | **MISSING** (returns full JSON) |
| Paraphrase = different words, same intent | ❌ | ✅ | ❌ | ✅ | ✅ ("different vocabulary") | ✅ HAS |
| Specific drill-down variant | ❌ | ✅ | ❌ | ❌ | ✅ (docs angle) | ✅ HAS |
| Broad parent topics variant | ❌ | ✅ | ❌ | ❌ | ✅ (community angle) | ✅ HAS |
| Temperature 0.0 for keywords | ❌ | ❌ | ✅ | ❌ | 0.7 | **WRONG VALUE** |
| Include rationale/explanation | ✅ | ❌ | ❌ | ❌ | ✅ ("why" field) | ✅ HAS |

---

## Targeted Improvements Based on Production Rules

### Improvement 1: Add "Remove Question Words" Rule
**From**: MemBrain production
**Why**: "what/when/did/who/how/is/are" are noise in search queries

**Current**: No explicit rule
**Add**:
```python
"- Remove question words: what, when, did, who, how, is, are, does, can, should"
```

---

### Improvement 2: Add "Use Base Verb Forms" Rule
**From**: MemBrain production
**Why**: "researching" → "research" improves BM25 matching

**Current**: No rule
**Add**:
```python
"- Use base/infinitive verb forms: 'researching' → 'research', 'installing' → 'install'"
```

---

### Improvement 3: Add "No Filler Words" Rule for Keyword Variant
**From**: Sydekx production
**Why**: Keyword queries should be terse for BM25

**Current**: "Strip irrelevant keywords" (similar but vague)
**Replace with**:
```python
"- Keywords variant: Extract ONLY important terms, NO filler words (the, a, an, for, with, to, in, on, at, of)"
```

---

### Improvement 4: Add "Don't Generate Similar Queries" Explicitly
**From**: Google Gemini production
**Why**: Prevent wasted retrieval effort

**Current**: "Make variants complementary, not near-duplicates" (similar)
**Keep**: Already present, good

---

### Improvement 5: Add "Each Query = One Aspect" Rule
**From**: Google Gemini, Sydekx production
**Why**: Prevent mixed-intent queries

**Current**: Implicit through variant types
**Add**:
```python
"- Each query should focus on ONE specific aspect or source type"
```

---

### Improvement 6: Temperature Adjustment
**From**: MemBrain production
**Why**: Temperature 0.0 for deterministic keyword extraction

**Current**: 0.7 (too random for keyword extraction)
**Recommendation**:
```python
# For keyword-focused variants: temperature=0.0
# For paraphrase variants: temperature=0.7-0.8
```

---

### Improvement 7: Add "Prefer Single Query" for Simple Queries
**From**: Google Gemini production
**Why**: Don't over-expand simple package lookups

**Current**: Always generates 3 variants
**Add**:
```python
"- If query is simple (single package/term), prefer fewer variants"
```

---

## Concrete Prompt Changes

### Before (Current):
```
CORE TASK: Take an over-keyworded or messy query and produce 3 concise, diverse search queries.

CRITICAL INSTRUCTION FROM RESEARCH:
"Strip out all information that is not relevant for the retrieval task"
- Reduce keyword pile-on (agents dump 10+ keywords)
- Keep only terms that meaningfully impact search results
- Preserve exact technical literals verbatim...
```

### After (With Production Rules):
```python
CORE TASK: Generate 2-3 search queries optimized for BM25 lexical search (NOT vector/embedding search).

HARD RULES FROM PRODUCTION SYSTEMS:
1. Remove question words: what, when, did, who, how, is, are, does, can, should
2. Use base verb forms: 'researching' → 'research', 'installing' → 'install'
3. Preserve exact literals: package names, versions, CLI flags, error codes, URLs, quoted text
4. Keywords variant: ONLY important terms, NO filler words (the, a, an, for, with, to, in, on, at, of)
5. Each query focuses on ONE specific aspect/source
6. Don't generate similar queries - each must be complementary

VARIANT ROLES:
- original: Strip noise, keep core keywords, minimal changes
- docs: Add "documentation", "API", "guide" - focus on official sources
- community: Add "GitHub issue", "Stack Overflow" - focus on problem-solving sources

Output JSON: {"variants": [{"kind": "...", "query": "...", "why": "..."}]}
```

---

## Validation: Why These Rules Work

### Question Word Removal
**Example**: "What version of React supports hooks?" → "React version hooks"
**Why**: "What" and "of" don't help BM25 find relevant docs

### Base Verb Forms
**Example**: "How to install React" → "install React"
**Why**: "to" is noise, "installing" → "install" normalizes

### No Filler Words
**Example**: "the best way to fix TypeError" → "TypeError fix"
**Why**: "the", "best", "way", "to" dilute BM25 term weights

### Each Query = One Aspect
**Example**: Don't mix "React hooks documentation" + "React hooks GitHub issues" → make separate focused queries

---

## Implementation Priority

| Improvement | Impact | Effort |
|-------------|--------|--------|
| Remove question words rule | HIGH (cleaner queries) | LOW |
| Base verb forms rule | MEDIUM (better matching) | LOW |
| No filler words rule | HIGH (terse keywords) | LOW |
| Each query = one aspect | MEDIUM (focused queries) | LOW |
| Temperature adjustment | MEDIUM | LOW |
| Prefer single query logic | HIGH (prevent over-expansion) | MEDIUM |

---

## Summary

**Current Prompt Strengths:**
- Preserves exact technical literals ✓
- Chain-of-think process ✓
- Structured JSON output ✓
- Anti-duplicate rule ✓
- Variant role definitions ✓

**Current Prompt Gaps (from Production Systems):**
1. **No question word removal** - MemBrain uses this
2. **No base verb form normalization** - MemBrain uses this
3. **No "no filler words" rule** - Sydekx uses this
4. **No "each query = one aspect" rule** - Google Gemini uses this
5. **Temperature wrong** - Should be 0.0 for keyword extraction
6. **Always generates 3 variants** - Should prefer fewer for simple queries

**Key Insight**: Production systems focus on CLEANING queries (removing noise), not just expanding them. The current prompt focuses heavily on expansion but lacks the cleaning rules that MemBrain and Sydekx use.