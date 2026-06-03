# Entity-Aware Result Memory Plan

**Merged plan**: GLiNER2 entity extraction + cache repurposing (result memory) + storage migration (LanceDB → Qdrant + DuckDB).

Date: 2026-06-03

## Problem Statement

The current cache architecture is fundamentally mismatched to the workload:

- **0% hit rate** over 48h (93 misses, 2 expired, 0 hits from 95 lookups — observability report 2026-06-03)
- **3.19s avg latency** for cache lookups that never hit — 60% of the full search pipeline cost
- Two of three LanceDB tables (`query_cache_v2`, `page_cache`) use no vector search at all — LanceDB is acting as a slow key-value store
- The exact query cache key includes 5 parameters (`query + num_results + rewrite_enabled + search_mode + providers_key`) making replay probability near-zero for AI agent workloads (291 unique queries in 48h)
- The semantic cache threshold (0.92) requires near-identical meaning; binary hit/miss means partial matches contribute nothing
- No entity understanding exists in the pipeline — the system cannot distinguish "FastMCP 2.14" from "FastMCP 3.x" at the cache/rerank level

The fix is not tuning thresholds. The fix is **repurposing the query store from a binary cache into an entity-aware result memory** that enriches future searches, and adding **GLiNER2 entity extraction** so the memory (and the whole pipeline) understands what queries and results are *about*.

## Architecture Overview

```
BEFORE (current):
  Query → [Exact Cache (miss)] → [Semantic Cache (miss)] → Rewrite → Search → RRF Merge → Rerank → Response
                                                                         ↑ cache stores after response
                                                                         (but never retrieves because keys never match)

AFTER (proposed):
  Query → [GLiNER Entity Extraction on Query] ──────────────────────────────────┐
          ↓                                                                      │
          [Result Memory: vector search for similar past queries]               │
          ↓ candidates from similar past queries                                 │
          [Rewrite: informed by entity context from memory]                      │
          ↓                                                                      │
          [Search providers] → [RRF Merge + historical candidates injection]    │
          ↓                                                                      │
          [Rerank: entity overlap features boost relevant candidates]            │
          ↓                                                                      │
          [Store: results + entities → Result Memory + DuckDB]                 │
          ↓                                                                      │
          Response (with entity annotations)  ←─────────────────────────────────┘
```

## Subproblem Decomposition

### Layer 0: Storage Migration (Prerequisite)

**Sub 0.1: Replace exact query cache with in-memory LRU**

- **Why**: The exact cache (`query_cache.py`) keys on `SHA256(query + num_results + rewrite_enabled + search_mode + providers_key)`. This never hits because AI agents never repeat the exact same query with identical parameters. LanceDB is massively overkill for key-value lookup.
- **How**: Replace `ExactQueryCache` (LanceDB-backed) with a simple Python `OrderedDict`-based LRU with TTL checking. The existing TTL logic (line 181-208) is preserved. Max size ~1000 entries, TTL 24h. Drop-in replacement in `cache/query_cache.py`.
- **Latency impact**: ~0.01ms vs current ~500ms+ LanceDB filter query.
- **File changes**: `cache/query_cache.py` (rewrite internals), `cache/__init__.py` (update exports), `settings.py` (replace `lancedb_dir` with irrelevant), `server.py` (no change — same API).

**Sub 0.2: Move page cache to DuckDB**

- **Why**: Page cache (`page_cache.py`) does SHA256 URL-hash exact match. No vectors. Already has SQL-like filtering. DuckDB already exists as a dependency for analytics.
- **How**: Create a `page_cache` table in the existing DuckDB analytics store (`.kindly/analytics/search_events.duckdb`) or a separate `page_cache.duckdb`. Schema: `(id TEXT, url_canonical TEXT, url_hash TEXT, page_content TEXT, extraction_method TEXT, word_count BIGINT, created_at TEXT, ttl_seconds BIGINT, metadata_json TEXT)`. Lookup: `SELECT * FROM page_cache WHERE url_hash = ? AND age < ttl`. Store: `INSERT INTO page_cache VALUES (?, ?, ...)`.
- **Latency impact**: ~1ms for simple indexed lookup vs current LanceDB filter.
- **File changes**: `cache/page_cache.py` (rewrite internals from LanceDB → duckdb), `cache/__init__.py`, `analytics/duckdb_store.py` (add page_cache schema to `ensure_schema` or separate file).
- **Note**: DuckDB has a 1-write-at-a-time limitation. The page cache has low write frequency (same as current), so this is fine. Use `duckdb.connect(path, read_only=False)` for writes, separate read connection for reads.

**Sub 0.3: Replace semantic cache with Qdrant-based result memory**

