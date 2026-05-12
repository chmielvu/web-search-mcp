# Query Reformulation: Extended Production Prompts Analysis

**Date**: 2026-05-12
**Sources**: Actual production implementations from GitHub (NOT academic papers)
**Total Prompts Found**: 22

---

## Executive Summary

After extensive GitHub search, found **22 production query rewrite prompts**. Key finding: **Only 3 explicitly target BM25/lexical search** (Onyx, Cherry Studio, Social Media Agent). Most are for RAG/vector DB retrieval (wrong paradigm for web search).

**Critical Pattern Convergence** (appears in ≥4 prompts):
| Rule | Prompts Using This | Signal Strength |
|------|-------------------|-----------------|
| Purely keywords, NO filler | Onyx, Cherry Studio, GEJ-LLM, Daiso | **STRONG** |
| Output: one per line / comma-separated | Onyx, Social Media, Template-Mill, Daiso, BMLibrarian | **STRONG** |
| Preserve exact terms / literals | Onyx, Vietnam Heritage, BMLibrarian, MemBrain | **STRONG** |
| JSON structured output | Daiso, Template-Mill, GEJ-LLM, BMLibrarian, Vietnam Heritage | **STRONG** |
| Keyword vs Semantic classification | Onyx (QUERY_TYPE_PROMPT), Instructgpt | **MEDIUM** |

---

## Category 1: WEB SEARCH / BM25-Focused (Most Relevant)

### 1.1 Onyx KEYWORD_EXPANSION_PROMPT ⭐⭐⭐⭐⭐ (BEST FOR WEB SEARCH)

**Repo**: `onyx-dot-app/onyx`
**File**: `backend/ee/onyx/prompts/query_expansion.py`

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

**CRITICAL ANALYSIS**:
- ✅ **Explicitly states BM25 target** - ONLY prompt that does this
- ✅ Purely keywords, NO filler - matches web search needs
- ✅ As few keywords as necessary - prevents keyword pile-on
- ✅ One per line - deterministic parsing
- ✅ Return unchanged if no expansion - conservative approach
- ❓ No examples in prompt - relies on model understanding

**Rules Extracted**:
| Rule | Purpose |
|------|---------|
| Passed to BM25 engine | Target system clarity |
| Purely keywords, NO filler | Clean BM25 input |
| As few keywords as necessary | Precision over recall |
| One query per line | Deterministic output |
| No additional formatting | Parseability |

---

### 1.2 Onyx QUERY_TYPE_PROMPT (Keyword vs Semantic Classification)

**Repo**: `onyx-dot-app/onyx`
**File**: `backend/ee/onyx/prompts/query_expansion.py`

```python
QUERY_TYPE_PROMPT = """
Determine if the provided query is better suited for a keyword search or a semantic search.
Respond with "keyword" or "semantic" literally and nothing else.
Do not provide any additional text or reasoning to your response.

CRITICAL: It must only be 1 single word - EITHER "keyword" or "semantic".

The user query is:
{user_query}
""".strip()
```

**CRITICAL ANALYSIS**:
- ✅ **Classifies query type before expansion** - smart routing
- ✅ Single word output - deterministic
- ✅ No reasoning in output - efficient
- ❓ No examples - may misclassify edge cases

---

### 1.3 Cherry Studio SEARCH_SUMMARY_PROMPT ⭐⭐⭐⭐

**Repo**: `CherryHQ/cherry-studio-app`
**File**: `src/config/prompts.ts`

