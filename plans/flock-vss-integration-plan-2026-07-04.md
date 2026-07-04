# Flock + VSS Integration Plan

## Goal

Integrate two DuckDB community extensions into the web-search-mcp analytics pipeline:

1. **Flock** — Run LLM operations (completion, embedding, filtering, reranking) entirely in SQL inside DuckDB, replacing Python-side API calls for judge evaluation and LLM reranking.
2. **VSS (Vector Similarity Search)** — Add HNSW-indexed vector search directly in DuckDB for semantic search over historical results, duplicate detection, similar-query suggestions, and semantic caching.

## Current State

### DuckDB Inventory

| Database | Path | Tables | Writes | Reads |
|----------|------|--------|--------|-------|
| `search_events.duckdb` | `duckdb_data/analytics/` | 20+ tables | Append-only, thread-locked | Read-only for analytics |
| `page_cache.duckdb` | `duckdb_data/cache/` | 1 table | URL-hash upsert | Key lookup by hash |
| `transcript_cache.duckdb` | `duckdb_data/cache/` | 1 table | Composite-key upsert | Key lookup by hash |
| `process_logs.duckdb` | `duckdb_data/logs/` | 1 table | Batch insert via QueueListener | FTS read (debugging) |

### Key Architecture Patterns

- Thread-safe writes via `threading.Lock()` + `duckdb.connect()` per operation
- Async dispatch via `asyncio.to_thread()` for non-blocking inserts (`async_writes.py`)
- Schema migration via `_ensure_columns()` pattern (backward-compatible additions)
- TTL cleanup for logs (every 50 flushes, `CHECKPOINT` after)
- Read-only connections for analytics queries (`queries.py`)
- MotherDuck sync capability (`motherduck_sync.py`)

### Embedding Infrastructure

| Setting | Value |
|---------|-------|
| Model | `intfloat/multilingual-e5-large-instruct` |
| Dimension | 1024 |
| Provider | Hugging Face Inference API |
| Timeout | 30s, 1 retry |
| Rate Limited | Yes (semaphore-bound) |
| Storage | ❌ Not persisted to DuckDB |

### LLM Call Flow (Current)

```
User Query → Python → litellm → External API → Python → DuckDB INSERT
                 ↑                                          |
                 └──────── fire-and-forget tasks ───────────┘
```

**Python-side LLM calls that could migrate to Flock:**

| Path | Function | Latency | Batch | Flock Fit |
|------|----------|---------|-------|-----------|
| `judge_runner.py` | Relevance scoring (1-4 scale) | ~2-5s per run | 1 run | **High** |
| `rerank/stages.py` | LLM listwise rerank (sliding window) | ~3-8s | 20 docs | **High** |
| `embeddings/hf_inference.py` | Text → vector | ~0.5s | Up to 32 texts | Low (HF free) |
| `query_understanding` | Intent classification | ~1-3s | 1 query | Low (ONNX primary) |

### DuckDB Version

| Source | Version | Flock Compatible? |
|--------|---------|-------------------|
| Installed (`pip show duckdb`) | 1.5.0 | ✅ Yes (needs ≥1.5.0) |
| `pyproject.toml` constraint | `duckdb>=1.1.0,<1.5.3` | ❌ Needs bump |
| Extension dir (`.duckdb_extensions/`) | v1.5.3 | ✅ Fine |

---

## Part 1: Flock Integration Plan

### What Flock Is

Flock is a DuckDB extension from Polytechnique Montréal's DAIS Lab (published in PVLDB 2025). It adds SQL functions for LLM calls, embedding generation, hybrid search fusion, and structured output — all running inside the DuckDB process.

**Key functions:**

| Function | Type | Purpose |
|----------|------|---------|
| `llm_complete` | Scalar (map) | Text generation, classification, summarization |
| `llm_embedding` | Scalar (map) | Generate embeddings per row |
| `llm_filter` | Scalar (map) | Boolean classification per row |
| `llm_reduce` | Aggregate (reduce) | Combine multiple rows into one |
| `llm_rerank` | Aggregate (reduce) | Sliding-window relevance rerank |
| `llm_first` / `llm_last` | Aggregate | Top/bottom result after rerank |
| `flock_get_metrics()` | Table function | Token counts, latency, call counts |
| `fusion_rrf` / `fusion_combsum` / etc. | Scalar | Hybrid search score fusion |

**Providers:** OpenAI, Azure, Ollama, Anthropic/Claude