- **Why**: The semantic cache (`semantic_cache.py` + `store.py`) is the ONLY current use of vector search, but its 3.19s avg latency is unacceptable. Qdrant's HNSW index provides ~30ms vector search latency (100x faster) with 95%+ recall. The hybrid search (FTS + vector + RRF) that LanceDB provides adds no value when the results go to a downstream cross-encoder reranker anyway.
- **How**: New module `cache/result_memory.py` wrapping `qdrant_client.QdrantClient(":memory:")`. Each point stores: `{id, vector: embedding, payload: {query_text, answer_json, entities_json, content_type, provider_key, created_at, result_count}}`. Lookup returns top-K similar queries (K=5, threshold=0.65) instead of binary hit/miss. No FTS — vector-only HNSW search is sufficient since the reranker downstream does better relevance scoring than RRF.
- **Persistent option**: `QdrantClient(path="./qdrant_data")` for disk persistence across restarts. Controlled by `KINDLY_RESULT_MEMORY_PATH` setting.
- **File changes**: New `cache/result_memory.py`, remove `cache/store.py`, remove `cache/semantic_cache.py`, update `cache/__init__.py`, update `cache/schema.py` (remove or simplify), update `server.py` cache integration (lines 600-820).
- **Dependency changes**: Add `qdrant-client` to `pyproject.toml`. Remove `lancedb` and `pyarrow` from core dependencies.

**Sub 0.4: Remove LanceDB dependency**

- **Why**: After 0.1, 0.2, and 0.3, no code uses LanceDB. Removes ~50MB install size and the `pyarrow` transitive dependency.
- **How**: Remove from `pyproject.toml` dependencies. Remove all `import lancedb` references. Delete `cache/store.py`, `cache/schema.py`. Remove `settings.py:lancedb_dir`. Clean up FTS index code in store.py (dead).
- **Risk**: Any existing data in `./lancedb_data` will become unreadable. Add a migration note in CHANGELOG.

### Layer 1: GLiNER2 Entity Extraction Foundation

**Sub 1.1: Entity extraction client**

- **Why**: The entire entity-aware pipeline depends on a reliable, lazy-loaded, swappable extraction backend.
- **How**: New module `entity/gliner_client.py` with:
  ```python
  class ExtractionClient(ABC):
      async def extract_entities(self, text: str, labels: dict[str, str] | list[str],
                                 threshold: float = 0.5) -> list[EntitySpan]: ...
      async def classify_content(self, text: str, labels: dict) -> dict: ...

  class GLiNER2LocalClient(ExtractionClient):
      """Lazy-loaded local GLiNER2 model (gliner2-base-v1)."""
      def __init__(self, model: str = "fastino/gliner2-base-v1", threshold: float = 0.5):
          self._model = None  # Lazy: loaded on first call
          self._model_name = model
          self._threshold = threshold

      def _get_model(self):
          if self._model is None:
              from gliner2 import GLiNER2
              self._model = GLiNER2.from_pretrained(self._model_name)
          return self._model

      async def extract_entities(self, text, labels, threshold=None):
          model = self._get_model()
          result = await asyncio.to_thread(
              model.extract_entities, text, labels, threshold or self._threshold
          )
          # Convert to EntitySpan list (normalized output)
          ...
  ```
- **Lazy loading pattern**: Same as existing `rerank/core.py:_rerank_results` singleton — first call loads, subsequent calls reuse. Model stays in memory (~0.5-1GB RAM for base model).
- **Thread safety**: GLiNER2 inference is CPU-bound. Wrap with `asyncio.to_thread()` to avoid blocking the FastMCP event loop (same pattern as existing reranker calls).
- **Latency budget**: ~25ms/chunk for NER. For a typical query (~100 chars), single chunk → ~25ms. For fetched content (5-20K chars), chunk into ~800-char chunks with 100-char overlap → 7-25 chunks → 175-625ms total. Acceptable because content fetch already takes 7-40s (observability report).

**Sub 1.2: Default entity schema for tech/web content**

- **Why**: GLiNER2 accuracy depends heavily on label descriptions. Pre-baked domain-specific labels maximize zero-shot performance for coding assistant use case.
- **How**: New module `entity/default_schema.py`:
  ```python
  DEFAULT_QUERY_LABELS: dict[str, str] = {
      "package": "Software package, library, or framework name (e.g. FastMCP, React, numpy, pydantic)",
      "version": "Software version string (e.g. 2.14.5, v3.0.0, 1.0.0-beta)",
      "api_function": "API endpoint, function, or method name (e.g. FastMCP.tool, requests.get, useState)",
      "error_class": "Error or exception class name (e.g. ImportError, TypeError, HTTPStatusError)",
      "repo_ref": "GitHub/ GitLab repository reference (e.g. owner/repo, owner/repo#123)",
      "cli_flag": "Command-line flag or argument (e.g. --verbose, -rf, --port 8000)",
      "model_id": "ML model identifier (e.g. bert-base-uncased, gpt-4o, voyage-3)",
      "file_path": "File path or module path (e.g. src/app.ts, kindli_web_search_mcp_server.server)",
      "env_var": "Environment variable name (e.g. SEARXNG_BASE_URL, KINDLY_RERANKING_ENABLED)",
  }

  DEFAULT_CONTENT_LABELS: dict[str, str] = {
      **DEFAULT_QUERY_LABELS,
      "person": "Person name (developer, author, maintainer)",
      "organization": "Company, team, or organization (e.g. Microsoft, Fastino AI, Hugging Face)",
      "date": "Date or time expression (e.g. 2025-06-03, last week, June 2025)",
      "product": "Product name (e.g. iPhone 15, Azure OpenAI, Cloud Run)",
      "url": "URL or web address",
  }

  DEFAULT_CLASSIFICATION_LABELS: dict[str, list[str]] = {
      "content_type": ["technical_doc", "news", "faq", "api_reference", "changelog", "discussion", "general"],
  }
  ```