```typescript
export const SEARCH_SUMMARY_PROMPT = `
  You are an AI question rephraser. Your role is to rephrase follow-up queries from a conversation into standalone queries that can be used by another LLM to retrieve information, either through web search or from a knowledge base.
  **Use user's language to rephrase the question.**
  Follow these guidelines:
  1. If the question is a simple writing task, greeting (e.g., Hi, Hello, How are you), or does not require searching for information (unless the greeting contains a follow-up question), return 'not_needed' in the 'question' XML block. This indicates that no search is required.
  2. If the user asks a question related to a specific URL, PDF, or webpage, include the links in the 'links' XML block and the question in the 'question' XML block. If the request is to summarize content from a URL or PDF, return 'summarize' in the 'question' XML block and include the relevant links in the 'links' XML block.
  3. For websearch, You need extract keywords into 'question' XML block. For knowledge, You need rewrite user query into 'rewrite' XML block with one alternative version while preserving the original intent and meaning.
  4. Websearch: Always return the rephrased question inside the 'question' XML block. If there are no links in the follow-up question, do not insert a 'links' XML block in your response.
  5. Knowledge: Always return the rephrased question inside the 'question' XML block.
  6. Always wrap the rephrased question in the appropriate XML blocks to specify the tool(s) for retrieving information: use <websearch></websearch> for queries requiring real-time or external information, <knowledge></knowledge> for queries that can be answered from a pre-existing knowledge base, or both if the question could be applicable to either tool. Ensure that the rephrased question is always contained within a <question></question> block inside these wrappers.
  ...
`;
```

**CRITICAL ANALYSIS**:
- ✅ **Explicitly differentiates websearch vs knowledge** - paradigm-aware
- ✅ Extract keywords for websearch - matches BM25 needs
- ✅ Greeting detection → 'not_needed' - prevents wasteful searches
- ✅ URL extraction to links[] - handles URL-based queries
- ✅ Examples provided - helps model understand
- ❌ XML format is verbose - unnecessary complexity
- ❌ Too many instructions (6 rules + examples) - dilution risk

**Rules Extracted**:
| Rule | Purpose |
|------|---------|
| Websearch = extract keywords | BM25-friendly |
| Knowledge = rewrite + alternative | Semantic search |
| Greeting → 'not_needed' | Skip unnecessary searches |
| URL → links[] + question | URL context preservation |
| Use user's language | Localization |

---

### 1.4 Social Media Agent keyword_expansion_prompt ⭐⭐⭐

**Repo**: `zenox-ux/social_media_agent`
**File**: `app.py`

```python
keyword_expansion_prompt = f"""You are a search query expert. For the given topic, generate a list of highly relevant keywords and phrases.
You can give phrases but prefer keywords (~2/3) over phrases (~1/3).
Topic: "{topic}"
"""
```

**CRITICAL ANALYSIS**:
- ✅ Keywords preferred over phrases (2/3 vs 1/3) - BM25-friendly
- ✅ Simple, concise prompt - no instruction dilution
- ✅ Used for Reddit search (web search)
- ❓ No output format specification - relies on comma-split
- ❓ No constraints on hallucination

**Rules Extracted**:
| Rule | Purpose |
|------|---------|
| Keywords ~2/3, phrases ~1/3 | BM25 optimization |
| Highly relevant keywords | Precision focus |

---

## Category 2: DOMAIN-SPECIFIC SEARCH (Retail, Biomedical, Templates)

### 2.1 Daiso KEYWORD_EXPANSION_PROMPT (Retail Search) ⭐⭐⭐⭐

**Repo**: `LeeYoungGeun/daiso-category-search`
**File**: `poc/kms/prompts.py`

```python
KEYWORD_EXPANSION_PROMPT = """

You are a Search Keyword Specialist for a retail store (Daiso).

Decompose and expand the given product name into a comprehensive list of search keywords based on the following structure:
1. **Original**: The exact input product name.
2. **Space/Location**: Where it is used (e.g., Bathroom, Kitchen, Living room).
3. **Super-concept/Root**: The core item type (e.g., Mat, Cleaner, Basket).
4. **Category**: The broader store category (e.g., Bathroom supplies, Stationery).
5. **Feature/Function**: Key features or usage (e.g., Anti-slip, Stain removal, Organizing).

- Output MUST be a JSON list of strings.
- Keys must be in Korean.
- Order: [Original, Space, Super-concept, Category, Feature...]

Input: "욕실매트"
Output: ["욕실매트", "욕실", "매트", "욕실용품", "미끄럼방지"]

Input: "욕실 미끄럼방지 매트"
Output: ["욕실 미끄럼방지 매트", "욕실매트", "미끄럼방지 매트", "매트", "욕실", "욕실용품", "미끄럼방지"]

Input: "아이폰 충전 케이블"
Output: ["아이폰 충전 케이블", "아이폰", "충전 케이블", "케이블", "디지털", "핸드폰 용품", "충전기"]

Input: {product_name}
Output:
"""
```