**Resource management:** Models and prompts are stored as DuckDB catalog objects (`CREATE MODEL`, `CREATE PROMPT`, `CREATE SECRET`).

### Integration Architecture

```
┌─────────────── search_events.duckdb ─────────────────────────┐
│                                                               │
│  ┌─────────────────┐         ┌──────────────────────────┐    │
│  │ Existing Tables  │         │  Flock Catalog (auto)     │    │
│  │ ─────────────   │         │  ───────────────────     │    │
│  │ search_runs      │         │  flock_models             │    │
│  │ provider_calls   │         │   ├ search_judge          │    │
│  │ merged_candidates│         │   ├ search_reranker       │    │
│  │ rerank_stages    │         │   └ embedding_model        │    │
│  │ final_results    │         │  flock_prompts            │    │
│  │ judge_evaluations│         │   └ relevance_judge       │    │
│  └────────┬────────┘         │  flock_secrets             │    │
│           │                  │   └ openai_key (env-ref)   │    │
│           │                  └──────────┬───────────────┘    │
│           │                             │                     │
│  ┌────────▼─────────────────────────────▼──────────────┐    │
│  │              Flock SQL Functions                     │    │
│  │  llm_complete │ llm_embedding │ llm_filter           │    │
│  │  llm_rerank   │ llm_reduce    │ flock_get_metrics()  │    │
│  └────────────────────────┬─────────────────────────────┘    │
│                           │                                   │
│                   ┌───────▼──────┐                            │
│                   │ OpenAI/Azure │                            │
│                   │ Ollama/Claude│                            │
│                   └──────────────┘                            │
└───────────────────────────────────────────────────────────────┘

Python side (reduced):
  judge_runner.py: trigger → Flock SQL (no Python LLM call)
  Flock reads: search_runs.query + final_results → writes: judge_evaluations
  Cost tracking: flock_get_metrics() ← no manual token counting
```

### Implementation Steps

#### Step 1: Bump DuckDB Constraint

**File:** `pyproject.toml` line 23

```toml
# Before:
"duckdb>=1.1.0,<1.5.3",
# After:
"duckdb>=1.5.0",
```

Rationale: Flock requires DuckDB ≥1.5.0. The installed version (1.5.0) and extension cache (v1.5.3) are both compatible.

#### Step 2: Create Flock Initialization Module

**New file:** `src/kindly_web_search_mcp_server/flock/__init__.py`

```python
"""Flock extension initialization for LLM-in-SQL capabilities.

Provides ensure_flock() to install/load the extension and
setup_flock_resources() to create models/prompts/secrets
in a given DuckDB connection.
"""

import duckdb
import logging

logger = logging.getLogger(__name__)

_FLOCK_INSTALLED = False


def ensure_flock(con: duckdb.DuckDBPyConnection) -> None:
    """Install (once) and load (per-connection) the Flock extension."""
    global _FLOCK_INSTALLED
    if not _FLOCK_INSTALLED:
        con.execute("INSTALL flock FROM community")
        _FLOCK_INSTALLED = True
    con.execute("LOAD flock")


def setup_flock_models(con: duckdb.DuckDBPyConnection) -> None:
    """Create Flock model configurations for search analytics tasks.

    Each CREATE MODEL is idempotent (CREATE IF NOT EXISTS semantics).
    Model arguments define tuple format, batch size, and LLM parameters.
    """
    ensure_flock(con)

    models = [
        (
            "search_judge",
            "CREATE MODEL IF NOT EXISTS search_judge ("
            "TYPE openai, MODEL_NAME 'gpt-4o-mini', "
            "{'tuple_format': 'JSON', 'batch_size': 4, "
            "'model_parameters': {'temperature': 0.0, 'top_p': 0.95}}"
            ")"
        ),
        (
            "search_reranker",
            "CREATE MODEL IF NOT EXISTS search_reranker ("
            "TYPE openai, MODEL_NAME 'gpt-4o-mini', "
            "{'tuple_format': 'JSON', 'batch_size': 8, "
            "'model_parameters': {'temperature': 0.0}}"
            ")"
        ),
        (
            "embedding_model",
            "CREATE MODEL IF NOT EXISTS embedding_model ("
            "TYPE openai, MODEL_NAME 'text-embedding-3-small'"
            ")"
        ),
    ]

    for name, sql in models:
        try:
            con.execute(sql)
        except Exception as exc:
            logger.warning("Failed to create Flock model %s: %s", name, exc)


def setup_flock_prompts(con: duckdb.DuckDBPyConnection) -> None:
    """Create Flock prompt templates for search pipeline tasks."""
    ensure_flock(con)

    prompts = [
        (
            "relevance_judge",
            "CREATE PROMPT IF NOT EXISTS relevance_judge ("
            "PROMPT 'Score the relevance of these search results to the "
            "query: {query}\n\nContext: {research_goal}\n\nResults:\n{results}\n\n"
            "Rate each result 1 (irrelevant) to 4 (highly relevant) and output JSON "
            "with relevance_score, relevance_raw, and rationale fields.'"
            ")"
        ),
        (
            "result_reranker",
            "CREATE PROMPT IF NOT EXISTS result_reranker ("
            "PROMPT 'Rerank the following search results by relevance to: "
            "{query}\n\nRank from most to least relevant. Consider authority, "
            "freshness, and direct relevance.'"
            ")"
        ),
    ]

    for name, sql in prompts:
        try:
            con.execute(sql)
        except Exception as exc:
            logger.warning("Failed to create Flock prompt %s: %s", name, exc)


def setup_flock_resources(con: duckdb.DuckDBPyConnection) -> None:
    """Create all Flock resources: extension, models, prompts."""
    setup_flock_models(con)
    setup_flock_prompts(con)
```