- **Why descriptions matter**: The GLiNER paper and community reports consistently show that descriptions are the #1 accuracy lever. Without descriptions, GLiNER2 is just matching label names; with descriptions, it uses the full encoder context for precise span detection.

**Sub 1.3: Chunking and offset correction for long content**

- **Why**: Fetched page content can be 5-20K+ chars. GLiNER2 handles ~512 tokens per call (~800-1200 chars). Need chunking with correct offset math so extracted entity spans map back to the original text.
- **How**: New module `entity/chunk.py`:
  ```python
  def chunk_text(
      text: str,
      chunk_size: int = 1000,
      overlap: int = 150,
  ) -> list[tuple[int, str]]:
      """Split text into overlapping chunks with word-boundary respect.

      Returns list of (global_start_offset, chunk_text) tuples.
      Uses existing _find_boundary logic from content/windowing.py.
      """
      ...
  ```
- **Offset correction**: After extraction on each chunk, every entity's `start` and `end` get `+ chunk_global_offset`. This is exactly the pattern from `theirstory/gliner-spacy` pipeline.py, validated in production.
- **Reuse**: The existing `content/windowing.py:_find_boundary` function already handles word-boundary extension. Import and reuse it.

**Sub 1.4: Post-processing pipeline**

- **Why**: Raw GLiNER2 output can have: overlapping spans, duplicate entities across chunk boundaries, low-confidence noise, label boundary bleed (e.g. "Mr." captured in person name).
- **How**: New module `entity/postprocess.py` with stages:
  1. **Validate**: Apply label-specific regex validators (e.g., version must match `\d+\.\d+`, repo_ref must match `[\w-]+/[\w-]+`). GLiNER2 has `RegexValidator` built in, but we can add post-hoc validation too.
  2. **Dedup**: By `(label, normalized_text)` (case-insensitive), keep max confidence.
  3. **Merge overlaps**: If two entity spans of the same label overlap, keep the longer one or the higher-confidence one.
  4. **Normalize**: Strip leading/trailing punctuation from spans; canonicalize versions (e.g. "v2.14" → "2.14").
- **Inspired by**: `gantz-ai/pii.engineer`'s 8-stage pipeline (reclassify→validate→filter→normalize→...→dedup→merge). We implement a lighter 4-stage version.

### Layer 2: Entity-Aware Result Memory

**Sub 2.1: Store search results + entities in result memory**

- **Why**: Current semantic cache stores `answer_json` (full serialized response) keyed by query embedding. Instead, store individual result items with their entity metadata so they can be selectively retrieved and injected into future searches.
- **How**: After each successful `web_search` call (in `server.py` ~line 782-820), in addition to storing the full response, upsert individual high-quality results into the result memory:
  ```python
  # After search completes, in the cache-write section:
  for result in final_results[:10]:  # Top 10 results only
      result_memory.store_result(
          query_embedding=query_embedding,
          query_text=normalized_query,
          query_entities=extracted_query_entities,  # From GLiNER
          result_url=result.link,
          result_title=result.title,
          result_snippet=result.snippet,
          result_entities=extract_result_entities(result),  # GLiNER on title+snippet
          content_type=classify_via_entities(extracted_query_entities),
          provider_key=provider_cache_key(providers),
          created_at=utcnow_iso(),
      )
  ```
- **Qdrant payload per point**:
  ```python
  {
      "query_text": str,          # Original query that produced this result
      "result_url": str,          # URL of the result
      "result_title": str,        # Title
      "result_snippet": str,      # Snippet
      "entities_json": str,       # JSON: [{text, label, confidence}, ...] from GLiNER
      "content_type": str,        # technical/news/faq/general
      "provider_key": str,        # Which providers were used
      "created_at": str,          # ISO timestamp
  }
  ```

**Sub 2.2: Result memory lookup — candidate pool mode**