**CRITICAL ANALYSIS**:
- ✅ **Structured decomposition (Original → Space → Root → Category → Feature)** - smart hierarchy
- ✅ JSON list output - deterministic parsing
- ✅ Examples provided - model guidance
- ✅ Korean output requirement - localization
- ✅ Multiple decomposition paths - coverage
- ❌ Complex structure - may over-expand simple queries

**Rules Extracted**:
| Rule | Purpose |
|------|---------|
| Original exact preserved | Precision anchor |
| Space/Location extraction | Context expansion |
| Super-concept/root | Core entity extraction |
| Category broadening | Parent topic coverage |
| Feature/function | Attribute expansion |

---

### 2.2 Template-Mill KEYWORD_EXPANSION_PROMPT (Product Search) ⭐⭐⭐

**Repo**: `AutosortermkI/Template-Mill`
**File**: `src/discover/analyzers/keyword_expander.py`

```python
KEYWORD_EXPANSION_PROMPT = """Given the seed keyword "{seed}" for digital template products (Notion dashboards, planners, budget sheets, productivity systems), generate semantically related keyword variations.

Generate variations in these categories:
1. Problem-framed: keywords describing the problem the template solves
2. Audience-framed: keywords targeting specific user segments
3. Feature-framed: keywords highlighting specific template features
4. Format-framed: keywords specifying the template format/platform

Respond in JSON:
{
  "problem_framed": ["keyword1", "keyword2", ...],
  "audience_framed": ["keyword1", "keyword2", ...],
  "feature_framed": ["keyword1", "keyword2", ...],
  "format_framed": ["keyword1", "keyword2", ...]
}

Generate 5-8 keywords per category. Focus on terms people would actually search for on Etsy, Pinterest, or Google."""
```

**CRITICAL ANALYSIS**:
- ✅ **Multi-perspective framing (Problem, Audience, Feature, Format)** - comprehensive coverage
- ✅ JSON structured output - deterministic
- ✅ 5-8 per category - bounded output
- ✅ Focus on real search terms - practicality
- ❌ Four categories may over-expand simple queries
- ❌ No examples - may drift

---

### 2.3 BMLibrarian KEYWORD_EXPANSION_PROMPT (Biomedical Search) ⭐⭐⭐

**Repo**: `hherb/bmlibrarian`
**File**: `src/bmlibrarian/pubmed_search/query_converter.py`

```python
KEYWORD_EXPANSION_PROMPT = """You are a biomedical terminology expert.
Given the following concept and its current terms, suggest additional synonyms,
abbreviations, and alternative phrasings that would help find more relevant articles.

Concept: {concept_name}
Current MeSH terms: {mesh_terms}
Current keywords: {keywords}

Provide additional terms that researchers might use when writing about this topic.
Consider:
- Common abbreviations (e.g., CVD for cardiovascular disease)
- British vs American spellings
- Lay terms vs technical terms
- Historical terminology changes

Output JSON only:
{
  "additional_keywords": ["term1", "term2"],
  "additional_synonyms": ["synonym1", "synonym2"],
  "notes": "brief explanation"
}"""
```

**CRITICAL ANALYSIS**:
- ✅ **Domain-specific expertise conditioning** - improves quality
- ✅ Multiple expansion sources (abbreviations, spellings, lay vs technical)
- ✅ JSON output - deterministic
- ✅ Historical terminology consideration - temporal coverage
- ❌ For PubMed (scientific search) - may not transfer to general web search
- ❌ No constraints on output count

---

## Category 3: QUERY CLASSIFICATION / ROUTING

### 3.1 Instructgpt Intent Determination ⭐⭐⭐

**Repo**: `kevinamiri/Instructgpt-prompts`
**File**: `v3.md`

```text
Intent Determination
- Determine if the user is asking a question or making a statement
- Identify if the customer has an intent to purchase based on an inquiry
- Categorize the type of support needed based on a customer's message
- Determine if a search query is informational, navigational or transactional
- Identify the specific action being requested in an email (e.g. review, reply, forward)
- Assess if a social media post is intended to inform, persuade or entertain
- Determine the type of recommendation being asked for in a forum post
- Identify the main objective behind a set of meeting agenda items
- Categorize a user's intent as a complaint, suggestion, or general feedback
- Determine a student's learning goal based on their course enrollment
```