#### Step 3: Flock-Based Judge Runner (New Path)

**Modify:** `src/kindly_web_search_mcp_server/analytics/judge_runner.py`

Add a Flock-based alternative alongside the existing Python judge path. Both paths write to the same `judge_evaluations` table.

```python
async def run_judge_evaluation_flock(
    run_key: str,
    query: str,
    results: list[Any],
    research_goal: str | None = None,
    db_path: str | None = None,
) -> None:
    """Judge evaluation using Flock — LLM runs inside DuckDB.

    Advantages over Python path:
      - No Python-side API call overhead per run
      - Direct DuckDB INSERT, no serialization roundtrip
      - Automatic cost tracking via flock_get_metrics()
    """
    from ..flock import ensure_flock, setup_flock_resources
    from .duckdb_store import _db_path

    path = _db_path(db_path)
    start_ms = time.monotonic_ns() // 1_000_000

    con = duckdb.connect(str(path))
    try:
        setup_flock_resources(con)

        results_text = json.dumps([
            {"title": getattr(r, "title", ""), "snippet": getattr(r, "snippet", "")}
            for r in results[:10]  # Top 10 for judge
        ])

        row = con.execute("""
            SELECT llm_complete(
                {'model_name': 'search_judge'},
                {'prompt_name': 'relevance_judge',
                 'context_columns': [
                    {'data': $query, 'name': 'query'},
                    {'data': $goal, 'name': 'research_goal'},
                    {'data': $results, 'name': 'results'}
                 ]}
            ) AS judge_output
        """, {
            "query": query,
            "goal": research_goal or "",
            "results": results_text,
        }).fetchone()

        if row and row[0]:
            output = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            duration_ms = (time.monotonic_ns() // 1_000_000) - start_ms

            insert_judge_evaluation(
                run_key=run_key,
                judge_model="gpt-4o-mini",
                relevance_score=output.get("relevance_score"),
                relevance_raw=output.get("relevance_raw"),
                relevance_scale="1-4",
                rationale=str(output.get("rationale", "")),
                duration_ms=duration_ms,
            )

        # Track Flock metrics
        metrics_rows = con.execute("SELECT * FROM flock_get_metrics()").fetchall()
        logger.info("Flock judge metrics: %s", metrics_rows)

    finally:
        con.close()
```

#### Step 4: Batch Judge via Flock (New Capability)

Add a function to judge ALL historical runs at once — impossible with the Python path:

```python
def batch_judge_historical_runs(
    days: int = 7,
    db_path: str | None = None,
) -> int:
    """Judge all runs from the last N days that haven't been judged yet.

    Returns count of newly judged runs.
    """
    from ..flock import ensure_flock, setup_flock_resources
    from .duckdb_store import _db_path

    path = _db_path(db_path)
    con = duckdb.connect(str(path))
    try:
        setup_flock_resources(con)

        # Run Flock over ALL un-judged runs in a single SQL statement
        con.execute("""
            WITH unjudged AS (
                SELECT sr.run_key, sr.query, sr.research_goal
                FROM search_runs sr
                WHERE sr.recorded_at > now() - INTERVAL $days DAY
                  AND sr.run_key NOT IN (
                      SELECT DISTINCT run_key FROM judge_evaluations
                  )
            )
            INSERT INTO judge_evaluations (
                run_key, recorded_at, judge_model,
                relevance_score, relevance_raw, relevance_scale,
                rationale, duration_ms
            )
            SELECT
                u.run_key,
                now(),
                'gpt-4o-mini',
                0.0,  -- placeholder; parse from llm_complete output
                0,
                '1-4',
                'batch-judge',
                0.0
            FROM unjudged u
        """, {"days": days})

        return con.execute("SELECT changes()").fetchone()[0]
    finally:
        con.close()
```

**Note:** Full batch implementation requires parsing structured JSON from `llm_complete` output inside SQL — possible with DuckDB's `json_extract()` functions on Flock's JSON-format output. This is a Phase 2 enhancement.

#### Step 5: Flock LLM Rerank (Optional Alternative Path)

Flock's `llm_rerank` implements the same sliding-window progressive reranking (Ma et al. 2023, the same paper used in the current `rerank/stages.py` LLM stage). Replace the Python-side rerank call:

```sql
-- Current Python path: litellm call → parse → reorder Python list
-- Flock path: single SQL aggregate function

SELECT llm_rerank(
    {'model_name': 'search_reranker'},
    {'prompt': $query,
     'context_columns': [
        {'data': title, 'name': 'title'},
        {'data': snippet, 'name': 'snippet'}
     ]}
) AS reranked
FROM merged_candidates
WHERE run_key = $run_key;
```

The output is a JSON array of objects ordered by relevance — matching the current output format.

### Flock Tradeoffs

| Factor | Flock Path | Current Python Path |
|--------|-----------|---------------------|
| API call latency | Same (both hit external API) | Same |
| Batch efficiency | **Better** — native SQL batching, no Python loop overhead | Requires Python `async for` loop |
| Result INSERT latency | **Better** — writes directly to DuckDB from SQL | Python → JSON → DuckDB roundtrip |
| Cost tracking | **Built-in** — `flock_get_metrics()` returns tokens, latency, call count | Manual in Python (`extract_llm_usage`) |
| Debugging | Harder — SQL execution context, opaque errors | Easier — Python breakpoints, stack traces |
| Structured output | JSON schema via `tuple_format: 'JSON'` | Uses `instructor` library (OpenAI-only) |
| Rate limiting | Must be managed externally | Existing Python rate limiter |
| Provider flexibility | 4 providers (OpenAI/Azure/Ollama/Anthropic) | Any via litellm (more options) |
| Error handling | DuckDB runtime errors (hard to recover) | Python `try/except` (graceful fallback) |
| Maintenance | Community extension — update risk on DuckDB version bumps | Own code — update when litellm updates |
| Embedding cost | OpenAI API charges per call | HF Inference free (`intfloat/e5` model) |

### Flock Sequencing

```
Phase 1 (Week 1):
├── Bump DuckDB constraint in pyproject.toml
├── Create flock/__init__.py with ensure_flock + setup helpers
└── Add run_judge_evaluation_flock() as alternative path

Phase 2 (Week 2):
├── Wire flock judge into existing fire-and-forget path
├── Add flock_get_metrics() logging to existing metrics pipeline
├── Add batch_judge_historical_runs() for backfill
└── Integration tests (test_flock_judge.py)

Phase 3 (Week 3+):
├── Replace LLM listwise rerank with llm_rerank (optional)
├── Add llm_filter for content quality gates
└── Monitor flock_get_metrics() for cost anomalies
```

### Key Decision Points for Flock

1. **Judge scope:** Start with judge runner only (highest value, lowest risk), or also replace LLM rerank in the same phase?

2. **Provider:** The repo already has OpenAI keys. `gpt-4o-mini` ($0.15/1M tokens) is the cheapest judge option. Anthropic/Claude is also available via Flock.

3. **Fallback:** Should the Flock path fall back to the existing Python judge path on failure? Recommended: yes, for resilience during the transition.

4. **Prompt versioning:** Flock supports prompt versions (`version: 1`, `version: 2`). Use for A/B testing judge prompt variations?

---

## Part 2: VSS (Vector Similarity Search) Integration Plan

### What VSS Is

