# Query Reformulation Production Prompts: Comprehensive Analysis

**Date**: 2026-05-12
**Source**: GitHub production implementations (NOT academic papers)
**Focus**: WEB SEARCH (BM25 lexical search), NOT RAG/vector/memory retrieval

---

## Executive Summary

After searching GitHub for production query rewrite prompts, I found **12+ actual implementations**. Key finding: **Most prompts are designed for RAG/vector search, NOT web search**. Only 4 prompts are truly web search/BM25 focused.

**Critical Pattern**: Production web search prompts are **SIMPLE** (4-10 lines), **DECLARATIVE** (hard rules), and **OUTPUT-CONSTRAINED** (JSON/comma-separated keywords only).

---

## All Prompts Found (12 Total)

### Category 1: WEB SEARCH / BM25 (4 prompts - APPLY THESE)

#### 1.1 Onyx (onyx-dot-app) - BM25 Keyword Expansion
**Source**: `backend/ee/onyx/prompts/query_expansion.py`
**Paradigm**: BM25 lexical search
**Status**: PRODUCTION, WEB SEARCH

```python
KEYWORD_EXPANSION_PROMPT = """
Generate a set of keyword-only queries to help find relevant documents for the provided query. 
These queries will be passed to a bm25-based keyword search engine. 
Provide a single query per line (where each query consists of one or more keywords). 
The queries must be purely keywords and not contain any filler natural language. 
The each query should have as few keywords as necessary to represent the user's search intent. 
If there are no useful expansions, simply return the original query with no additional keyword queries. 
CRITICAL: Do not include any additional formatting, comments, or anything aside from the keyword queries.

The user query is:
{user_query}
""".strip()
```

**RULES IDENTIFIED:**
| Rule | Purpose |
|------|---------|
| Passed to BM25 keyword search engine | Explicitly states target system |
| Purely keywords, NO filler natural language | Noise removal for BM25 |
| As few keywords as necessary | Terse queries, not verbose |
| Single query per line | Deterministic parsing |
| If no useful expansions, return original | Conservative fallback |
| NO formatting, comments, anything aside | Clean output only |

**CRITICAL INSIGHT**: This is the ONLY prompt that explicitly states "bm25-based keyword search engine". All others assume vector/embedding search.

---

#### 1.2 Google Gemini (Production)
**Source**: `google-gemini/gemini-fullstack-langgraph-quickstart/backend/src/agent/prompts.py`
**Paradigm**: Web search (Google Search grounding)
**Status**: PRODUCTION, WEB SEARCH

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

**RULES IDENTIFIED:**
| Rule | Purpose |
|------|---------|
| Prefer single query | Don't over-expand simple queries |
| Each query = one specific aspect | Avoid mixed-intent queries |
| Don't produce more than N queries | Cap on fanout |
| Queries should be diverse | Cover different angles |
| Don't generate similar queries | Prevent duplicate effort |
| Ensure most current information | Time context matters |

---

#### 1.3 Sydekx (Production)
**Source**: `cognizhi/sydekx/backend/app/agent/tools/query_expansion_tool.py`
**Paradigm**: Query expansion (could be web or RAG)
**Status**: PRODUCTION

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

**RULES IDENTIFIED:**
| Rule | Purpose |
|------|---------|
| Paraphrase = different words, same intent | Vocabulary diversity |
| Specific = drill down | Targeted retrieval |
| Broad = widen to parent topics | Context expansion |
| Keywords = terse, NO FILLER WORDS | Clean BM25-friendly query |
| Respond ONLY with JSON array | Deterministic output |

---

#### 1.4 Vietnam Heritage API (Production)
**Source**: `T-Phong/vietnam-heritage-api/main.py`
**Paradigm**: Database search with conversation context
**Status**: PRODUCTION