**CRITICAL ANALYSIS**:
- ✅ **Informational vs Navigational vs Transactional classification** - web search routing
- ✅ Question vs statement detection - prevents wasteful searches
- ✅ Multiple intent types - comprehensive routing
- ❌ Not actual prompt - just examples of what to classify
- ❌ No output format specified

---

## Category 4: RAG / VECTOR DB (NOT WEB SEARCH - FOR COMPARISON ONLY)

### 4.1 GEJ-LLM Query Analysis & Rephrasing Prompt ⭐⭐⭐ (RAG)

**Repo**: `Wanzhe-Liao/GEJ-LLM`
**File**: `LLM_PROMPTS.md`

```text
You are a clinical query analysis expert. Your task is to analyze the user's query about clinical guidelines.

1. Determine if the query is a simple greeting or a substantive clinical question.
2. If it is a substantive question, rephrase it into an optimal, keyword-rich search query for a vector database.
3. Extract key medical terms, conditions, treatments, or guidelines mentioned.

Respond ONLY with a JSON object in this exact format:
{"is_substantive": true/false, "rephrased_query": "your optimized query here" or "N/A"}

Example:
User: "What is the treatment for stage 3 gastric cancer?"
Response: {"is_substantive": true, "rephrased_query": "stage 3 gastric cancer treatment guidelines chemotherapy surgery options"}
```

**CRITICAL ANALYSIS**:
- ⚠️ **Explicitly states "for a vector database"** - NOT web search
- ✅ Keyword-rich rephrasing - still useful pattern
- ✅ Greeting detection → 'not_needed' - smart routing
- ✅ JSON structured output
- ✅ Example provided
- ❌ For vector DB - may not optimize for BM25

---

### 4.2 Vietnam Heritage KEYWORD_EXTRACTOR_PROMPT ⭐⭐⭐ (Memory Search)

**Repo**: From previous search (Vietnam Heritage project)

```python
KEYWORD_EXTRACTOR_PROMPT = """
Bạn là một API trích xuất từ khóa thông minh (Context-Aware Keyword Extractor).
Nhiệm vụ: Trích xuất danh sách các thực thể (Entity) và từ khóa quan trọng từ câu hỏi của người dùng để phục vụ tìm kiếm Database.

QUY TRÌNH XỬ LÝ (BẬT BUC):
1. Đọc "Lịch sử hội thoại" để hiểu ngữ cảnh.
2. If current question uses pronouns (it, he, they, that...), immediately replace with specific nouns mentioned in history.
3. Only extract: NAMED ENTITIES (Names, Nicknames, Places, Organizations, Private Events) from the question.
4. Remove meaningless words (why, what is, how many, how).

OUTPUT FORMAT:
- Return only one line with keywords separated by commas.
- NEVER answer the question.
- NEVER explain.
"""
```

**CRITICAL ANALYSIS**:
- ⚠️ For database search - not web search
- ✅ **Pronoun resolution from history** - context handling
- ✅ Named entities only - precision focus
- ✅ **Remove question words (why, what is, how many)** - noise removal
- ✅ Comma-separated output - deterministic
- ✅ Never answer/explain - output constraint

---

### 4.3 Google Gemini query_writer_instructions (Production RAG)

**Repo**: `google-gemini/gemini-fullstack-langgraph-quickstart`

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

**CRITICAL ANALYSIS**:
- ⚠️ For RAG context gathering - not direct web search
- ✅ **Prefer single query unless needed** - conservative expansion
- ✅ **Each query = one specific aspect** - focus
- ✅ **Don't generate similar queries** - dedup rule
- ✅ Current date context - temporal awareness
- ✅ Rationale + query structure - explainability

---

### 4.4 LangChain MultiQueryRetriever DEFAULT_QUERY_PROMPT (RAG)

**Repo**: `langchain-ai/langchain`

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