VSS is a DuckDB core extension that adds HNSW (Hierarchical Navigable Small Worlds) indexing for `FLOAT[N]` `ARRAY` columns, enabling sub-millisecond approximate nearest neighbor (ANN) search directly in SQL.

**Supported operations:**

| Pattern | SQL | Index Acceleration |
|---------|-----|--------------------|
| Top-k NN search | `ORDER BY array_distance(emb, q) LIMIT k` | HNSW scan |
| Similarity filter | `WHERE array_cosine_distance(emb, q) < t` | HNSW scan |
| Batch NN search | `min_by(table, array_distance(emb, q), k)` | HNSW scan |
| Fuzzy join | `vss_join(left, right, col_a, col_b, k)` | Brute-force only |
| Duplicate check | `array_cosine_similarity(emb_i, emb_j) > 0.95` | Post-filter |
| Hybrid: FTS + VSS | BM25 candidates → HNSW rerank | Two-step |

**Supported distance metrics:**

| Metric | SQL Function | VSS `metric` |
|--------|-------------|--------------|
| Euclidean (L2) | `array_distance()` | `l2sq` |
| Cosine | `array_cosine_distance()` | `cosine` |
| Inner product | `array_negative_inner_product()` | `ip` |

**Key constraints from DuckDB docs:**

- Only `FLOAT[dim]` vectors supported (not DOUBLE, not variable-length)
- HNSW index **must fit in RAM** (not buffer-managed, not counted in `memory_limit`)
- Persistence is **experimental** (`SET hnsw_enable_experimental_persistence = true`)
- No WAL recovery for index — crash → possible data loss or corruption
- Deletes mark entries stale → `PRAGMA hnsw_compact_index()` needed periodically
- Bulk-load after index creation → create index after data load for better parallelism

### Integration Architecture

```
┌───────────────── search_events.duckdb ────────────────────────────┐
│                                                                     │
│  ┌─────────────────────┐        ┌──────────────────────────────┐  │
│  │ Existing Tables      │        │  New VSS Tables               │  │
│  │ ─────────────       │        │  ──────────────              │  │
│  │ final_results        │        │  final_results (enriched)     │  │
│  │   + rank             │        │   + embedding FLOAT[1024]     │  │
│  │   + title            │        │   + HNSW index (cosine)       │  │
│  │   + link             │        │                               │  │
│  │   + snippet          │        │  query_embeddings (NEW)        │  │
│  │   + domain           │        │   + run_key VARCHAR PK        │  │
│  │   + final_score      │        │   + query VARCHAR             │  │
│  │   + providers         │        │   + embedding FLOAT[1024]    │  │
│  │   + payload_json      │        │   + HNSW index (cosine)       │  │
│  └──────────┬──────────┘        └──────────────┬───────────────┘  │
│             │                                   │                   │
│  ┌──────────▼───────────────────────────────────▼───────────────┐  │
│  │                        VSS Queries                            │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │  │
│  │  │ Semantic      │  │ Duplicate    │  │ Similar Queries   │   │  │
│  │  │ Search over   │  │ Detection    │  │ Suggestions       │   │  │
│  │  │ Past Results  │  │              │  │                   │   │  │
│  │  └──────────────┘  └──────────────┘  └──────────────────┘   │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  Existing Qdrant (remote) ← write-only long-term archive           │
│  DuckDB VSS (local)     ← hot recent results + query embeddings     │
└─────────────────────────────────────────────────────────────────────┘
```

### What VSS Enables (New Capabilities)

| Capability | Before | After VSS |
|-----------|--------|-----------|
| **Semantic search over past results** | ❌ No vector indexing | ✅ `ORDER BY array_cosine_distance(emb, q_emb) LIMIT k` — sub-5ms |
| **Semantic deduplication** | ❌ Title/snippet text match only | ✅ Cosine similarity ≥0.95 → duplicate |
| **"Similar searches" suggestions** | ❌ Not possible | ✅ ANN over `query_embeddings` table |
| **Semantic cache lookup** | ⚠️ Exact query string LRU only | ✅ Find semantically similar cached pages |
| **Result quality clustering** | ❌ Not possible | ✅ Cluster results by embedding → detect biases |
| **Hybrid search (BM25 + VSS)** | ❌ Not possible | ✅ FTS candidate pool → HNSW rerank |

### Implementation Steps

#### Step 1: Add embedding column to final_results

**Modify:** `src/kindly_web_search_mcp_server/analytics/duckdb_store.py`

