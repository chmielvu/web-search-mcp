# ML/NLP Techniques for Web Search MCP — Research Synthesis
> Based on: Gemini-grounded web research, PyPI/GitHub source analysis, and codebase assessment

---

## 1. Current State Assessment

The MCP provides:

| Component | What It Does | Backend |
|-----------|-------------|---------|
| `web_search` | Multi-provider search with RRF merging, query rewriting | SearXNG/Tavily/Brave/Jina/DDG/Gemini + Mistral rewrite |
| `get_content` | Staged fallback: StackExchange → GitHub Issues → Discussions → Wikipedia → arXiv → trafilatura → Jina → nodriver browser | `fetch_pipeline.py` |
| `batch_get_content` | Parallel URL fetching with budget/cursor | Batch wrapper around fetch pipeline |
| `discover_links` | Sitemap/link extraction | trafilatura/BS4 |
| `gemini_search` / `perplexity_search` | AI-synthesized answers with citations | Gemini/Perplexity APIs |
| `academic_search` | 6-source academic paper search | SemanticScholar/arXiv/OpenAlex/CrossRef/PubMed/CORE |
| Semantic Cache | LanceDB embedding-based similarity cache | `cache/semantic_cache.py` |
| Page Cache | LanceDB URL→content cache, 7-day TTL | `cache/page_cache.py` |
| Reranking | 3-stage: bi-encoder filter → cross-encoder → MMR diversity | `rerank/core.py` |
| Summarization | LLM-powered brief/detailed via Chutes API | `content/summary.py` |

### What the MCP Lacks

- **No content quality assessment** — `get_content` returns whatever trafilatura extracts, with only a `status` field (success/partial/error), no quality signal
- **No structured metadata** — outputs are flat Markdown strings, no entities/keywords/readability/language data
- **No near-duplicate detection** — page cache is URL-keyed only, so mirror URLs duplicate content
- **No document-level enrichment** — no way to ask "what language is this?" or "what are the key entities?" without an extra LLM call
- **No result clustering/diversity analysis** — MMR diversity exists but only at reranking, not as a response field

---

## 2. Research Methodology

Four rounds of discovery:

1. **Open-ended Gemini queries** asking: "What niche/emerging Python libraries exist for text mining, computational linguistics, and statistical NLP in 2025?"
2. **Targeted Gemini queries** asking: "What ML techniques apply to 5-20 search results?" and "What can be extracted from a single web page without API calls?"
3. **Source validation** — fetched PyPI pages and GitHub READMEs of the most promising libraries to verify claims, check maintenance status, and assess API surface
4. **Cross-comparison** — evaluated each library against the MCP's constraints (must run offline, no GPU, <200ms for real-time use, minimal new dependencies)

---

## 3. Library Landscape — What Was Found

### 3.1 Text Quality & Readability

| Library | Maintained | Size | Key Capability |
|---------|-----------|------|---------------|
| **TextDescriptives** | ✅ Active (v2.0+, JOSS publication) | Medium (spaCy pipeline) | 60+ metrics: readability, coherence, POS stats, **Gopher/C4 duplicate detection**, lorem ipsum check, quality pass/fail |
| `textstat` | ✅ Stable | Tiny (pure Python) | ~10 readability scores only |
| `py-readability-metrics` | ✅ Stable | Small | Extended readability with Dale-Chall |
| `textcomplexity` | ❌ Last release 2022 | Small | Linguistic/stylistic complexity, language-independent |

**Winner: TextDescriptives**. It is the only library that provides both readability AND quality heuristics (Gopher/C4 content quality filters) in one call. The `passed_quality_check` field alone is worth the spaCy dependency. The coherence metric (sentence-level semantic similarity) is unique — no other library provides this.

### 3.2 Language Detection