- **Why**: Instead of binary hit/miss, the result memory should return candidates from semantically similar past queries. These candidates get injected into the RRF merge. The reranker acts as the quality gate, so we can use a lower similarity threshold (0.65 vs current 0.92).
- **How**: New function `result_memory.py:lookup_candidates()`:
  ```python
  def lookup_candidates(
      self,
      query_embedding: list[float],
      query_entities: list[EntitySpan] | None = None,
      limit: int = 5,
      min_similarity: float = 0.65,
  ) -> list[CandidateResult]:
      """Return candidate results from similar past queries.

      Uses Qdrant vector search (HNSW, cosine).
      If query_entities provided, payloads with entity overlap get boosted.
      """
      results = self._client.search(
          collection_name="result_memory",
          query_vector=query_embedding,
          limit=limit * 3,  # Over-fetch for entity filtering
          score_threshold=min_similarity,
      )

      candidates = []
      for hit in results:
          entities = json.loads(hit.payload.get("entities_json", "[]"))
          entity_overlap = _compute_entity_overlap(query_entities, entities)
          age_hours = (now - parse_iso(hit.payload["created_at"])).total_seconds() / 3600

          # Boost by entity overlap, decay by age
          adjusted_score = hit.score * (1 + 0.2 * entity_overlap) * math.exp(-0.01 * age_hours)

          candidates.append(CandidateResult(
              url=hit.payload["result_url"],
              title=hit.payload["result_title"],
              snippet=hit.payload["result_snippet"],
              similarity=hit.score,
              entity_overlap=entity_overlap,
              adjusted_score=adjusted_score,
              cached_at=hit.payload["created_at"],
              source_query=hit.payload["query_text"],
          ))

      candidates.sort(key=lambda c: c.adjusted_score, reverse=True)
      return candidates[:limit]
  ```

**Sub 2.3: Entity overlap computation**

- **Why**: This is the core synergy between GLiNER and result memory. Without it, "FastMCP 2.x" and "FastMCP 3.x" look identical by embedding similarity. With entity overlap, we can precisely quantify that they share a package but differ in version.
- **How**: New function in `entity/overlap.py`:
  ```python
  def compute_entity_overlap(
      query_entities: list[EntitySpan],
      candidate_entities: list[dict],
  ) -> float:
      """Compute weighted entity overlap score between query and candidate.

      Matching logic:
      - Exact match on (label, text): full weight (e.g., both have package=FastMCP)
      - Same label, different text: partial weight for compatible types (e.g., both have version but different values)
      - Version mismatch: NEGATIVE weight — this is the "FastMCP 2.x vs 3.x" guardrail

      Returns: float in [-1.0, 1.0]
      """
      ...
  ```
- **Weight map**:
  | Label | Exact match weight | Mismatch penalty |
  |-------|-------------------|-------------------|
  | package | 0.3 | -0.5 (different package = probably irrelevant) |
  | version | 0.2 | -0.4 (same package, different version = likely stale) |
  | error_class | 0.25 | -0.1 (different error = might still be related) |
  | api_function | 0.15 | 0 (different API = neutral) |
  | repo_ref | 0.25 | -0.3 (different repo = probably different context) |

### Layer 3: Pipeline Integration

**Sub 3.1: Hook GLiNER into query_policy (always-on)**

- **Why**: The current `query_policy.py:_extract_must_keep_terms` uses regex only. GLiNER catches what regex misses: package names, model IDs, complex version strings like "FastMCP@2.14.5", error classes like "ImportError from starlette.middleware".
- **How**: Extend `_extract_must_keep_terms` in `search/query_policy.py`:
  ```python
  async def _extract_must_keep_terms(query: str) -> list[str]:
      # Existing regex extraction (unchanged)
      regex_terms = _regex_must_keep(query)

      # GLiNER augmentation (always-on if model loaded)
      gliner_terms = []
      if _gliner_available():
          try:
              entities = await _get_gliner().extract_entities(
                  query, DEFAULT_QUERY_LABELS, threshold=0.5
              )
              # Only add entities that regex didn't already catch
              gliner_terms = [
                  e.text for e in entities
                  if e.text not in regex_terms and e.confidence >= 0.5
              ]
          except Exception:
              pass  # Never fail the query path on extraction errors

      combined = list(set(regex_terms + gliner_terms))
      return combined
  ```
- **Latency budget**: Query is ~100 chars. GLiNER2 on single chunk: ~25ms. Negligible vs the 12s query rewrite average.
- **Fallback**: If GLiNER2 model fails to load or times out, silently fall back to regex-only. Zero risk to core search path.

**Sub 3.2: Content-type classification via entities**

- **Why**: The current `content_type.py:classify_content_type` uses keyword sets. GLiNER entities provide a richer, more accurate signal.
- **How**: Add an entity-aware classifier in `cache/content_type.py`:
  ```python
  def classify_from_entities(entities: list[EntitySpan]) -> ContentType | None:
      """Classify content type based on extracted entities."""
      labels = {e.label for e in entities}
      entity_texts = {e.text.lower() for e in entities}

      if "date" in labels and "person" in labels and "organization" in labels:
          return ContentType.NEWS
      if {"package", "version", "api_function"} & labels:
          return ContentType.TECHNICAL
      if {"error_class", "cli_flag", "env_var"} & labels:
          return ContentType.TECHNICAL
      return None
  ```