```python
def _ensure_final_results(connection: duckdb.DuckDBPyConnection) -> None:
    """Create final_results table with VSS embedding column."""
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_FR_TABLE_NAME} (
            run_key VARCHAR NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            rank INTEGER,
            title VARCHAR,
            link VARCHAR,
            snippet VARCHAR,
            domain VARCHAR,
            final_score DOUBLE,
            providers VARCHAR[],
            provider_count INTEGER,
            entities_count INTEGER,
            embedding FLOAT[1024],
            payload_json JSON
        )
        """
    )
```

#### Step 2: Create HNSW Index (Startup/Migration)

**Modify:** `src/kindly_web_search_mcp_server/analytics/views.py` ensure_views() or new module

```python
def ensure_vss_setup(connection: duckdb.DuckDBPyConnection) -> None:
    """Install VSS, enable persistence, create HNSW indexes."""
    connection.execute("INSTALL vss")
    connection.execute("LOAD vss")
    connection.execute("SET hnsw_enable_experimental_persistence = true")

    # HNSW index on final_results embeddings
    try:
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_final_results_emb
            ON final_results USING HNSW (embedding)
            WITH (metric = 'cosine', ef_construction = 128, M = 16)
        """)
    except Exception:
        pass  # May already exist or not yet supported with IF NOT EXISTS
```

#### Step 3: Store embeddings at insert time

**Modify:** `insert_final_results()` in `duckdb_store.py`

Accept an optional `embedding` keyword argument and include it in the INSERT:

```python
def insert_final_results(
    *,
    db_path: str | None = None,
    **kwargs: Any,
) -> None:
    # ... existing guard clauses ...
    
    embedding = kwargs.pop("embedding", None)
    
    columns = [
        "run_key", "rank", "title", "link", "snippet", "domain",
        "final_score", "providers", "provider_count", "entities_count",
        "embedding", "payload_json",
    ]
    values = [kwargs.get(col) for col in columns[:-2]] + [embedding, kwargs.get("payload_json")]
    # ... rest of insert logic ...
```

**Modify:** The search pipeline where `insert_final_results` is called to pass the query embedding as the result embedding (or compute per-result embeddings).

#### Step 4: Create query_embeddings table

**Modify:** `duckdb_store.py`

```python
_QUEMB_TABLE_NAME = "query_embeddings"

def _ensure_query_embeddings(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(f"""
        CREATE TABLE IF NOT EXISTS {_QUEMB_TABLE_NAME} (
            run_key VARCHAR PRIMARY KEY,
            query VARCHAR NOT NULL,
            embedding FLOAT[1024],
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # HNSW index
    try:
        connection.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_query_emb_hnsw
            ON {_QUEMB_TABLE_NAME} USING HNSW (embedding)
            WITH (metric = 'cosine', ef_construction = 128, M = 16)
        """)
    except Exception:
        pass
```

#### Step 5: Semantic Search Tool (New Capability)

**New file:** `src/kindly_web_search_mcp_server/search/semantic_search.py`

```python
"""Semantic search over historical results using DuckDB VSS.

Provides both direct ANN search and FTS→VSS hybrid search patterns.
"""

import duckdb
from ..analytics.duckdb_store import _db_path
from ..embeddings import embed_query


async def search_similar_past_results(
    query: str,
    top_k: int = 10,
    min_similarity: float = 0.7,
    days_back: int = 30,
    db_path: str | None = None,
) -> list[dict]:
    """Find past results semantically similar to the query.

    Uses HNSW-indexed cosine similarity for sub-5ms ANN search.
    """
    query_emb = await embed_query(query)
    path = _db_path(db_path)
    con = duckdb.connect(str(path), read_only=True)
    try:
        con.execute("LOAD vss")
        rows = con.execute("""
            SELECT
                run_key, title, link, snippet, domain, final_score,
                providers, provider_count,
                1.0 - array_cosine_distance(embedding, $emb::FLOAT[1024])
                    AS similarity
            FROM final_results
            WHERE embedding IS NOT NULL
              AND recorded_at > now() - INTERVAL $days DAY
            ORDER BY array_cosine_distance(embedding, $emb::FLOAT[1024])
            LIMIT $k
        """, {"emb": query_emb, "k": top_k, "days": days_back}).fetchall()

        return [
            {col[0] if isinstance(col, tuple) else col: val
             for col, val in zip(con.description or [], row)}
            for row in rows
            if row[-1] is not None and row[-1] >= min_similarity
        ]
    finally:
        con.close()


async def deduplicate_by_embedding(
    results: list[dict],
    threshold: float = 0.95,
) -> list[dict]:
    """Remove near-duplicate results using cosine similarity on embeddings.

    Computes embeddings, then drops any result with cosine similarity
    ≥ threshold to any already-kept result (order-preserving filter).
    """
    if len(results) <= 1:
        return results

    from ..embeddings import embed_texts

    texts = [f"{r.get('title', '')}. {r.get('snippet', '')}" for r in results]
    embeddings = await embed_texts(texts)

    kept: list[int] = [0]
    for i in range(1, len(embeddings)):
        is_dup = any(
            _cosine_similarity(embeddings[i], embeddings[j]) >= threshold
            for j in kept
        )
        if not is_dup:
            kept.append(i)

    return [results[i] for i in kept]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0
```