| Library | Speed | Languages | Offline | Model Size |
|---------|-------|-----------|---------|------------|
| **fast-langdetect** | 80x faster than langdetect | **176** | ✅ (lite model) | 45MB lite / 170MB full |
| `langdetect` | Slow | 55 | ❌ | N/A |
| `lingua-py` | Medium | 75 | ✅ | No model download |
| `polyglot` | Medium | Limited | ⚠️ | CLD2 binding |

**Winner: fast-langdetect**. 176 languages, offline, ultra-fast, and the lite model (45MB) is suitable for production. The `detect(text, model="lite")` API is one line. Critical insight: the optimal input length is 80 characters — longer input reduces accuracy.

### 3.3 Near-Duplicate Detection

| Library | Technique | Scale | Memory per Doc | Query Type |
|---------|-----------|-------|----------------|------------|
| **datasketch** | MinHash + LSH | Millions | ~8-128 bytes (configurable) | "Find docs with Jaccard > 0.9" |
| `simhash-py` | SimHash (Hamming) | Millions | 64-bit integer | "Find docs within Hamming dist 3" |
| `dedupe` | Active learning | Thousands | High (trains classifiers) | "Find exact duplicate pairs" |

**Winner: datasketch**. MinHash LSH is the standard for production deduplication. The `LSHForest` index supports top-K queries (not just threshold) — ideal for "find the 3 most similar cached pages." The library is battle-tested (3,000+ GitHub stars, used in production by many projects) and has zero training requirements.

### 3.4 Keyword Extraction

| Library | Speed | Quality | Models Needed | Multilingual |
|---------|-------|---------|---------------|-------------|
| **YAKE** | ~10ms | Good | None | ✅ |
| `PKE` | 100-500ms | Better (graph-based) | None (some external corpora) | ⚠️ |
| `KeyBERT` | 500ms-2s | Best (semantic) | SentenceTransformer (~90MB) | ❌ (depends on embedding model) |
| `RAKE-NLTK` | 50ms | Fair | NLTK stopwords only | ❌ English-biased |

**Winner: YAKE**. The only library that meets the MCP's constraints: sub-100ms, no model downloads, truly multilingual, works on single documents without a corpus. Published in Information Sciences (Elsevier, 2020), ECIR'18 Best Short Paper award. Its five statistical features (casing, position, frequency, relatedness, differentiator) are well-studied.

### 3.5 Vocabulary Richness

| Library | Metrics | Maintained |
|---------|---------|-----------|
| **LexicalRichness** | TTR, RTTR, CTTR, Herdan's C, Summer's S, Dugast's U, Maas's T, MSTTR, MATTR, MTLD, HDD | ✅ |
| `lexical-diversity` | TTR, MTLD, HDD | ❌ (2020) |

**Assessment**: Niche but valid. High TTR/MTLD values indicate varied vocabulary (typically human-written). Low values indicate repetitive/templated content. A zero-cost signal for distinguishing human-written from machine-generated or boilerplate content.

### 3.6 Clustering

| Library | Technique | MCP Fit |
|---------|-----------|---------|
| `HDBSCAN` | Density-based, no preset k | ❌ |

**Rejected**: Clustering 5-20 search results is statistically meaningless. MMR diversity reranking already serves the same purpose (grouping similar items, ensuring diverse coverage) more directly and with fewer parameters.

### 3.7 Information-Theoretic Measures

| Library | Key Capability |
|---------|---------------|
| `pyitlib` | Entropy, mutual information, KL divergence for discrete variables |
| `infomeasure` | Transfer entropy for time series |
| `information-density` | Propositional idea density (CPIDR/DEPID algorithms) |

**Assessment**: Mostly rejected. Idea density is academically interesting but too slow (requires spaCy dependency parsing). Entropy metrics could quantify "information density" of web pages but the libraries are too low-level — you'd need to build the feature engineering yourself.

---

## 4. Cross-Comparison: What Actually Adds Value

### Tier 1: Implement Now (High Value, Low Cost)