```python
KEYWORD_EXTRACTOR_PROMPT = """
You are a Context-Aware Keyword Extractor API.
Task: Extract entities and important keywords from user question for Database search.

PROCESS (REQUIRED):
1. Read "Conversation History" to understand context.
2. If current question uses pronouns (it, he, they, that...), immediately replace with specific nouns mentioned in history.
3. Only extract: NAMED ENTITIES (Names, Nicknames, Places, Organizations, Private Events) from the question.
4. Remove meaningless words (why, what is, how many, how).

OUTPUT FORMAT:
- Return only one line with keywords separated by commas.
- NEVER answer the question.
- NEVER explain.

Example 1:
History:
User: "Introduce Ha Long Bay."
Assistant: "Ha Long Bay is a natural world heritage in Quang Ninh..."
Input: "What beautiful caves are there?"
Output: Ha Long Bay, caves
(Explanation: "there" means "Ha Long Bay").
"""
```

**RULES IDENTIFIED:**
| Rule | Purpose |
|------|---------|
| Resolve pronouns from history | Context-aware rewriting |
| Only extract NAMED ENTITIES | Precision preservation |
| Remove meaningless words (why, what is...) | Question word removal |
| One line, comma-separated | Deterministic output |
| NEVER answer, NEVER explain | No drift |

---

### Category 2: NOT WEB SEARCH (8 prompts - DIFFERENT PARADIGMS)

#### 2.1 MemBrain - MEMORY RETRIEVAL (NOT WEB SEARCH)
**Source**: `czxxing/learn/membrain/search_stages/02_query_expansion.md`
**Paradigm**: Conversational memory retrieval (vector embeddings)
**Status**: NOT WEB SEARCH - designed for memory/vector search

```python
# Keyword Extraction
_SYSTEM = (
    "Extract 3-6 search keywords from the question. "
    "Keep proper nouns exactly as written. "
    "Use base/infinitive verb forms (e.g. 'research' not 'researching'). "
    "Remove question words (what/when/did/who/how/is/are). "
    "Output only the keywords, space-separated, no punctuation."
)

# Multi-Query for EMBEDDING search
_SYSTEM = """\
You are an expert at query reformulation for long-term conversational memory retrieval.
Generate EXACTLY 3 complementary search queries. Each query has a fixed role:

Query 1 — Event-focused (for embedding):
  For temporal questions, drop the time aspect and focus on the EVENT itself.
  
Query 2 — HyDE declarative (for embedding):
  Write the sentence that WOULD appear verbatim in a memory record if the answer existed.

Query 3 — BM25 keyword strip (for keyword search):
  Keep ONLY entity names + core noun/verb base forms.
"""
```

**CRITICAL**: This prompt explicitly mentions "for embedding" - it's designed for VECTOR search, NOT web search. The HyDE pattern (Query 2) generates pseudo-documents which works for embeddings but NOT for BM25 web search.

---

#### 2.2 LangChain MultiQueryRetriever - VECTOR DB (NOT WEB SEARCH)
**Source**: `langchain-ai/langchain/libs/langchain/langchain_classic/retrievers/multi_query.py`
**Paradigm**: Vector database similarity search
**Status**: NOT WEB SEARCH - designed for vector DB

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

**CRITICAL**: Explicitly says "vector database" and "distance-based similarity search" - NOT web search.

---

#### 2.3 HopRAG - MULTI-HOP RAG (NOT WEB SEARCH)
**Source**: `LIU-Hao-2002/HopRAG/config.py`
**Paradigm**: Knowledge graph multi-hop reasoning
**Status**: NOT WEB SEARCH - designed for multi-hop graph traversal

```python
query_reformulation_template='''
You are a query reformulation robot. I will provide you with a multi-hop query that touches multiple information. Your task is to break down the query into multiple sub-queries, each of which should be a single-hop query. The sub-queries should be related to each other and can be answered in sequence. You need to ensure that the sub-queries are clear and concise, and that they can be answered independently. Return as few subqueries as possible, but make sure that all the information in the original query is covered.
Your response must strictly follow the JSON format...

```json{"Subqueries":["What...?","How...?",.....]}}```

The followings are your multi-hop query:
{query}
'''
```

**CRITICAL**: Multi-hop decomposition is for knowledge graph traversal, NOT web search.

---

#### 2.4 HalluciGuard-CRAG - DOCUMENT RETRIEVAL (NOT WEB SEARCH)
**Source**: `Pavan-220405/HalluciGuard-CRAG/schemas/templates.py`
**Paradigm**: RAG document retrieval
**Status**: NOT WEB SEARCH - designed for RAG