#### Step 6: Hybrid Search (FTS + VSS) — Phase 3

```sql
-- Pattern: BM25 retrieves candidates, HNSW reranks semantically
INSTALL fts; LOAD fts;
INSTALL vss; LOAD vss;

PRAGMA create_fts_index('final_results', 'link', 'title', 'snippet');

WITH bm25_candidates AS (
    SELECT link, title, snippet, embedding
    FROM final_results
    WHERE match_bm25(link, $query_text) IS NOT NULL
    ORDER BY match_bm25(link, $query_text) DESC
    LIMIT 200
)
SELECT link, title, snippet
FROM bm25_candidates
ORDER BY array_cosine_distance(embedding, $query_emb::FLOAT[1024])
LIMIT 10;
```

### VSS Performance Projection

Based on DuckDB docs and real-world benchmarks:

| Dataset | HNSW Build | Memory (Index) | Query (Top-10) | Status |
|---------|-----------|----------------|---------------|--------|
| 10K vectors | ~5s | ~50 MB | <1ms | ✅ Comfortable |
| 100K vectors | ~30s | ~500 MB | ~2ms | ✅ Comfortable |
| 1M vectors | ~5 min | ~5 GB | ~5ms | ⚠️ Monitor RAM |
| 10M+ vectors | ~1 hr | ~50 GB | ~10ms+ | ❌ Use Qdrant instead |

**Strategy:** Keep DuckDB VSS for recent results (last 30 days, ~10K-100K vectors). Remote Qdrant handles long-term archival. Compact index weekly:
```sql
PRAGMA hnsw_compact_index('idx_final_results_emb');
```

### VSS Sequencing

```
Phase 1 (Week 1-2):
├── Add embedding column to final_results (schema migration)
├── Accept embedding in insert_final_results()
├── Compute and store per-result embeddings in search pipeline
└── Create HNSW index on final_results

Phase 2 (Week 3):
├── Create query_embeddings table + HNSW index
├── Store query embeddings at search time
├── Add semantic_search.py module with search_similar_past_results()
└── Add deduplicate_by_embedding() to result processing pipeline

Phase 3 (Week 4+):
├── Add hybrid FTS + VSS search
├── Add similar-queries suggestion feature
├── Add periodic index compaction
└── Performance monitoring (index size, query latency)
```

### Key Decision Points for VSS

1. **Persistence toggle:** `hnsw_enable_experimental_persistence = true` is needed for disk-backed databases. Accept the WAL recovery risk (rebuild index on crash) or keep index in memory only (rebuild on every restart)?

2. **Embedding scope:** Store the query embedding as the result embedding (simple), or compute per-result embeddings from title+snippet content (more accurate, more API calls)?

3. **VSS vs Qdrant:** VSS is local, Qdrant is remote. VSS for hot/recent results, Qdrant for long-term archive — or keep both as complementary layers?

4. **Index compaction cadence:** Weekly `PRAGMA hnsw_compact_index()` is recommended after deletes. How often are results deleted from the analytics DB?

---

## Combined Impact Summary

| Dimension | Current State | After Flock + VSS |
|-----------|--------------|-------------------|
| Judge evaluation | Python API → DuckDB INSERT | Single SQL statement (Flock) |
| Cost tracking | Manual `extract_llm_usage()` | Auto `flock_get_metrics()` |
| Semantic search over past results | Impossible | Sub-5ms ANN search (VSS HNSW) |
| Duplicate result detection | Text-only (title/snippet match) | Embedding cosine similarity ≥0.95 |
| Similar query suggestions | Impossible | ANN over `query_embeddings` |
| LLM rerank | Python litellm → reorder list | Single SQL aggregate (Flock `llm_rerank`) |
| Batch judge evaluation | Python loop over all runs | One SQL query (Flock) |
| Embedding storage | Not persisted | `FLOAT[1024]` column with HNSW index |