**1. TextDescriptives — Content Quality Assessment**

```
nlp = spacy.load("en_core_web_sm")
nlp.add_pipe("textdescriptives/all")
doc = nlp(extracted_markdown)
```

Returns in one call:
- `passed_quality_check` (Gopher + C4 heuristics: duplicate n-grams, lorem ipsum, bullet/ellipsis ratios, symbol ratios)
- `flesch_reading_ease`, `flesch_kincaid_grade`, `smog`, `gunning_fog`, `lix`, `rix`
- `first_order_coherence`, `second_order_coherence` (sentence-level semantic similarity)
- `token_length_mean/std`, `sentence_length_mean/std`, `syllables_per_token_mean/std`
- `pos_prop_DET/NOUN/VERB/ADJ/ADP/ADV/...` (part-of-speech distributions)
- `dependency_distance_mean/std` (syntactic complexity)
- `alpha_ratio`, `n_stop_words`, `proportion_unique_tokens`

Integration: Add `quality` field to `ContentArtifact`. Run TextDescriptives on extracted Markdown. Populate quality score and metadata fields.

**2. fast-langdetect — Language Detection**

```
from fast_langdetect import detect
result = detect(text[:80], model="lite", k=1)
# [{"lang": "en", "score": 0.98}]
```

Integration: Add `detected_language` and `language_confidence` to `ContentArtifact`. Run after extraction, before quality assessment (quality heuristics are language-dependent).

**3. datasketch — Page Cache Deduplication**

```
from datasketch import MinHash, MinHashLSH

# On initial page fetch:
m = MinHash(num_perm=128)
for word in markdown.split():
    m.update(word.encode('utf8'))
lsh.insert(canonical_url, m)

# Before fetching new URL:
m2 = MinHash(num_perm=128)
for word in new_markdown.split():
    m2.update(word.encode('utf8'))
near_duplicates = lsh.query(m2)  # returns URLs with high Jaccard similarity
```

Integration: Extend `PageCache` to store MinHash signatures alongside cached content. Before fetching a new URL, check if its MinHash signature matches any existing cache entry. If Jaccard > 0.95, serve cached content with a `content_duplicate_of` field pointing to the original URL.

### Tier 2: Add As Optional Enrichment (Medium Value)

**4. YAKE — Keyword Extraction**

```
import yake
extractor = yake.KeywordExtractor(lan="en", n=3, top=10)
keywords = extractor.extract_keywords(markdown)
```

Integration: Add `extract_keywords` parameter to `get_content`. When enabled, append `keywords: [{text, score}]` to response. Run only on successful extractions (status=success).

**5. LexicalRichness — Vocabulary Diversity**

```
from lexicalrichness import LexicalRichness
lex = LexicalRichness(markdown)
# {words: N, terms: M, ttr: M/N, mtld: ..., hdd: ...}
```

Integration: Add `vocabulary` field when `enrich=True` on `get_content`. Useful signal for distinguishing human-written vs boilerplate/templated content.

### Tier 3: Rejected (Not Worth Adding)

| Library/Technique | Reason for Rejection |
|------------------|---------------------|
| `hdbscan` / clustering | Statistically invalid on <20 results; MMR diversity serves same purpose |
| `pyitlib` / information theory | Too low-level; requires custom feature engineering |
| `ideadensity` | Academic only; too slow (requires full spaCy dependency parse) |
| `textcomplexity` | Abandoned since 2022 |
| `PKE` / `KeyBERT` | 10-100x slower than YAKE; requires embedding model or external corpora |
| `textstat` alone | TextDescriptives supersedes it |
| `simhash-py` | Less flexible than datasketch (Hamming distance vs Jaccard threshold) |
| `dedupe` | Requires training data and active learning workflow |

---

## 5. Implementation Roadmap

### Phase 1: Quality Foundation (Week 1)

Add TextDescriptives + fast-langdetect to `ContentArtifact`:

1. Add `quality_score`, `detected_language`, `language_confidence`, `readability` fields to `ContentArtifact` model
2. In `fetch_pipeline.py`, after extraction: `nlp(text) → extract textdescriptives metrics → populate fields`
3. Add `detect(text[:80], model="lite")` → populate language fields
4. Run existing tests, add new coverage for quality fields

**Dependencies added**: `textdescriptives`, `fast-langdetect`, `spacy` (already optional)

### Phase 2: Deduplication (Week 2)

Add datasketch MinHash LSH to page cache:

1. Extend `PageCache` schema to include `minhash_signature` field (bytes column)
2. On cache insertion: compute MinHash signature, store alongside content
3. Before cache insertion: query LSH for near-duplicates (Jaccard > 0.85)
4. If duplicate found: return cached content with `content_duplicate_of` field
5. Expose `dedup_jaccard_threshold` env var for tuning

**Dependencies added**: `datasketch`

### Phase 3: Optional Enrichment (Week 3)

Add enrich=True parameter to get_content:

1. YAKE keyword extraction (fast, no deps beyond `yake`)
2. LexicalRichness vocabulary metrics (fast, no deps beyond `lexicalrichness`)
3. Make all of Phase 1-3 enrichment gated behind `enrich=True` to keep default responses lightweight

**Dependencies added**: `yake`, `lexicalrichness`

---

## 6. Open Questions

1. **spaCy model loading**: `en_core_web_sm` (13MB) must be downloaded separately. Should the MCP auto-download it on first use, or require users to pre-install? PostHog's pattern: lazy import inside the function, with clear error message if model not found.

2. **Multilingual quality**: TextDescriptives quality heuristics (Gopher/C4) assume English text structure. For non-English pages, should quality assessment be skipped or adapted?

3. **Cache dedup storage**: Storing MinHash signatures (128 × 4 bytes = 512 bytes per page) in LanceDB adds minimal overhead. But the LSH index must be rebuilt on restart. Should it be persisted or rebuilt from scratch?

4. **Enrich vs. performance**: TextDescriptives full pipeline adds ~200ms per page. Should enrichment be opt-in (default `enrich=False`) or opt-out (default `enrich=True`)?

---

## 7. References

### Libraries (verified via PyPI/GitHub fetches)

- **TextDescriptives**: `pip install textdescriptives` — https://github.com/HLasse/TextDescriptives — JOSS publication, spaCy integration, Gopher/C4 quality heuristics
- **fast-langdetect**: `pip install fast-langdetect` — https://github.com/llmonpy/fast-langdetect — FastText-based, 176 languages, 80x faster than langdetect, CC BY-SA 3.0 model license
- **datasketch**: `pip install datasketch` — https://github.com/ekzhu/datasketch — MinHash, LSH, LSH Forest, HNSW; Redis/Cassandra support
- **YAKE**: `pip install yake` — https://github.com/INESCTEC/yake — ECIR'18 Best Short Paper, Information Sciences 2020 publication
- **LexicalRichness**: `pip install lexicalrichness` — https://github.com/LSYS/lexicalrichness — 11 metrics including MTLD, HDD, Maas's T

### Prior Art (production OSS applications studied)

- **PostHog business_knowledge** (PostHog/posthog): Simple trafilatura extraction + paragraph-aware chunking (1200 target, 1600 hard max), no embeddings, PostgreSQL-based neighbor retrieval, full-rebuild updates
- **process-pinboard** (markvan/process-pinboard): spaCy NER pipeline, text[:5000] truncation, 8-type entity filter, REBEL relationships (batch-only), KeyBERT keywords (batch-only), co-occurrence edges

### Research methodology credit

Initial discovery via Gemini Search queries for landscape exploration, followed by PyPI/GitHub source verification of top candidates. Cross-referenced against production OSS application code (PostHog, process-pinboard) to validate real-world applicability.