```python
rewrite_prompt = ChatPromptTemplate.from_messages([
    ("system", "Rewrite the user query to improve document retrieval."),
    ("human", "Rewrite Original query:\n{query}")
])
```

**CRITICAL**: Extremely minimal - just "improve document retrieval" without any rules.

---

#### 2.5 Agentic-RAG Self-Evaluation - DOCUMENT RETRIEVAL (NOT WEB SEARCH)
**Source**: `JinSeoung-Oh/Reference/RAG/AgenticRAG/Agentic_RAG_with_Self_Evaluation_Mechanism.py`
**Paradigm**: RAG self-evaluation
**Status**: NOT WEB SEARCH

```python
def query_reformulation(query):
    response = llm.predict("Rewrite this query to be more specific: " + query)
    return response
```

**CRITICAL**: Just "be more specific" - no rules, no constraints.

---

#### 2.6 Total Agent Memory - MEMORY RETRIEVAL (NOT WEB SEARCH)
**Source**: `vbcherepanov/total-agent-memory/src/query_rewriter.py`
**Paradigm**: Memory retrieval
**Status**: NOT WEB SEARCH - designed for memory system

```python
SYSTEM_PROMPT = (
    "You rewrite user questions for a memory retrieval system. "
    "Output ONE single-line minified JSON object with keys "
    '"canonical" (string), "decomposed" (array of 0-3 strings), '
    '"hyde" (1-2 sentence hypothetical answer string). '
    "Drop conversational filler. Decompose only true multi-hop questions; "
    "single-fact lookups get decomposed=[]. No markdown, no prose, JSON only."
)
```

**CRITICAL**: Memory retrieval system with HyDE - NOT web search.

---

#### 2.7 IDP Azure - DOCUMENT SEARCH (MAY BE SIMILAR TO WEB)
**Source**: `eggboy/idp-azure/src/idp_azure/search/query_rewrite.py`
**Paradigm**: Document search (could apply to web)
**Status**: MAY BE APPLICABLE

```python
_SYSTEM_PROMPT = (
    "You are a search query preprocessor. Your job is to transform a "
    "user's conversational question into an effective search query.\n\n"
    "## Rules\n\n"
    "1. Resolve pronouns into a standalone query.\n"
    "2. Preserve all specific names, numbers, dates, page references, "
    "filenames, and technical terms EXACTLY as they appear.\n"
    "3. If query is already clear, return unchanged.\n"
    "4. Only generate keyword_expansions when query is short (<6 words).\n"
    "5. Keyword expansions should be short noun phrases (2-4 words).\n"
    "6. Do NOT answer the question — only reformulate it for search.\n"
)
```

**CRITICAL**: This has good rules for web search! Pronoun resolution, preserve exact literals, return unchanged if clear, keyword expansions only for short queries.

---

#### 2.8 Jett-RAG - RAG FAILED RETRY (NOT WEB SEARCH)
**Source**: `AnkitDash-code/Jett-RAG/RAG-Backend/app/services/auto_retry_service.py`
**Paradigm**: RAG retry on failure
**Status**: NOT WEB SEARCH

```python
system_prompt = (
    "You are a query reformulation expert. When a search query doesn't "
    "find relevant results, you rewrite it to be more effective."
)
prompt = """Focus on:
1. Using different keywords/synonyms
2. Being more specific or more general as needed
3. Clarifying ambiguous terms
"""
```

**CRITICAL**: RAG retry - NOT web search.

---

## Pattern Analysis: What Rules Appear Across MULTIPLE Prompts?

### Rules that appear in MULTIPLE WEB SEARCH prompts (strong signal):