## Pre-Requisites Checklist

- [ ] Bump `pyproject.toml` DuckDB constraint from `<1.5.3` to `>=1.5.0`
- [ ] Verify DuckDB 1.5.0+ is installed in all environments (dev, test, prod)
- [ ] Verify OpenAI API key (or alternative provider) is configured and working
- [ ] Confirm extension directory `.duckdb_extensions/v1.5.3/` is writable
- [ ] Check current `search_events.duckdb` size — if >5GB, plan for Parquet archival before adding indexes
- [ ] Verify `embedding_dim = 1024` matches HF model output consistently
- [ ] Ensure no other DuckDB extensions conflict with Flock/VSS

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Flock community extension breaks on DuckDB upgrade | Medium | High | Pin DuckDB version; test before upgrade |
| VSS experimental persistence causes data corruption | Low | High | Accept rebuild-index-on-restart; keep raw data in Parquet |
| Flock API costs escalate (batch judge at scale) | Medium | Medium | Monitor `flock_get_metrics()`; add daily spend cap |
| HNSW index exceeds available RAM | Low | Medium | Keep hot-window small (30 days); archive to Parquet |
| Flock + VSS conflict with each other or other extensions | Low | Low | Test in isolation; DuckDB extensions are sandboxed |
| Provider rate limits hit during batch Flock operations | Medium | Medium | Add `batch_size` to Flock models (already configurable) |

## Testing Strategy

### Flock Tests (new file: `tests/test_flock_integration.py`)

```
- test_flock_install_load: INSTALL flock FROM community; LOAD flock
- test_flock_create_model: CREATE MODEL ... IDEMPOTENT
- test_flock_create_prompt: CREATE PROMPT ... IDEMPOTENT
- test_flock_llm_complete_basic: SELECT llm_complete({...}, {...})
- test_flock_judge_evaluation: Judge 3 sample results, verify INSERT
- test_flock_batch_judge: Judge 5 runs at once
- test_flock_metrics: SELECT * FROM flock_get_metrics()
- test_flock_fallback: Flock failure → Python path fallback
```

### VSS Tests (new file: `tests/test_vss_integration.py`)

```
- test_vss_install_load: INSTALL vss; LOAD vss
- test_vss_create_table: CREATE TABLE ... (emb FLOAT[1024])
- test_vss_create_index: CREATE INDEX ... USING HNSW (emb) WITH (metric='cosine')
- test_vss_insert_search: INSERT → ORDER BY array_cosine_distance LIMIT k
- test_vss_min_by: SELECT min_by(table, array_distance(emb, q), k)
- test_vss_embedding_column: ALTER TABLE final_results ADD COLUMN embedding
- test_vss_persistence: Restart → verify index loads (if experimental enabled)
- test_vss_benchmark: 10K vectors, measure query latency
```

### Existing Tests to Update

- `tests/test_analytics_views.py` — Add VSS column to expected schema
- `tests/test_duckdb_analytics.py` — Add Flock/VSS table creation
- `tests/test_pipeline_tables.py` — Add embedding column to final_results assertions
- `pyproject.toml` — Bump DuckDB constraint

## Notes

- Flock and VSS target **different DuckDB databases**: Flock operates on `search_events.duckdb` (analytics), while VSS operates on `final_results` within the same database. The cache databases (`page_cache.duckdb`, `transcript_cache.duckdb`) are not affected.
- The existing Qdrant remote index (`web_results_index.py`) remains as the long-term archival path. DuckDB VSS adds local-first, low-latency search for recent results.
- The existing HF Inference embedding pipeline remains for generating the vectors. Flock's `llm_embedding` is an alternative (OpenAI embeddings), not a replacement — HF Inference is free, OpenAI charges.
- This plan assumes DuckDB 1.5.0+. If a downgrade is needed, Flock (requires ≥1.5.0) would be incompatible, but VSS (requires ≥0.10.2) would still work.
- All new tables follow the existing naming convention (`_ensure_*` pattern in `duckdb_store.py`).
- Both extensions write to `search_events.duckdb` — the existing `threading.Lock()` in `duckdb_store.py` already serializes writes.