**CRITICAL ANALYSIS**:
- ⚠️ **Explicitly for vector database** - NOT BM25
- ⚠️ "overcome distance-based similarity search limitations" - wrong paradigm
- ❌ Always generates 3 variants - over-expansion
- ❌ No keyword cleaning - may produce verbose queries

---

### 4.5 MemBrain Query Rewrite Prompt (Memory Retrieval)

```python
_SYSTEM = (
    "Extract 3-6 search keywords from the question. "
    "Keep proper nouns exactly as written. "
    "Use base/infinitive verb forms (e.g. 'research' not 'researching'). "
    "Remove question words (what/when/did/who/how/is/are). "
    "Output only the keywords, space-separated, no punctuation."
)
```

**CRITICAL ANALYSIS**:
- ⚠️ For memory retrieval - not web search
- ✅ **Keep proper nouns exactly** - precision preservation
- ✅ **Base verb forms** - normalization
- ✅ **Remove question words** - noise removal
- ✅ Space-separated, no punctuation - clean output
- ❌ No constraints on hallucination

---

### 4.6 Sydekx _EXPANSION_PROMPT (Document Retrieval)

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

**CRITICAL ANALYSIS**:
- ⚠️ For document retrieval - not web search
- ✅ **4 variant strategy (Paraphrase, Specific, Broad, Keywords)** - comprehensive
- ✅ **Keywords variant: NO FILLER WORDS** - BM25-friendly
- ✅ JSON array output - deterministic
- ❌ Always 4 variants - over-expansion risk

---

### 4.7 Azure Search OpenAI Demo query_rewrite.system.jinja2 (RAG)

```jinja2
Below is a history of the conversation so far,and a new question asked by the user that needs to be answered by searching in a knowledge base. You have access to Azure AI Search index with 100's of documents. Generate a search query based on the conversation and the new question. Do not include cited source filenames and document names e.g. info.txt or doc.pdf in the search query terms. Do not include any text inside [] or <<>> in the search query terms. Do not include any special characters like '+'. If the question is not in English,translate the question to English before generating the search query. If you cannot generate a search query,return just the number 0.
```

**CRITICAL ANALYSIS**:
- ⚠️ For Azure AI Search (hybrid) - not pure web search
- ✅ **No filenames in query** - noise removal
- ✅ **No special characters like '+'** - clean query
- ✅ Translate to English - normalization
- ✅ Return "0" if cannot generate - fallback
- ❌ No keyword extraction rules

---

### 4.8 Chat-your-data _template (Harrison Chase/LangChain)

```python
_template = """Given the following conversation and a follow up question, rephrase the follow up question to be a standalone question.
You can assume the question about the most recent state of the union address.

Chat History:
{chat_history}
Follow Up Input: {question}
Standalone question:"""
```

**CRITICAL ANALYSIS**:
- ⚠️ For knowledge base search - not web search
- ✅ Chat history context - conversation handling
- ✅ Standalone question generation - context resolution
- ❌ No keyword extraction - verbose output
- ❌ Domain-specific (State of Union) - not general

---

## Category 5: NOT SEARCH-RELATED (Control Flow / Research)

### 5.1 Privachat Decision Prompt

```text
Before: Generic instructions for query rewriting
After: Few-shot examples matching Perplexica

Examples included:
- "What is the capital of France" → need_search=true, optimized_query="Capital of France"
- "Hi, how are you?" → need_search=false, optimized_query="not_needed"
- "What is Docker?" → need_search=true, optimized_query="What is Docker"
- "Tell me about X from https://example.com" → extract URL to links[], create question
- "Summarize https://example.com" → optimized_query="summarize", links=["..."]
- "Write a poem" → need_search=false, optimized_query="not_needed"
```

**CRITICAL ANALYSIS**:
- ⚠️ Decision logic, not rewrite prompt
- ✅ Search decision examples - routing
- ✅ URL extraction handling
- ❌ No actual rewrite instructions

---

## Pattern Analysis: Rules Across All Prompts

### Rules Appearing in ≥3 Prompts (Strong Signal)