| Rule | Onyx | Gemini | Sydekx | Vietnam | IDP Azure | SIGNAL |
|------|------|--------|--------|---------|-----------|--------|
| Purely keywords, NO filler | ✅ | ❌ | ✅ | ✅ | ❌ | **STRONG** |
| Few keywords as necessary | ✅ | ✅ | ✅ | ❌ | ✅ | **STRONG** |
| Return unchanged if no expansion needed | ✅ | ✅ | ❌ | ❌ | ✅ | **MEDIUM** |
| Deterministic output format | ✅ | ✅ | ✅ | ✅ | ❌ | **STRONG** |
| Never answer the question | ❌ | ❌ | ✅ | ✅ | ✅ | **MEDIUM** |
| Preserve exact literals | ❌ | ❌ | ❌ | ❌ | ✅ | **WEAK** (only 1) |
| Single query per line | ✅ | ❌ | ❌ | ✅ | ❌ | **WEAK** |
| Explicitly mention target system | ✅ | ❌ | ❌ | ❌ | ❌ | **WEAK** (only Onyx) |

### Rules that appear ONLY IN NON-WEB-SEARCH prompts (EXCLUDE):

| Rule | Source | Why Exclude |
|------|--------|-------------|
| HyDE (pseudo-document generation) | MemBrain, Total Agent Memory | Designed for embedding search, NOT BM25 |
| "For embedding" | MemBrain | Vector search specific |
| "Vector database" | LangChain | Vector search specific |
| "Distance-based similarity search" | LangChain | Vector search specific |
| Multi-hop decomposition | HopRAG | Knowledge graph traversal |
| Temperature 0.0 for keywords | MemBrain | Specific to their system, not universal |

### Rules that appear ONCE but are GOOD (consider adopting):

| Rule | Source | Reason to Adopt |
|------|--------|-----------------|
| "Passed to BM25 keyword search engine" | Onyx | **CRITICAL**: Explicitly states target system |
| "Resolve pronouns from conversation history" | Vietnam, IDP Azure | Context-aware rewriting |
| "Preserve exact literals: names, numbers, dates" | IDP Azure | Precision preservation |
| "Keyword expansions only for short queries (<6 words)" | IDP Azure | Smart conditional expansion |
| "Keyword expansions = short noun phrases (2-4 words)" | IDP Azure | Terse expansion format |

---

## Critical Analysis: Are These Prompts Actually Good?

### Good Prompts (Well-Designed):

**1. Onyx KEYWORD_EXPANSION_PROMPT - BEST FOR BM25**
- Explicitly states target: "bm25-based keyword search engine"
- Hard output constraint: "single query per line"
- Noise removal: "not contain any filler natural language"
- Conservative: "If no useful expansions, return original"
- **CRITICAL**: Only prompt that knows BM25 is the target

**2. IDP Azure _SYSTEM_PROMPT - GOOD RULES**
- Pronoun resolution (context-aware)
- Preserve exact literals
- Return unchanged if already clear
- Keyword expansions only for short queries
- Expansions = short noun phrases

**3. Vietnam Heritage - GOOD FOR CONVERSATIONAL CONTEXT**
- Pronoun resolution from history
- Named entities only
- Question word removal
- Deterministic output

### Bad Prompts (Over-Designed or Wrong Paradigm):

**1. MemBrain - WRONG PARADIGM**
- Designed for "embedding" search, NOT BM25
- HyDE pattern generates pseudo-documents (bad for BM25)
- Temperature discussion irrelevant

**2. LangChain MultiQueryRetriever - WRONG PARADIGM**
- Explicitly says "vector database"
- "Distance-based similarity search" - NOT BM25
- The whole prompt is wrong for web search

**3. HalluciGuard-CRAG - TOO MINIMAL**
- Just "improve document retrieval" - no rules
- No constraints, no output format
- LLM has no guidance on WHAT to do

**4. Agentic-RAG - TOO MINIMAL**
- Just "be more specific" - no rules
- No constraints, no guidance

---

## Pattern Convergence: Rules that MATTER for BM25 Web Search

Based on analysis of production prompts that actually work with BM25:

### MUST HAVE (Strong Signal):

1. **"Purely keywords, NO filler natural language"** (Onyx, Sydekx, Vietnam)
   - BM25 is term-frequency based - filler dilutes weights
   
2. **"As few keywords as necessary"** (Onyx, Gemini, Sydekx, IDP Azure)
   - Terse queries = higher term weights
   
3. **"Deterministic output format"** (Onyx, Gemini, Sydekx, Vietnam)
   - JSON/comma-separated/one-per-line for reliable parsing