- **Integration**: Call this after GLiNER extraction, use as fallback/enrichment for the keyword heuristic. If entity-based classification is confident, override keyword classification.

**Sub 3.3: Inject historical candidates into RRF merge**

- **Why**: This is the highest-impact integration. Instead of the cache returning "hit" or "miss", it contributes candidates to the merge, making the pipeline *collectively smarter over time*.
- **How**: Modify `orchestrator.py:run_web_search` between lines 219 and 232 (after provider search, before RRF merge):
  ```python
  # After: result_lists = await asyncio.gather(*search_tasks)
  # Before: merged = merge_search_results(result_lists, ...)

  # Inject historical candidates from result memory
  if settings.result_memory_enabled:
      try:
          memory_candidates = result_memory.lookup_candidates(
              query_embedding=query_embedding,  # Shared from embed-once
              query_entities=query_entities,     # From GLiNER extraction
              limit=settings.result_memory_candidate_limit,  # Default: 5
              min_similarity=0.65,
          )
          if memory_candidates:
              # Convert to WebSearchResult objects and add as a virtual "result_list"
              historical_results = [
                  WebSearchResult(
                      title=c.title,
                      link=c.url,
                      snippet=c.snippet,
                      domain=_extract_domain(c.url),
                      providers=["result_memory"],
                      raw_score=0.0,  # Will be scored by RRF
                      resource_type="cached",
                  )
                  for c in memory_candidates
              ]
              result_lists.append(historical_results)
              list_weights.append(0.5)  # Lower weight than fresh results
              # Emit observability
              emit_observability_event(...)
      except Exception as exc:
          logger.warning("Result memory candidate injection failed: %s", exc)
  ```
- **Why `list_weights.append(0.5)`**: Historical candidates from the memory have already been validated by a previous rerank pass, but may be stale. A lower weight in RRF ensures they are considered but don't dominate fresh provider results.
- **Dedup guarantee**: The `merge_search_results` function already deduplicates by canonical URL (`merge.py:_pick_better`). If a URL appears in both fresh results and memory, RRF keeps the higher-scoring version.

**Sub 3.4: Entity-informed query rewrite context**

- **Why**: The current rewrite generates variants in a vacuum. Showing the LLM what entities similar past queries found makes variants more targeted.
- **How**: Modify `query_rewrite.py` to include historical context in the rewrite prompt:
  ```python
  async def _get_rewrite_context(query: str, query_entities: list[EntitySpan]) -> str | None:
      """Get context from historically similar queries to inform rewriting."""
      if not settings.result_memory_enabled or not query_entities:
          return None
      try:
          candidates = result_memory.lookup_candidates(
              query_embedding=..., query_entities=query_entities,
              limit=3, min_similarity=0.55,
          )
          if not candidates:
              return None
          context_parts = []
          for c in candidates:
              entity_str = ", ".join(f"{e['label']}:{e['text']}" for e in c.entities)
              context_parts.append(
                  f"Similar past query '{c.source_query}' (overlap={c.entity_overlap:.0%}) "
                  f"found results about: {entity_str}"
              )
          return "\n".join(context_parts[:3])
      except Exception:
          return None
  ```
- **Prompt integration**: Append to the rewrite prompt template:
  ```
  Historical context from similar past queries (use to inform but not repeat):
  {rewrite_context}
  ```
- **Cost**: ~200-500 additional tokens in the rewrite prompt. Negligible vs the 12s rewrite average.

**Sub 3.5: Entity overlap as rerank feature**

- **Why**: The cross-encoder reranker uses dense text similarity. Entity overlap provides an explainable, precise signal that complements it — especially for exact version/package matches where text similarity is approximate but entity match is exact.
- **How**: Modify `rerank/core.py` to add an `entity_overlap_score` field:
  ```python
  # After reranker scores each (query, result) pair:
  for result in results:
      if query_entities and hasattr(result, 'entities'):
          overlap = compute_entity_overlap(query_entities, result.entities or [])
          # Blend: entity overlap adds a boost on top of the cross-encoder score
          # Weight: 10% entity overlap, 90% cross-encoder (conservative start)
          result.rerank_score = (
              0.9 * result.rerank_score +
              0.1 * (overlap * result.rerank_score)  # Proportional boost
          )
  ```
- **Observable**: Emit `entity_overlap` in rerank observability events so we can tune the weight.
- **Conservative start**: 10% weight ensures entity overlap adds signal without overwhelming the trained cross-encoder. Tune upward if observability shows clear improvements.