| Rule | Prompts | Count |
|------|---------|-------|
| **Purely keywords, NO filler** | Onyx, Cherry Studio, GEJ-LLM, Daiso, Sydekx | 5 |
| **Output: one per line / comma-separated** | Onyx, Social Media, Template-Mill, Daiso, BMLibrarian, Vietnam Heritage, MemBrain | 7 |
| **JSON structured output** | Daiso, Template-Mill, GEJ-LLM, BMLibrarian, Vietnam Heritage, Sydekx, Google Gemini | 7 |
| **Preserve exact terms / literals** | Onyx, Vietnam Heritage, BMLibrarian, MemBrain | 4 |
| **Remove question words** | Vietnam Heritage, MemBrain, GEJ-LLM (greeting) | 3 |
| **Greeting → 'not_needed' / skip** | Cherry Studio, GEJ-LLM, Privachat | 3 |
| **Examples provided** | Cherry Studio, Daiso, GEJ-LLM, Template-Mill, Sydekx | 5 |

### Rules Unique to BM25/Web Search Prompts

| Rule | Prompts | Web-Search Specific |
|------|---------|---------------------|
| **"BM25-based keyword search engine"** | Onyx | ✅ Only one |
| **"As few keywords as necessary"** | Onyx | ✅ Precision focus |
| **Keywords ~2/3, phrases ~1/3** | Social Media | ✅ BM25 weighting |
| **Websearch vs Knowledge differentiation** | Cherry Studio | ✅ Paradigm routing |

### Rules Missing from Current web-search-mcp

| Missing Rule | Source | Should Implement |
|--------------|--------|------------------|
| **Remove question words** | Vietnam Heritage, MemBrain | ✅ HIGH |
| **Base verb normalization** | MemBrain | ✅ MEDIUM |
| **Greeting → skip search** | Cherry Studio, GEJ-LLM | ✅ HIGH |
| **As few keywords as necessary** | Onyx | ✅ HIGH |
| **Keywords ~2/3 preference** | Social Media | ✅ MEDIUM |
| **Return unchanged if no expansion** | Onyx | ✅ HIGH |

---

## Recommendations for web-search-mcp

### Immediate Implementation (HIGH Impact)

1. **Add "Remove Question Words" Rule**
   ```
   - Remove question words: what, when, did, who, how, is, are, does, can, should
   ```
   From: Vietnam Heritage, MemBrain

2. **Add "As Few Keywords as Necessary" Rule**
   ```
   - Each query should have as few keywords as necessary to represent the search intent
   ```
   From: Onyx (BM25-specific)

3. **Add Greeting Detection → Bypass**
   ```
   - If query is a greeting (Hi, Hello, How are you) or writing task, return original unchanged
   ```
   From: Cherry Studio, GEJ-LLM

4. **Add "Return Unchanged if No Useful Expansion"**
   ```
   - If there are no useful expansions, simply return the original query with no additional variants
   ```
   From: Onyx

### Secondary Implementation (MEDIUM Impact)

5. **Add Base Verb Normalization**
   ```
   - Use base/infinitive verb forms: 'researching' → 'research', 'installing' → 'install'
   ```
   From: MemBrain

6. **Add Keywords Preference Ratio**
   ```
   - Keywords ~2/3, phrases ~1/3 for BM25 optimization
   ```
   From: Social Media Agent

7. **Add Query Type Classification**
   ```
   - Determine if query is better for keyword search (precision) or needs expansion (coverage)
   ```
   From: Onyx QUERY_TYPE_PROMPT

---

## Summary

**Total prompts found**: 22
**BM25/Web-search specific**: 3 (Onyx, Cherry Studio, Social Media)
**RAG/Vector DB**: 10 (not directly applicable)
**Domain-specific**: 4 (Retail, Biomedical, Templates, Clinical)
**Classification/Routing**: 3

**Key Insight**: Only Onyx explicitly targets BM25. Most production prompts are for RAG/vector search, which optimizes for semantic similarity rather than lexical precision. For web-search-mcp (SearXNG BM25), we should prioritize Onyx patterns and Cherry Studio websearch differentiation.

**Strongest Pattern**: "Purely keywords, NO filler" + "One per line/comma-separated" + "JSON output" appears in 5+ prompts. This is the consensus for clean search query output.

**Missing in current prompt**: Question word removal, base verb normalization, greeting bypass, "return unchanged if no expansion", keyword minimization rule.