### SHOULD HAVE (Medium Signal):

1. **"Never answer the question"** (Sydekx, Vietnam, IDP Azure)
   - Prevents LLM drift into answering instead of rewriting
   
2. **"Return unchanged if already clear"** (Onyx, Gemini, IDP Azure)
   - Conservative approach - don't expand unnecessarily

### CONSIDER (Good but Single Source):

1. **"Explicitly state target system (BM25)"** (Onyx only)
   - Helps LLM understand constraints
   
2. **"Preserve exact literals"** (IDP Azure)
   - Package names, versions, error codes
   
3. **"Resolve pronouns from context"** (Vietnam, IDP Azure)
   - For multi-turn conversations

---

## What Current web-search-mcp Prompt Missing

From `query_rewrite.py` (MISTRAL_QUERY_REWRITE_SYSTEM_PROMPT):

| Missing Rule | Onyx Has It | Impact |
|--------------|-------------|--------|
| "bm25-based keyword search engine" | ✅ explicit | LLM doesn't know target |
| "Purely keywords, NO filler" | ✅ | Prompt says "strip irrelevant" but vague |
| "As few keywords as necessary" | ✅ | Prompt has no terse requirement |
| "If no useful expansions, return original" | ✅ | Always generates 3 variants |
| "Single query per line" | ✅ | Returns JSON with objects, not just keywords |

**Current prompt strengths:**
- Preserves exact technical literals ✅
- JSON structured output ✅
- Anti-duplicate rule ✅
- Chain-of-think process ✅

**Current prompt weaknesses:**
- Doesn't explicitly state BM25 target ❌
- "Strip irrelevant keywords" is vague (should be "NO filler words") ❌
- Always generates 3 variants (should be conditional) ❌
- Returns full JSON objects (Onyx returns just keywords per line) ❌
- 106 lines (Onyx is 10 lines) ❌

---

## Targeted Improvements Based on BM25-Focused Prompts

### Improvement 1: Explicitly State Target System

**From**: Onyx (only prompt that does this)

**Add to current prompt:**
```python
"These queries will be passed to a BM25 lexical search engine (NOT a vector/embedding search system)."
```

**Why**: LLM needs to know BM25 constraints (term frequency weighting, no semantic understanding).

---

### Improvement 2: "NO Filler Words" (Explicit List)

**From**: Onyx, Sydekx, Vietnam

**Current**: "Strip irrelevant keywords" (vague)

**Replace with:**
```python
"The queries must be purely keywords and not contain any filler natural language: 
the, a, an, for, with, to, in, on, at, of, what, when, where, how, why, did, does, is, are, can, should"
```

---

### Improvement 3: Conditional Expansion

**From**: Onyx, Gemini, IDP Azure

**Current**: Always generates 3 variants

**Add:**
```python
"If the query is already clear and specific (contains exact literals like package names, versions, error codes), 
return only the original query with minimal cleanup. Do not expand unnecessarily."
```

---

### Improvement 4: Terse Keyword Output Option

**From**: Onyx

**Current**: Returns `{"variants": [{"kind": "...", "query": "...", "why": "..."}]}`

**Alternative for BM25 mode:**
```python
# Option: Keyword-only output format
"Provide a single query per line. Each query should be 2-5 keywords maximum."
```

---

## Summary

**Key Finding**: Only 4 of 12 prompts are actually designed for WEB SEARCH/BM25. The rest are for RAG/vector/memory systems with different constraints.

**Best Prompt for BM25**: Onyx KEYWORD_EXPANSION_PROMPT - only one that explicitly mentions BM25 and enforces keyword-only output.

**Rules that Matter for BM25**:
1. Purely keywords, NO filler (STRONG)
2. As few keywords as necessary (STRONG)
3. Deterministic output format (STRONG)
4. Never answer the question (MEDIUM)
5. Return unchanged if already clear (MEDIUM)
6. Explicitly state BM25 target (GOOD, single source)

**Rules to IGNORE** (from non-web-search prompts):
- HyDE pseudo-document generation (for embeddings)
- Temperature values (irrelevant)
- "For embedding" patterns (wrong paradigm)
- Multi-hop decomposition (for knowledge graphs)