**Sub 3.6: GLiNER on fetched content (content extraction)**

- **Why**: Agents currently receive raw markdown and must parse it themselves. GLiNER2 turns fetched pages into typed, grounded data: package names, versions, APIs, error classes — all with char offsets into the page_content they receive.
- **How**: Hook into `content/fetch_pipeline.py` after markdown is ready (post special resolvers, post http_extract/universal_html):
  ```python
  # In fetch_content_artifact, after page_content is produced:
  if settings.entity_extraction_enabled:
      try:
          chunks = chunk_text(page_content, chunk_size=1000, overlap=150)
          all_entities = []
          for offset, chunk in chunks:
              chunk_entities = await gliner_client.extract_entities(
                  chunk, DEFAULT_CONTENT_LABELS, threshold=0.5
              )
              for e in chunk_entities:
                  e.start += offset
                  e.end += offset
              all_entities.extend(chunk_entities)
          all_entities = postprocess_entities(all_entities)
          artifact.entities = all_entities
      except Exception as exc:
          logger.warning("Content entity extraction failed: %s", exc)
  ```
- **Latency budget**: 10K char page → ~10 chunks × 25ms/chunk = 250ms. Acceptable on top of 7-40s fetch.
- **Response shape**: Add `entities: list[EntitySpan] | None` to `GetContentResponse`. Backward compatible (None by default for clients not requesting it).

**Sub 3.7: GLiNER on search result titles/snippets**

- **Why**: Short text (title + snippet) is very cheap to extract (~25ms for a full results set). Annotating results with entities gives agents immediate type information without fetching.
- **How**: After RRF merge, batch-extract entities on title + snippet for top results:
  ```python
  # In orchestrator.py, after reranking and before final_results slice:
  if settings.entity_extraction_enabled:
      try:
          for result in final_results[:num_results]:
              text = f"{result.title} {result.snippet}"
              result.entities = await gliner_client.extract_entities(
                  text, DEFAULT_QUERY_LABELS, threshold=0.5
              )
      except Exception:
          pass  # Never fail the search path
  ```
- **Add field to `WebSearchResult`**: `entities: list[EntitySpan] | None = Field(default=None, description="...")`. Additive, backward compatible.

### Layer 4: Observability and Analytics

**Sub 4.1: Entity events in DuckDB analytics**

- **Why**: Track extraction quality, entity distribution, and result memory hit rates in the existing analytics pipeline.
- **How**: Extend `analytics/duckdb_store.py:_ensure_schema` with entity tables:
  ```sql
  CREATE TABLE IF NOT EXISTS entity_events (
      event_id TEXT PRIMARY KEY,
      event_type TEXT,  -- 'query_extraction', 'result_extraction', 'content_extraction'
      query_text TEXT,
      entity_label TEXT,
      entity_text TEXT,
      confidence REAL,
      source_type TEXT,  -- 'query', 'result_snippet', 'fetched_content'
      provider_key TEXT,
      created_at TEXT
  );

  CREATE TABLE IF NOT EXISTS result_memory_events (
      event_id TEXT PRIMARY KEY,
      event_type TEXT,  -- 'lookup', 'store', 'candidate_injected', 'candidate_used'
      query_text TEXT,
      similarity REAL,
      entity_overlap REAL,
      candidates_returned INTEGER,
      candidates_injected INTEGER,
      created_at TEXT
  );
  ```

**Sub 4.2: Replace cache hit-rate metrics with result memory utilization metrics**

- **Why**: Binary cache hit rate is no longer the right metric. We need to track: how often does the memory contribute useful candidates? How many of the injected candidates survive to the final results?
- **How**: New metrics in `telemetry.py`:
  ```python
  RESULT_MEMORY_LOOKUP = "result_memory.lookup"
  RESULT_MEMORY_CANDIDATES_INJECTED = "result_memory.candidates_injected"
  RESULT_MEMORY_CANDIDATES_SURVIVED = "result_memory.candidates_survived"  # After rerank
  ENTITY_EXTRACTION_LATENCY = "entity.extraction_latency"
  ENTITY_COUNT_BY_LABEL = "entity.count_by_label"
  ```

## Gap Analysis and Quick Wins

### Gap 1: Embed-once, use-twice

**Problem**: The current pipeline generates query embeddings twice — once for cache lookup, once for search (if using embedding-based providers). The result memory also needs the same embedding.
**Fix**: Refactor the pipeline so `embed_query(query)` is called once and the result is passed through. The embedding is already generated for the semantic cache at `server.py:664`; after removing the semantic cache, this call should be moved to the orchestrator where it can be shared.
**Quick win**: This alone drops ~0.5-1s from the cold path.

### Gap 2: Result memory cold start

