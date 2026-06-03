# Unified Query Understanding: Redesign Plan

**Date**: 2026-06-03
**Status**: Draft for review
**Depends on**: entity-aware-result-memory-plan.md, GLiNER2 integration

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Red Team: Current System](#2-red-team-current-system)
3. [Research Findings](#3-research-findings)
4. [Proposed Architecture](#4-proposed-architecture)
5. [Multi-Axis Classification Ontology](#5-multi-axis-classification-ontology)
6. [FunctionGemma: Search Program Generator](#6-functiongemma-search-program-generator)
7. [GLiNER2 Integration: Entity-Aware Routing](#7-gliner2-integration-entity-aware-routing)
8. [Pipeline Integration](#8-pipeline-integration)
9. [Migration Phases](#9-migration-phases)
10. [Risk Analysis](#10-risk-analysis)

---

## 1. Executive Summary

The current query classification system has **three disconnected layers** that don't communicate: a heuristic precision detector (`query_policy.py`), a 3-class intent classifier (FunctionGemma via prompts), and a keyword-based content type heuristics (`content_type.py`). Each was built ad-hoc and each is inadequate for its role.

This plan proposes a **unified query understanding pipeline** based on Daniel Tunkelang's three-part framework (Holistic → Reductionist → Resolution), using:

- **GLiNER2** for reductionist entity extraction (what specific things is the query about?)
- **FunctionGemma** in its **native function-calling mode** for holistic+resolution (which search functions to call, with what parameters?)
- **6 derived classification axes** (domain, task_type, granularity, freshness, complexity, confidence) extracted from the search program output

The key insight: instead of asking FunctionGemma "classify this query," we ask it **"generate a search program"** — selecting and parameterizing search provider functions. This is what FunctionGemma was trained to do. The classification signals are **derived properties** of the search program, not separate classification steps. This eliminates `query_policy.py` and `content_type.py` as separate modules entirely — their functionality is absorbed.

---

## 2. Red Team: Current System

### 2.1 Architecture (as-is)

```
Raw Query
    │
    ├──► query_policy.py (heuristic regex)
    │     → binary: bypass / expand
    │     → must_keep_terms (extracted literals)
    │
    ├──► query_classifier_client.py → FunctionGemma (Cloud Run)
    │     → intent: code | general_research | comparison
    │     → should_decompose: bool
    │     → confidence: float
    │     → routing: {keyword: bool, neural: bool, community: bool}
    │
    ├──► query_decomposition.py → FunctionGemma (same call)
    │     → sub_questions: [{question, target, why, weight}]
    │
    └──► content_type.py (keyword heuristic, used only for page cache TTL)
          → ContentType: technical | news | faq | general
```

### 2.2 Critical Weaknesses

#### W1: 3-class intent is a dumping ground

`"code"` captures everything from debug queries to API lookups to installation to conceptual questions about programming. These need **completely different** search strategies:

| Query | Current Intent | Optimal Strategy |
|---|---|---|
| "ECONNREFUSED Node.js TCP socket" | code | keyword exact-match + community issues |
| "FastMCP ResourceAsTools decorator" | code | docs search + API reference |
| "install PyTorch with CUDA 12.4" | code | keyword + docs + version constraint |
| "how does async/await work in Rust" | code | semantic search + academic + tutorials |

`"general_research"` is even worse — history, architecture patterns, market analysis, and "what is RAG" all get the same treatment. `"comparison"` isn't a goal — it's a **query structure** (multi-entity), not an intent.

#### W2: No domain awareness

The classifier doesn't know if you're asking about software, biology, finance, or law. Domain determines **which sources are authoritative**. Software queries need docs + GitHub + StackOverflow. Academic queries need arXiv + papers. Business queries need reports + news. Without domain, the system can't make intelligent source selection.

#### W3: No temporal/freshness signal

"What changed in React 19" vs "How does React work" — totally different freshness needs, same classification. Real-time queries need news providers and recent indexing. Evergreen queries can use result memory aggressively. The system can't tell the difference.

#### W4: No granularity signal

"Python" vs "Python GIL implementation in CPython 3.12 posix thread scheduling" — the first needs broad exploration, the second needs surgical precision. Both get `routing.keyword=true, neural=true` with no weighting guidance.

#### W5: Provider routing is too coarse

`keyword/neural/community` conflates multiple orthogonal dimensions. A "code" query might need exact-match keyword for error codes OR conceptual neural search for architecture patterns. Boolean flags can't express "weight keyword 2x over neural for this query."

#### W6: FunctionGemma is massively underutilized

The current system uses FunctionGemma as a **prompt-based JSON classifier** with a custom 3-class enum schema. But FunctionGemma was specifically trained for **function calling** — selecting which functions to invoke with what parameters. The model has 256K vocabulary tokens including structural JSON tokens (`<start_function_call>`, etc.) that make structured output reliable. Using it for prompt-based classification is like using a Formula 1 car for city commuting.

Evidence:
- Multi-agent router experiment: Base FunctionGemma achieves ~58% on custom routing, but **fine-tuned achieves 89.4%** when used in its native function-calling format (DEV community blog)
- The same experiment showed that **format mismatch** (even minor prompt deviations) drops accuracy from 89% to 62%
- FunctionGemma's special tokens are a VASTLY more reliable output format than free-form JSON from a 270M model

#### W7: content_type.py is isolated and redundant

The 4-class content type (technical/news/faq/general) is used only for page cache TTL. It has zero feedback to the main classification pipeline. It's keyword-based (`{"how","implement","code","api"...}`) and will be **completely subsumed** by entity-aware classification.

#### W8: Decomposition is binary and late

`should_decompose` is boolean. A query with 2 sub-goals and 5 sub-goals gets the same treatment (decompose = true). Decomposition also happens AFTER classification, instead of being an integral part of understanding query structure.

#### W9: No entity awareness in classification

Entities from GLiNER2 (planned) could directly inform classification: "query mentions 2 specific software packages + an error code" → high-confidence debug domain. There's no hook for this in the current pipeline.

#### W10: Three systems, zero communication

`query_policy.py`, FunctionGemma classifier, and `content_type.py` are three independent classifiers with no shared context. Precision signals from query_policy don't inform FunctionGemma's routing. Content type doesn't inform variant generation. This is a missed opportunity for compositional intelligence.

---

## 3. Research Findings

### 3.1 Tunkelang's Query Understanding Framework

**Source**: Daniel Tunkelang (eBay), "Query Understanding, Divided into Three Parts"

Three stages:
1. **Holistic understanding**: Broad classification — topic, category, high-level intent
2. **Reductionist understanding**: Segmentation + entity recognition — break query into components and classify each
3. **Resolution**: Transform holistic + reductionist understanding into a search program (the query executed against the engine)

Key insight: "Holistic understanding makes it possible to select the right model for reductionist understanding — one that corresponds to the right language, category, etc." This is a **cascade**, not a parallel system.

**Application to our design**: GLiNER2 does reductionist (entity extraction). FunctionGemma does holistic+resolution (search program generation). The cascade is natural: entities feed into FunctionGemma's context.

### 3.2 REIC: RAG-Enhanced Intent Classification

**Source**: arXiv 2506.00210v1

Key findings:
- Hierarchical intent classification outperforms flat classification at scale
- REIC uses RAG to retrieve similar (query, intent) pairs, then uses an LLM to score candidate intents
- Hierarchical classification reduces per-head complexity: "each classification head only needs to handle less than 50 intents"
- Fine-tuned Mistral-7B with RAG achieves F1=0.572, outperforming Claude zero-shot (0.227), Claude few-shot (0.329), and Claude+RAG (0.419)
- **Critical**: Constrained decoding (probability-based reranking) prevents LLM hallucination of invalid intents

**Application**: Our search program approach is a form of constrained decoding — FunctionGemma can only output valid function calls, not arbitrary intent strings. This prevents hallucination by design.

### 3.3 Vonage Hierarchical Intent Classification

**Source**: Vonage AI Studio, "How to Build an Intent Classification Hierarchy"

Key findings:
- **Group by nouns first, then verbs** (not the reverse)
- Nouns = subjects/products, verbs = actions on those subjects
- Group by greatest differentiation to eliminate overlap and ambiguity
- At scale (50+ intents), hierarchy reduces misclassification dramatically
- Some agents saw **55% increase in successful calls** and **83% reduction in human escalations**
- For ambiguous cases, add a "catch" intent at the highest level

**Application**: Our Domain axis (nouns: software, infrastructure, academic, etc.) comes before Task Type (verbs: debug, implement, compare, etc.). This matches the empirically validated best practice.

### 3.4 Content Harmony Multi-Axis Scoring

**Source**: Content Harmony, "There's a Better Way to Classify Search Intent"

Key findings:
- 9 intent types scored on 0-3 scale (not boolean)
- Multi-axis scoring naturally handles overlapping intents
- Primary intent = highest scoring axis; secondary/tertiary intents matter too
- JSON output format: `{"primary_intent": "Transactional", "intent_scores": {"Transactional": 3, "Branded": 2, ...}}`
- **Format and intent are intertwined** — a video intent means the user needs video results

**Application**: Our 6 derived axes are scored independently, not as a single label. A query can be high on both `debug` and `precise` — they're orthogonal, not competing.

### 3.5 Perplexity Search as Code (SaC)

**Source**: Perplexity Research, "Rethinking Search as Code Generation"

Key insights:
- Monolithic search pipelines fail for complex tasks — rigid interfaces prevent models from leveraging domain knowledge
- **Search primitives as composable building blocks** exposed via SDK
- Model generates code that assembles task-specific retrieval pipelines
- Three failure modes of monolithic search: coarse context, failure to leverage domain knowledge, inefficient control flow
- "Agents benefit from parsimony, and it would be inefficient for the SDK to cover every potential operation with a dedicated function"

**Application**: Our search provider functions ARE the composable building blocks. FunctionGemma generates the "program" (function calls), not just a label. This is micro-SaC adapted for an MCP server context.

### 3.6 FunctionGemma Deep Analysis

**Architecture**:
- 270M parameter model based on Gemma 3
- Custom chat template with special tokens: `<start_function_declaration>`, `<end_function_declaration>`, `<start_function_call>`, `<end_function_call>`
- 256K vocabulary includes JSON structural elements as single tokens
- ~550MB RAM (FP16), ~180MB (INT4)
- Trained on 6 trillion tokens

**Capabilities**:
- **Trained for**: Function calling — selecting tools and generating arguments
- **NOT trained for**: General conversation, open-ended text generation
- **Execution ability** (format/syntax): Strong — special tokens make output reliable
- **Cognitive ability** (intent/routing): Weak out-of-box, requires fine-tuning for domain-specific routing

**Benchmarks** (0-shot):
- BFCL Simple: 61.6% | Multiple: 63.5% | Parallel: 39.0%
- Relevance: 61.1% | Irrelevance: 73.7%

**Multi-Agent Router Experiment**:
- Fine-tuned FunctionGemma to route across 7 e-commerce agents
- Accuracy: keyword matching 52-58% → fine-tuned FunctionGemma **89.4%**
- Training: 45 minutes on T4 GPU, 12,550 examples, LoRA r=16
- Latency: 127ms avg (T4 GPU), 287ms P95
- **Critical**: Format mismatch drops accuracy to 62%. Must use the EXACT same format in training and inference.

**Fine-tuning capabilities**:
- Can handle custom JSON schemas after fine-tuning
- Optimizing context usage: "bake" tool definitions into weights via fine-tuning
- Resolving selection ambiguity: bias toward domain-specific policies
- Model distillation: use larger model outputs as training data

**Key implication for us**: FunctionGemma should be used in its **native function-calling format**, not as a prompt-based JSON classifier. The current system is fighting the model's training. Using `<start_function_declaration>` + `<start_function_call>` tokens will dramatically improve reliability and accuracy.

---

## 4. Proposed Architecture

### 4.1 Unified Query Understanding Pipeline

```
Raw Query
    │
    ▼
╔═══════════════════════════════════════════════════╗
║ STAGE 1: REDUCTIONIST — Entity Extraction (GLiNER2) ║
╠═══════════════════════════════════════════════════╣
║ Input:  raw query                                  ║
║ Output: entities + must_keep_terms + entity_types  ║
║ Latency: ~25ms                                      ║
║                                                     ║
║ Subsumes: query_policy.py precision detection       ║
║ (entity types: error_code, version, url, etc.      ║
║  directly encode precision signals)                 ║
╚═══════════════════════════════════════════════════╝
    │ entities, must_keep_terms
    ▼
╔═══════════════════════════════════════════════════════╗
║ STAGE 2: HOLISTIC + RESOLUTION — Search Program (FunctionGemma) ║
╠═══════════════════════════════════════════════════════╣
║ Input:  raw query + entity annotations                ║
║ Mode:   Function-calling (native format)               ║
║ Tools:  keyword_search, semantic_search,               ║
║         community_search, docs_search, academic_search ║
║ Output: list of function calls = search program        ║
║ Derived: 6-axis classification signals                  ║
║ Latency: ~127ms local / ~300ms Cloud Run               ║
║                                                        ║
║ Subsumes: intent classification, provider routing,     ║
║ content type classification, decomposition decision    ║
╚═══════════════════════════════════════════════════════╝
    │ search_program + classification_signals
    ▼
╔══════════════════════════════════════════════════════╗
║ STAGE 3: EXECUTION — Pipeline Assembly               ║
╠══════════════════════════════════════════════════════╣
║ • Map function calls → concrete providers + variants ║
║ • Provider routing from which functions selected       ║
║ • Variant count from complexity + granularity           ║
║ • Freshness constraints passed to providers            ║
║ • Entity must_keep_terms injected into all variants    ║
║ • Result memory candidates queried via search program  ║
║   embedding similarity (not just query text)           ║
╚══════════════════════════════════════════════════════╝
    │
    ▼
  Search Execution → RRF Merge → Rerank
```

### 4.2 Key Design Principle: No Parallel Classifiers

The current system has 3 classifiers that run independently and don't communicate. The new system has 2 stages in a **cascade**: entities feed into function-calling, which produces a search program that drives everything downstream. One pipeline, one source of truth.

---

## 5. Multi-Axis Classification Ontology

### 5.1 The Six Axes

These are NOT separate classifiers. They are **derived properties** of the search program generated by FunctionGemma. Each axis is inferred from which functions were called, what parameters were used, and how many calls were made.

#### Axis 1: Domain (holistic — what subject area?)

```
software_development  — code, APIs, libraries, frameworks, tools
infrastructure        — DevOps, cloud, networking, security, databases
data_science          — ML, statistics, data processing, visualization
general_tech          — hardware, consumer tech, product reviews
academic              — research papers, theories, methodologies
business              — finance, legal, market analysis
general               — everything else
```

**Derived from**: Which functions are called + what entities are mentioned.
- `docs_search` + software entities → `software_development`
- `academic_search` → `academic`
- `semantic_search` + business entities → `business`
- No domain-specific functions/entities → `general`

**Downstream effect**: Source authority. Software → prioritize docs, GitHub, StackOverflow. Academic → prioritize arXiv, papers. Business → prioritize reports, news.

**Why**: Domain determines which sources are authoritative. Without domain awareness, the system treats all queries the same way regardless of subject matter.

#### Axis 2: Task Type (what the user wants to DO)

```
lookup     — find specific entity/definition/version ("what is FastMCP", "Python 3.13 release date")
debug      — diagnose and fix a problem ("ECONNREFUSED Node.js", "pytest fixture not found")
implement  — build something new ("how to add auth to FastAPI", "React server components example")
compare    — evaluate alternatives ("React vs Vue SSR", "PostgreSQL vs MySQL JSON")
understand — conceptual/educational ("how does attention work", "explain RAG architecture")
navigate   — reach specific location ("site:github.com", "FastMCP docs")
monitor    — track changes/status ("React 19 changelog", "CUDA 12.4 compatibility")
```

**Derived from**: Entity types + function selection + parameter patterns.
- Error code entities + `keyword_search` + `community_search` → `debug`
- Software entities + `docs_search` no error codes → `implement` or `lookup`
- Multiple distinct entity mentions + parallel calls → `compare`
- No specific entities + `semantic_search` → `understand`
- URL entities + `keyword_search` → `navigate`

**Downstream effect**: Variant strategy.
- `debug` → exact-match preservation + community issues + error code terms as must-keep
- `implement` → official docs + tutorials + code examples
- `compare` → multiple parallel queries, one per entity
- `understand` → neural/conceptual search + academic
- `navigate` → minimal rewrite, preserve exact terms

**Why**: "code" intent lumps together queries that need completely different search strategies. Task type disambiguates them.

#### Axis 3: Granularity (how specific)

```
precise  — exact match needed (error codes, versions, specific APIs)
moderate — specific topic but some flexibility (framework feature, library comparison)
broad    — open-ended exploration (architecture patterns, best practices)
```

**Derived from**: Entity count + specificity + function parameter detail.
- Error code + version + specific API → `precise`
- Named framework + feature → `moderate`
- No named entities or very generic → `broad`

**Downstream effect**: Variant count and depth.
- `precise` → 1-2 focused variants, minimal rewriting
- `moderate` → 2-3 variants with some expansion
- `broad` → 3+ exploratory variants, aggressive expansion

**Why**: "Python" and "Python GIL implementation in CPython 3.12" are both "code" but need completely different levels of search specificity.

#### Axis 4: Freshness (temporal sensitivity)

```
realtime  — needs breaking/changing info (last 24h meaningful)
recent    — needs current info (last month matters)
evergreen — stable knowledge (doesn't expire)
```

**Derived from**: Entity types + function `freshness` parameters + keyword signals.
- "changelog", "release", "announcement" entities or explicit `freshness=realtime` → `realtime`
- Version-locked to current → `recent`
- Conceptual/theoretical → `evergreen`

**Downstream effect**: Provider weighting, TTL, recency boost.
- `realtime` → prefer news providers, shorter cache TTL, recency ranking signals
- `recent` → standard weighting, moderate TTL
- `evergreen` → aggressive result memory use, long TTL

**Why**: "What changed in React 19" and "How does React work" have completely different temporal needs but identical current classification.

#### Axis 5: Complexity (structural)

```
atomic   — single search goal, one query suffices
compound — 2-3 related sub-goals, moderate decomposition
complex  — 3+ independent sub-goals, deep decomposition needed
```

**Derived from**: Number of function calls + entity count + query structure.
- Single function call, 1-2 entities → `atomic`
- 2-3 function calls, coherent entity set → `compound`
- 3+ function calls or clearly independent sub-goals → `complex`

**Downstream effect**: Decomposition trigger and depth.
- `atomic` → no decomposition needed
- `compound` → 2-3 sub-queries
- `complex` → full decomposition with independent sub-queries + weights

**Why**: Current boolean `should_decompose` treats 2 and 5 sub-goals identically.

#### Axis 6: Confidence (meta-signal)

Per-function and per-axis confidence scores from FunctionGemma's output probability.

**Derived from**: FunctionGemma's output logits + entity extraction confidence.

**Downstream effect**: Fallback aggressiveness.
- High confidence → commit to the generated search program
- Low confidence → add fallback variants, broaden search, increase result memory candidates
- Very low confidence → fall back to current 3-class heuristic classifier

**Why**: A single confidence number on the entire classification is insufficient. Per-axis confidence lets downstream components make targeted fallback decisions.

### 5.2 Why These 6 Axes?

| Axis | Maps To | Current Gap |
|---|---|---|
| Domain | Source authority | None — treats all queries the same |
| Task Type | Search strategy | "code" dumping ground |
| Granularity | Variant depth | No signal at all |
| Freshness | Recency/TTL | No signal at all |
| Complexity | Decomposition | Binary only |
| Confidence | Fallback/robustness | Single float only |

Each axis was chosen because it maps to a **concrete pipeline decision** that the current system makes poorly or not at all.

### 5.3 Why NOT More Axes?

Considered and rejected:
- **Language**: Not needed — search providers handle multilingual queries already
- **Geographic**: Not relevant for developer/technical queries
- **Sentiment**: Not relevant for search routing
- **Modality** (text/video/image): Could be useful long-term but adds complexity now; revisit when we have image/video search providers

---

## 6. FunctionGemma: Search Program Generator

### 6.1 Current vs Proposed Usage

| Aspect | Current | Proposed |
|---|---|---|
| **Mode** | Prompt-based JSON classification | Native function-calling format |
| **Input** | Raw query + research_goal | Raw query + entity annotations + research_goal |
| **Output** | `{intent, should_decompose, confidence, routing}` | List of function calls (search program) |
| **Format** | Custom JSON schema | `<start_function_call>` special tokens |
| **Schema** | 3-class enum + booleans | 5 function declarations with 2-3 params each |
| **Fallibility** | High — 270M model generating free-form JSON | Low — special tokens constrain output |
| **Extensibility** | Add more enum values (fragile) | Add more functions (natural) |

### 6.2 Search Provider Function Declarations

```python
SEARCH_PROVIDER_FUNCTIONS = [
    {
        "name": "keyword_search",
        "description": (
            "Exact-match search for specific terms: error codes, version numbers, "
            "API names, function signatures, exact identifiers. Use when query "
            "contains precision-sensitive literals that must be preserved verbatim."
        ),
        "parameters": {
            "query": {"type": "string", "description": "Search query with exact terms preserved"},
            "freshness": {
                "type": "string",
                "enum": ["realtime", "recent", "evergreen"],
                "description": "How time-sensitive the results need to be",
            },
        },
    },
    {
        "name": "semantic_search",
        "description": (
            "Conceptual search using semantic understanding. Use for "
            "understanding, exploring, conceptual questions, and broad research. "
            "Best when the query is about 'how' or 'why' rather than 'what exactly'."
        ),
        "parameters": {
            "query": {"type": "string", "description": "Paraphrased query for semantic matching"},
            "freshness": {
                "type": "string",
                "enum": ["realtime", "recent", "evergreen"],
            },
        },
    },
    {
        "name": "community_search",
        "description": (
            "Search community forums, GitHub issues, StackOverflow, Reddit for "
            "real-world problems, workarounds, opinions, bug reports, and "
            "developer experiences. Best when someone is debugging or wants "
            "real-world advice."
        ),
        "parameters": {
            "query": {"type": "string", "description": "Query focused on problems and solutions"},
        },
    },
    {
        "name": "docs_search",
        "description": (
            "Search official documentation, API references, and release notes. "
            "Best for implementation guidance, API specifics, and authoritative "
            "answers from the source. Use when query mentions specific software "
            "or libraries by name."
        ),
        "parameters": {
            "query": {"type": "string", "description": "Query targeting official documentation"},
            "version": {
                "type": "string",
                "nullable": True,
                "description": "Specific version constraint if mentioned (e.g. '3.12', '2.x')",
            },
        },
    },
    {
        "name": "academic_search",
        "description": (
            "Search academic papers, arXiv preprints, and research publications. "
            "Best for methodology, theory, state-of-art, and research-oriented "
            "queries. Use when the query asks about research, algorithms, or "
            "scientific concepts."
        ),
        "parameters": {
            "query": {"type": "string", "description": "Query for academic literature"},
            "year_from": {
                "type": "integer",
                "nullable": True,
                "description": "Earliest publication year if specified",
            },
        },
    },
]
```

### 6.3 Example Search Programs

**Debug query**: "FastMCP ResourcesAsTools ECONNREFUSED"
```
Entities: [software:"FastMCP", api:"ResourcesAsTools", error_code:"ECONNREFUSED"]

Search Program:
1. keyword_search(query="FastMCP ResourcesAsTools ECONNREFUSED", freshness="evergreen")
2. community_search(query="FastMCP ECONNREFUSED workaround")

Derived signals:
  domain: software_development
  task_type: debug
  granularity: precise
  freshness: evergreen
  complexity: atomic
  confidence: 0.92
```

**Comparison query**: "React 19 vs Vue 4 SSR performance"
```
Entities: [software:"React", version:"19", software:"Vue", version:"4", concept:"SSR"]

Search Program:
1. docs_search(query="React 19 SSR performance", version="19")
2. docs_search(query="Vue 4 SSR performance", version="4")
3. semantic_search(query="React vs Vue server-side rendering performance comparison", freshness="recent")
4. community_search(query="React Vue SSR developer experience")

Derived signals:
  domain: software_development
  task_type: compare
  granularity: moderate
  freshness: recent
  complexity: compound
  confidence: 0.88
```

**Conceptual query**: "how does attention work in transformers"
```
Entities: [concept:"attention mechanism", concept:"transformer architecture"]

Search Program:
1. semantic_search(query="attention mechanism in transformer neural networks explanation", freshness="evergreen")
2. academic_search(query="attention mechanism transformer architecture", year_from=2017)

Derived signals:
  domain: data_science
  task_type: understand
  granularity: broad
  freshness: evergreen
  complexity: atomic
  confidence: 0.85
```

### 6.4 From Search Program to Classification Signals

The search program **encodes** all 6 axes implicitly. Extraction rules:

```python
def derive_classification(search_program: list[FunctionCall], entities: list[Entity]) -> QueryClassification:
    
    # Domain: from function types + entity types
    if any(fc.name == "academic_search" for fc in calls):
        domain = "academic" if not has_software_entities(entities) else "data_science"
    elif any(fc.name == "community_search" for fc in calls) and has_software_entities(entities):
        domain = "software_development"
    elif any(fc.name == "docs_search" for fc in calls):
        domain = "software_development"
    # ... etc
    
    # Task type: from entity types + function combination
    if has_entity_type(entities, "error_code"):
        task_type = "debug"
    elif count_distinct_software_entities(entities) >= 2:
        task_type = "compare"
    elif has_entity_type(entities, "api") and not has_entity_type(entities, "error_code"):
        task_type = "implement"
    elif "keyword_search" in [fc.name for fc in calls] and len(calls) == 1:
        task_type = "lookup"
    # ... etc
    
    # Granularity: from entity specificity
    if has_entity_type(entities, "error_code") or has_entity_type(entities, "version"):
        granularity = "precise"
    elif count_entities(entities) >= 2:
        granularity = "moderate"
    else:
        granularity = "broad"
    
    # Freshness: from function parameters
    freshness_values = [fc.args.get("freshness") for fc in calls if "freshness" in fc.args]
    if "realtime" in freshness_values:
        freshness = "realtime"
    elif "recent" in freshness_values:
        freshness = "recent"
    else:
        freshness = "evergreen"
    
    # Complexity: from call count
    if len(calls) == 1:
        complexity = "atomic"
    elif len(calls) <= 3:
        complexity = "compound"
    else:
        complexity = "complex"
    
    # Confidence: from model logits (if available) or heuristics
    confidence = compute_confidence(search_program, entities)
    
    return QueryClassification(
        domain=domain, task_type=task_type, granularity=granularity,
        freshness=freshness, complexity=complexity, confidence=confidence,
    )
```

### 6.5 Backward Compatibility

During migration, the search program output is mapped to the existing `ClassifierOutput` schema:

```python
def search_program_to_legacy_classifier(program: SearchProgram, classification: QueryClassification) -> ClassifierOutput:
    # Intent mapping
    intent_map = {
        "debug": "code", "implement": "code", "lookup": "code", "navigate": "code",
        "compare": "comparison", "understand": "general_research", "monitor": "general_research",
    }
    
    # Routing mapping
    function_to_routing = {
        "keyword_search": "keyword",
        "docs_search": "keyword",
        "semantic_search": "neural",
        "academic_search": "neural",
        "community_search": "community",
    }
    
    routing = ProviderRouting(
        keyword=any(fc.name in ("keyword_search", "docs_search") for fc in program.calls),
        neural=any(fc.name in ("semantic_search", "academic_search") for fc in program.calls),
        community=any(fc.name == "community_search" for fc in program.calls),
    )
    
    # Decomposition mapping
    should_decompose = classification.complexity in ("compound", "complex")
    
    return ClassifierOutput(
        intent=intent_map[classification.task_type],
        should_decompose=should_decompose,
        confidence=classification.confidence,
        routing=routing,
    )
```

---

## 7. GLiNER2 Integration: Entity-Aware Routing

### 7.1 Entity Types for Query Classification

Extend the default GLiNER2 schema with types that directly inform classification:

```python
QUERY_ENTITY_SCHEMA = {
    "software":       "Software packages, libraries, frameworks, tools (e.g. FastMCP, React, PyTorch)",
    "api":            "API names, function signatures, decorator names, method calls",
    "error_code":     "Error codes, exceptions, HTTP status codes (e.g. ECONNREFUSED, TypeError, 404)",
    "version":        "Version numbers, release identifiers (e.g. 3.12, v2.0, 2024.1)",
    "url":            "URLs, URIs, web addresses",
    "file_path":      "File paths, module paths (e.g. src/utils/helpers.py)",
    "class_method":   "Class::method or Module.function patterns",
    "config_key":     "Configuration keys, environment variables, settings",
    "concept":        "Technical concepts, design patterns, architectures (e.g. SSR, RAG, attention mechanism)",
    "organization":  "Companies, open-source orgs (e.g. Google, Meta, Apache)",
    "person":         "Named individuals when relevant to query",
    "standard":       "Standards, protocols, specifications (e.g. OAuth2, HTTP/3, W3C)",
}
```

### 7.2 Entity-to-Classification Mapping

Entity types directly produce classification signals:

| Entity Type | Task Type Signal | Granularity Signal | Precision Signal |
|---|---|---|---|
| `error_code` | `debug` | `precise` | must_keep_term |
| `api` | `implement` or `lookup` | `precise` | must_keep_term |
| `version` | `lookup` or `monitor` | `precise` | must_keep_term |
| `url` | `navigate` | `precise` | must_keep_term |
| `software` (2+) | `compare` | `moderate` | partial |
| `concept` | `understand` | `broad` | expandable |
| `organization` | domain signal | — | expandable |

### 7.3 Entity Context for FunctionGemma

The entity annotations are formatted and passed to FunctionGemma as part of the function-calling context:

```
User Query: "FastMCP ResourcesAsTools ECONNREFUSED"

Extracted Entities:
- software: FastMCP
- api: ResourcesAsTools
- error_code: ECONNREFUSED

Entity-aware routing hints:
- Software + API + error_code → likely debug task
- Error code present → keyword search recommended
- Multiple entity types → community search for workarounds
```

This context is NOT a directive — FunctionGemma makes the final call selection. But it provides the semantic annotation that a 270M model might miss from raw text alone.

---

## 8. Pipeline Integration

### 8.1 Files Modified

| Current File | Action | Replacement |
|---|---|---|
| `query_policy.py` | **Delete** (Phase B) | Entity extraction + search program derivation |
| `query_policy_resolver.py` | **Delete** (Phase B) | `resolve_query_understanding()` |
| `content_type.py` | **Delete** (Phase B) | Derived from search program |
| `query_classifier_client.py` | **Rewrite** | `SearchProgramGenerator` using function-calling |
| `query_decomposition.py` | **Merge** (Phase B) | Into `SearchProgramGenerator` |
| `query_rewrite_models.py` | **Extend** | Add `SearchProgram`, `QueryClassification` models |
| `query_rewrite.py` | **Update** | Consume `SearchProgram` instead of `ClassifierOutput` |
| `query_rewrite_plan.py` | **Update** | Build plan from `SearchProgram` |
| `server.py` | **Update** | Wire new pipeline stages |

### 8.2 New Models

```python
class SearchFunctionCall(BaseModel):
    name: str
    arguments: dict[str, Any]
    confidence: float = 1.0

class SearchProgram(BaseModel):
    calls: list[SearchFunctionCall]
    raw_query: str
    entities: list[EntityExtraction]  # from GLiNER2
    classification: QueryClassification  # derived
    
class QueryClassification(BaseModel):
    domain: Literal[
        "software_development", "infrastructure", "data_science",
        "general_tech", "academic", "business", "general"
    ]
    task_type: Literal[
        "lookup", "debug", "implement", "compare",
        "understand", "navigate", "monitor"
    ]
    granularity: Literal["precise", "moderate", "broad"]
    freshness: Literal["realtime", "recent", "evergreen"]
    complexity: Literal["atomic", "compound", "complex"]
    confidence: float = Field(ge=0.0, le=1.0)
```

### 8.3 Content Type Derivation (replacing content_type.py)

```python
def derive_content_type(classification: QueryClassification) -> ContentType:
    if classification.freshness == "realtime":
        return ContentType.NEWS
    if classification.domain in ("software_development", "infrastructure", "data_science"):
        return ContentType.TECHNICAL
    if classification.task_type in ("lookup", "understand", "compare"):
        return ContentType.FAQ
    return ContentType.GENERAL

def derive_adaptive_ttl(classification: QueryClassification) -> timedelta:
    base_ttl = ADAPTIVE_TTL[derive_content_type(classification)]
    if classification.freshness == "realtime":
        return min(base_ttl, timedelta(minutes=30))
    if classification.freshness == "evergreen" and classification.granularity == "precise":
        return base_ttl * 2  # precise evergreen caches longest
    return base_ttl
```

### 8.4 Result Memory Integration

The search program provides a **richer embedding key** than raw query text. Instead of embedding just "FastMCP ECONNREFUSED", embed the normalized search program representation:

```
[keyword_search:evergreen, community_search, entities:software+error_code]
```

Two queries that generate similar search programs are likely looking for similar results, even if their raw text differs. "FastMCP connection refused" and "FastMCP ECONNREFUSED" would generate nearly identical search programs → high similarity in result memory → strong candidate overlap.

This directly connects to the entity-aware result memory plan: the search program vector is a **natural key** for the Qdrant result memory collection.

---

## 9. Migration Phases

### Phase A: Search Program Mode (minimal change, high impact)

**Goal**: Switch FunctionGemma to function-calling mode. Keep backward compat.

**Changes**:
1. Rewrite `FunctionGemmaClient._generate_sync()` to use native `<start_function_declaration>` + `<start_function_call>` format
2. Define 5 search provider functions as FunctionGemma tool declarations
3. Add `generate_search_program()` method to `FunctionGemmaClient`
4. Add entity context to FunctionGemma input (from GLiNER2 or pre-GLiNER2 heuristics)
5. Add `SearchProgram` and `QueryClassification` models to `query_rewrite_models.py`
6. Add `search_program_to_legacy_classifier()` mapping function
7. Keep existing `classify_query()` and `decompose_query()` as fallback
8. Update `query_rewrite.py` to try search program first, fall back to legacy classification
9. Add observability: track search program vs legacy classification choice, compare downstream result quality

**Files**: `query_classifier_client.py`, `query_rewrite_models.py`, `query_rewrite.py`

**Timeline**: 3-5 days

**Risk**: Low — legacy path remains as fallback. Search program path is additive.

### Phase B: Pipeline Unification (deeper refactor)

**Goal**: Eliminate separate classifiers. Build unified QueryUnderstanding pipeline.

**Changes**:
1. Create `query_understanding.py` — unified entry point replacing `query_policy_resolver.py`
2. GLiNER2 entity extraction runs first (requires Phase 1 from entity-aware-result-memory-plan)
3. Entity-informed must_keep_terms replace regex-based `_extract_must_keep_terms()`
4. `SearchProgramGenerator` is the only classifier (no dual path)
5. `content_type.py` deleted — content type derived from `QueryClassification`
6. `query_policy.py` deleted — precision detection from entity types
7. `query_decomposition.py` merged into `SearchProgramGenerator`
8. `query_rewrite.py` updated to consume `SearchProgram` directly (no mapping to legacy schema)
9. Update analytics: new DuckDB tables for classification signals, search program events

**Files**: `query_understanding.py` (new), `query_classifier_client.py`, `query_rewrite.py`, `query_rewrite_models.py`, delete `query_policy.py`, `query_policy_resolver.py`, `content_type.py`

**Timeline**: 5-7 days (after GLiNER2 Phase 1 is complete)

**Risk**: Medium — removes fallback paths. Requires GLiNER2 to be stable.

### Phase C: Local FunctionGemma + Fine-tuning (optimization)

**Goal**: Run FunctionGemma locally for latency. Fine-tune on our query logs.

**Changes**:
1. Local FunctionGemma inference via `llama-cpp-python` or `transformers`
2. Eager load at server startup (2-5s cold start, then ~127ms per call)
3. Eliminate Cloud Run dependency and 10s timeout
4. Fine-tune FunctionGemma on our DuckDB query logs
5. Training data: (query_text, entity_annotations, optimal_search_program) triples
6. Use LoRA (r=16) — only ~1.47M trainable parameters
7. Autoresearch loop: run fine-tuned model against validation set, measure routing accuracy
8. Search program embedding for result memory lookup (Qdrant)

**Timeline**: 7-10 days (after Phase B and Phase 0/2 from entity-aware-result-memory-plan)

**Risk**: Medium-High — fine-tuning requires curated training data. But the multi-agent router experiment showed it's feasible on a free T4 GPU in 45 minutes.

---

## 10. Risk Analysis

### R1: FunctionGemma accuracy for search provider selection

**Severity**: Medium
**Likelihood**: Possible
**Current evidence**: BFCL Simple 61.6% 0-shot. Multi-agent router 58% base → 89.4% fine-tuned.
**Mitigation**: Phase A keeps legacy classifier as fallback. Search provider functions are simpler than the multi-agent router case (5 tools, 2-3 params each vs 7 agents). Phase C adds fine-tuning. Observability tracks accuracy.

### R2: Format mismatch between training and inference

**Severity**: High (accuracy drops from 89% to 62%)
**Likelihood**: Low (we control both sides)
**Mitigation**: Use EXACT same formatting function for training data generation and inference. If using Cloud Run, ensure the API preserves FunctionGemma's native format. If running locally, format is guaranteed.

### R3: Local FunctionGemma adds server startup time

**Severity**: Low
**Likelihood**: Certain
**Mitigation**: Eager load at server startup, before any queries arrive. 550MB RAM is acceptable. If startup time is a concern, lazy-load in background while serving with Cloud Run.

### R4: GLiNER2 entities are noisy or missing

**Severity**: Medium
**Likelihood**: Possible
**Mitigation**: Entity extraction confidence threshold — low-confidence entities are flagged but not treated as must-keep terms. FunctionGemma can still make good decisions from raw query text. Entity annotations are SUGGESTIONS, not commands.

### R5: Migration breaks downstream consumers

**Severity**: High
**Likelihood**: Low
**Mitigation**: Phase A's backward compat mapping ensures existing code works unchanged. Phase B only proceeds after Phase A is validated in production. Test suite coverage for all classification paths.

### R6: Training data for fine-tuning is insufficient

**Severity**: Medium
**Likelihood**: Possible
**Mitigation**: Use model distillation — run a larger model (e.g., the current Cerebras/Groq rewrite LLM) over historical queries to generate optimal search program labels. The multi-agent router experiment used 12,550 examples — we likely have thousands of queries in DuckDB analytics.

### R7: Over-engineering for an MCP search server

**Severity**: Low
**Likelihood**: Debated
**Counterargument**: The current system ALREADY has 3 classifiers that don't communicate. The proposed system UNIFIES them. FunctionGemma in native mode is arguably simpler than prompt-based JSON classification. The 6-axis classification is derived from a single model call, not 6 separate classifiers. Less code, less complexity, better results.

---

## Appendix A: FunctionGemma vs Alternatives

| Model | Size | Function Calling | Fine-tunable | Latency | Notes |
|---|---|---|---|---|---|
| FunctionGemma 270M | 270M | Native (special tokens) | Yes (LoRA) | ~127ms GPU | Trained for this task. Small but focused. |
| xLAM-1B-fc-r | 1B | Native | Yes | ~200ms GPU | BFCL top performer. Larger but more capable. |
| Hermes-2B | 2B | Native | Yes | ~400ms GPU | General-purpose with FC support. |
| Qwen2.5-3B | 3B | Native (tool_choice) | Yes | ~500ms GPU | Very capable FC, but heavier. |
| Prompt-based (current) | 270M | No (prompt injection) | No | ~300ms HTTP | What we have now. Fragile. |

**Recommendation**: Start with FunctionGemma 270M in native mode (no infrastructure change). Evaluate xLAM-1B-fc-r if accuracy is insufficient after fine-tuning.

## Appendix B: Comparison with Current Pipeline Outputs

### Current Output (per query)
```json
{
  "intent": "code",
  "should_decompose": false,
  "confidence": 0.35,
  "routing": {"keyword": true, "neural": true, "community": false}
}
```

### Proposed Output (per query)
```json
{
  "search_program": {
    "calls": [
      {"name": "keyword_search", "arguments": {"query": "FastMCP ResourcesAsTools ECONNREFUSED", "freshness": "evergreen"}, "confidence": 0.94},
      {"name": "community_search", "arguments": {"query": "FastMCP ECONNREFUSED workaround"}, "confidence": 0.87}
    ]
  },
  "classification": {
    "domain": "software_development",
    "task_type": "debug",
    "granularity": "precise",
    "freshness": "evergreen",
    "complexity": "atomic",
    "confidence": 0.91
  },
  "entities": [
    {"type": "software", "value": "FastMCP", "confidence": 0.95},
    {"type": "api", "value": "ResourcesAsTools", "confidence": 0.89},
    {"type": "error_code", "value": "ECONNREFUSED", "confidence": 0.97}
  ]
}
```

The proposed output encodes **more information in a more reliable format** while requiring fewer total model calls (1 FunctionGemma call + 1 GLiNER2 extraction vs current 1-2 FunctionGemma calls + regex + keyword heuristic).