**Problem**: The memory is empty on first run. No candidates will be available until enough queries have been stored.
**Fix**: Accept the cold start (it mirrors the current cache behavior of "always miss"). The memory fills naturally as queries are processed. At the current rate (~50-100 queries/day based on observability data), the memory will have ~500+ entries within a week, enough for meaningful candidate retrieval.
**Quick win**: Seed the memory with the 44 entries already stored in the current semantic cache. Write a one-time migration script that reads from LanceDB and writes to Qdrant.

### Gap 3: GLiNER2 model cold start

**Problem**: First GLiNER2 call must load the model from disk (~0.5-1GB), taking 2-5 seconds.
**Fix**: Trigger model load at server startup (not on first query). In `server.py` lifespan handler, call `_get_gliner()` eagerly if `KINDLY_ENTITY_EXTRACTION_ENABLED=true`. This moves the cold start to startup, not to the first user query.
**Quick win**: Set `KINDLY_ENTITY_EXTRACTION_ENABLED=false` by default. Power users opt in. Once stable, flip default to `true`.

### Gap 4: No mechanism to expire stale results from memory

**Problem**: Without expiry, the result memory grows indefinitely and old results become stale.
**Fix**: Use Qdrant's payload filtering with `created_at` to compute age at query time. Apply adaptive TTL based on `content_type` (reusing the existing `ADAPTIVE_TTL_SECONDS` map). Also add a periodic compaction task that deletes points older than their TTL.
**Quick win**: For the Qdrant `:memory:` mode, simply clear the collection on restart (no persistence). For disk mode, add a `compact_result_memory` function called on startup.

### Gap 5: SQL injection in exact query cache

**Problem**: `query_cache.py:142` has `table.search().where(f"cache_key = '{cache_key}'")`. The cache_key is SHA256-hex so it's safe in practice, but it's a bad pattern. The in-memory LRU replacement (Sub 0.1) eliminates this entirely.
**Quick win**: Already addressed by Sub 0.1.

### Gap 6: No dedup in result memory stores

**Problem**: If the same query is run twice, the result memory will store duplicate points.
**Fix**: Use Qdrant's `upsert` with a deterministic point ID derived from `SHA256(query_text + result_url)`. This ensures the same query-result pair is stored only once; subsequent stores update the `created_at` and `provider_key` payload.
**Quick win**: Implement in `result_memory.py:store_result()` from day one.

### Gap 7: Entity extraction on query may conflict with rewrite

**Problem**: GLiNER extracts entities from the normalized query, then the rewrite changes the query. The extracted entities still refer to the original query.
**Fix**: Extract entities from the ORIGINAL query (before normalization and rewrite), not from the rewritten variants. The original entities are what matter for result memory matching and must_keep_terms. Rewritten variants are for search provider targeting.
**Quick win**: Call GLiNER extraction ONCE on the original query in `server.py:web_search` (line ~600), before the rewrite step, and pass the entities down to both query_policy and the orchestrator.

### Gap 8: The current `content_type.py:classify_content_type` will be wrong sometimes

**Problem**: Keyword heuristics misclassify queries. "how to install python" matches both `_TECHNICAL_KEYWORDS` (how, implement, python) and `_FAQ_KEYWORDS` (how, install). The current code checks technical first, which is correct for most cases but not all.
**Fix**: When GLiNER entities are available, use them as the primary classification signal. If GLiNER detects technical entities, it's technical. If it detects news entities, it's news. Fall back to keywords only when GLiNER is not available or returns no entities.
**Quick win**: Sub 3.2 already covers this.

## Settings Reference

New environment variables:

```python
# Result Memory (replaces semantic_cache + exact query cache)
result_memory_enabled: bool = True  # ON by default (was semantic_cache_enabled)
result_memory_path: str = ""  # Empty = :memory:, path = persistent Qdrant
result_memory_candidate_limit: int = 5  # Max candidates to inject into merge
result_memory_min_similarity: float = 0.65  # Lower than old 0.92
result_memory_candidate_weight: float = 0.5  # RRF weight for historical candidates

# Entity Extraction
entity_extraction_enabled: bool = False  # OFF by default initially, ON once stable
gliner_model: str = "fastino/gliner2-base-v1"
gliner_threshold: float = 0.5
gliner_chunk_size: int = 1000  # Chars per chunk for content extraction

# Rerank entity overlap
rerank_entity_overlap_weight: float = 0.1  # 10% blend with cross-encoder

# Deprecated (remove after migration)
# lancedb_dir  ← REMOVE
# semantic_cache_enabled  ← REMOVE (replaced by result_memory_enabled)
# semantic_cache_min_score  ← REMOVE (replaced by result_memory_min_similarity)
```

## Execution Order

| Phase | Subproblems | Duration estimate | Dependencies |
|-------|-------------|-------------------|--------------|
| **0: Storage** | 0.1, 0.2, 0.3, 0.4 | 2-3 days | None |
| **1: GLiNER core** | 1.1, 1.2, 1.3, 1.4 | 3-4 days | None (parallel with Phase 0) |
| **2: Result memory** | 2.1, 2.2, 2.3 | 2-3 days | Phase 0 complete |
| **3: Pipeline integration** | 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7 | 4-5 days | Phases 1+2 complete |
| **4: Observability** | 4.1, 4.2 | 1-2 days | Phase 3 complete |

**Critical path**: 0.3 → 2.1 → 2.2 → 3.3 (result memory→candidate injection into RRF). This is the highest-impact change.

**Parallel track**: 1.1-1.4 can proceed independently of Phase 0. Phase 3.1 (query_policy hook) can land as soon as 1.1+1.2 are done, even before the result memory exists.

## Regression Risk

- **Phase 0**: Each storage migration (0.1, 0.2, 0.3) has zero behavioral change if done correctly — same data, same API, different backend. Test by running the focused test slice before and after each migration.
- **Phase 1**: GLiNER extraction is additive (new module, new entity fields). Existing tests should pass unchanged because entity fields default to `None`. New tests mock the GLiNER2 library.
- **Phase 2**: Result memory replaces semantic cache. The `server.py` integration (lines 600-820) needs careful refactoring. The candidate injection path (3.3) must be exception-safe: if memory lookup fails, proceed with fresh search results only.
- **Phase 3**: Each hook point (3.1, 3.3, 3.4, 3.5) must be independently disableable via settings. If any integration causes issues, it can be turned off without affecting others.

## Success Metrics

1. **Result memory candidate injection rate**: % of searches where ≥1 candidate is injected. Target: >30% after 1 week of operation.
2. **Candidate survival rate**: % of injected candidates that survive to final results (after rerank). Target: >10% (means the reranker finds them relevant).
3. **Latency**: End-to-end search latency should NOT increase. The embed-once fix (Gap 1) should compensate for the GLiNER extraction overhead. Cache lookup latency drops from 3.19s → ~30ms.
4. **Zero-hit elimination**: Binary cache hit rate was 0%. Result memory utilization >0% from day 1.
5. **Entity extraction correctness**: Spot-check 50 real queries, manually verify GLiNER entities match ground truth. Target: >80% precision.

## Files to Create

- `src/kindly_web_search_mcp_server/entity/__init__.py`
- `src/kindly_web_search_mcp_server/entity/gliner_client.py`
- `src/kindly_web_search_mcp_server/entity/default_schema.py`
- `src/kindly_web_search_mcp_server/entity/chunk.py`
- `src/kindly_web_search_mcp_server/entity/postprocess.py`
- `src/kindly_web_search_mcp_server/entity/overlap.py`
- `src/kindly_web_search_mcp_server/entity/models.py`
- `src/kindly_web_search_mcp_server/cache/result_memory.py`
- `tests/test_entity_extraction.py`
- `tests/test_result_memory.py`
- `tests/test_entity_overlap.py`

## Files to Modify

- `src/kindly_web_search_mcp_server/cache/query_cache.py` — rewrite internals (LRU)
- `src/kindly_web_search_mcp_server/cache/page_cache.py` — rewrite internals (DuckDB)
- `src/kindly_web_search_mcp_server/cache/__init__.py` — update exports
- `src/kindly_web_search_mcp_server/cache/content_type.py` — add entity-aware classification
- `src/kindly_web_search_mcp_server/search/query_policy.py` — GLiNER augmentation
- `src/kindly_web_search_mcp_server/search/orchestrator.py` — candidate injection, entity extraction
- `src/kindly_web_search_mcp_server/rerank/core.py` — entity overlap feature
- `src/kindly_web_search_mcp_server/search/query_rewrite.py` — historical context
- `src/kindly_web_search_mcp_server/models.py` — add entity fields to WebSearchResult, GetContentResponse
- `src/kindly_web_search_mcp_server/server.py` — result memory integration, entity extraction wiring
- `src/kindly_web_search_mcp_server/settings.py` — new env vars
- `src/kindly_web_search_mcp_server/telemetry.py` — new metrics
- `src/kindly_web_search_mcp_server/analytics/duckdb_store.py` — entity event tables
- `pyproject.toml` — add qdrant-client, gliner2 (optional); remove lancedb, pyarrow

## Files to Delete

- `src/kindly_web_search_mcp_server/cache/store.py` (LanceDB semantic cache store)
- `src/kindly_web_search_mcp_server/cache/semantic_cache.py` (replaced by result_memory.py)
- `src/kindly_web_search_mcp_server/cache/schema.py` (LanceDB schema definitions)

## Dependencies to Add

- `qdrant-client>=1.9.0` (core dependency — replaces lancedb)
- `gliner2` (optional dependency in `[entity-extraction]` extra; `pip install kindly-web-search-mcp-server[entity-extraction]`)

## Dependencies to Remove

- `lancedb` (core dependency)
- `pyarrow` (transitive of lancedb, used directly only for LanceDB schema creation)