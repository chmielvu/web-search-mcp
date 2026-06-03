# GLiNER2 Integration Plan for Kindly Web Search MCP Server (Direct, Deep Integration)

**Goal**: Investigate spacy-gliner + other real-world implementations, ideate the *best natural fit* for GLiNER2 inside this specific web-search FastMCP codebase, and produce a clean, non-corporate plan for direct integration (no artificial "MVP phases", no heavy opt-in flags — make the powerful extraction a core, always-on capability that improves search + fetch for the coding-assistant use case, with simple env knobs for model/config).

The original plans/ gliner/ docs were good starting points but written in corporate style (phased MVP, "opt-in explicit", shadow first). User request: drop that. Think like a fastMCP + GLiNER expert who wants the MCP to *just be better* at understanding and extracting structure from web results.

## Research Performed (Further Investigation)

**1. Read the local plans first (as instructed)**: Fully read `plans/gliner/GLiNER_Research_Report.md` and `content-extraction-entity-schema-oss-patterns-2026-06-01.md`. Noted the 9 use cases, recommended shapes, and emphasis on query_policy + orchestrator + fetch_pipeline as seams.

**2. Deep external research on GLiNER/GLiNER2 + implementations (new in this iteration)**:
- Official GLiNER2: github.com/fastino-ai/GLiNER2 (unified schema-driven: extract_entities + extract_json + relations + classify + create_schema builder; descriptions for precision; native spans+confidence; chunking left to caller; 2048 token context; from_api for remote no-torch; regex validators; LoRA; batch). Models fastino/gliner2-base-v1 (205M), large-v1 (340M+). Apache 2.0. Paper arXiv:2507.18546. HF spaces and demos heavy on financial/legal/medical structured extraction.
- Original GLiNER (urchade): still active, but GLiNER2 is the evolution from the same lead author at Fastino.
- **spacy + gliner (key target)**: https://github.com/theirstory/gliner-spacy (and listed in urchade/GLiNER docs + spacy Universe).
  - Real code: `gliner_spacy/pipeline.py` — Language.factory("gliner_spacy"), __init__ loads GLiNER.from_pretrained with map_location/onnx, config for chunk_size (default 250!), labels list, style ("ent" or "span"), threshold.
  - Chunking logic: simple char chunks + word-boundary extension, then offset accumulation (`'start': offset + entity['start']`), predict_entities per chunk (flat_ner toggled by style), then doc.char_span + custom extension `span._.score`.
  - Also GlinerCat for sentence-level classification on top of entities (requires sentencizer).
  - ONNX support (load_onnx_model + onnx_model_file).
  - Usage: nlp = spacy.blank("en"); nlp.add_pipe("gliner_spacy", config={...}); then doc.ents or doc.spans["sc"] with .label_ + ._.score.
  - Issues in wild: Python version pins (3.7-3.10 noted), perf complaints on CPU for high-volume (chunk overhead), import/registration gotchas.
- **Other real implementations** (cross-validated):
  - Presidio: has gliner_recognizer example (external model plugin pattern for PII).
  - LangChain: GLiNERLinkExtractor (for graph edges via shared entities) + experimental GlinerGraphTransformer (GLiNER + GLiREL for nodes/rels). Pre-GLiNER2 mostly.
  - Production Rust/ONNX: gantz-ai/pii.engineer (full PII detection system using fine-tuned GLiNER2 multi + ONNX INT8; 8-stage post-processing pipeline: reclassify → validate → filter → normalize → ... → threshold → dedup → merge; language detection; redaction output; multilingual 50+ langs; very high F1; single binary; benchmarks vs Presidio/spacy; chunking + merge logic; designed for high-volume privacy scanning/LLM guardrails).
  - Fast inference: talmago/fast_gliner (Rust Python bindings for ~4x CPU speedup over torch GLiNER/GLiNER2), fbilhaut/gline-rs (pure Rust inference engine for GLiNER models, ONNX/tokenizers), elcuervo/gliner (Ruby ONNX wrapper for GLiNER2).
  - Document AI: MantisAI/sieves (pluggable zero-shot doc AI, lists GLiNER2 as backend option alongside transformers).
  - Deployment: Vast.ai has full GLiNER2 server Docker examples (API + batch); gliner2 official has serving notes.
  - **gliner2-mcp**: Exists on PyPI (0.1.0) as "MCP server exposing GLiNER2 entity extraction, classification, and JSON parsing as tools". (Standalone pure-extraction MCP; source not deeply surfaced in searches but confirms the pattern of exposing GLiNER2 *as MCP tools* is already happening in the ecosystem. Perfect sibling for this web-search-mcp.)
  - Haystack/Superlinked mentions in original plans (tagging docs with deterministic metadata before vectorization).
  - Community: heavy use in legal/finance/medical extraction demos; people love replacing LLM calls for grounded structured output; complaints mostly around label wording, long-text chunking, and CPU scaling for thousands of docs.

**3. Codebase re-analysis (this dir)**: Used list_dir + grep + read_file + serena symbols on src/kindly_web_search_mcp_server (content/fetch_pipeline.py + artifact.py + windowing.py + batch_orchestrator.py + summary.py + options.py; search/{query_policy.py, orchestrator.py, merge.py, normalize.py}; server.py (the 2673-line real one + tiny root shim); models.py; settings.py; telemetry/analytics; rerank/core.py; cache/{page_cache,query_cache,semantic_cache}; scrape/; utils/public_output.py; pyproject.toml; docs/ARCHITECTURE.md; tests hitting content/server).
  - Confirmed: no existing NER/entity code (summary.py does *LLM* "important_entities" + verbatim_terms as precedent for grounded post-processing).
  - query_policy.py is *pure regex* literal protection — ideal for GLiNER augmentation on short queries.
  - fetch_pipeline is the *staged* place after special (gh/se/wiki/arxiv) + trafilatura/universal_html → markdown ready → metadata/links/status/window/summary.
  - get_content + batch_get_content already support summary_mode + focus_query + windowing + include_metadata/links as "opt-ish" enrichments, but many are on by default or simple bools.
  - WebSearchResult is intentionally lightweight but *already* gets post-merge enrichment (providers, score, domain, etc.).
  - Caches and rerank already have "guardrail" and "feature" extension points.
  - FastMCP patterns: @mcp.tool with ToolAnnotations, async + ctx, Pydantic responses, diagnostics, public output filtering.
  - Domain of this MCP (AI coding assistants): perfect for tech-heavy entity sets (package names, versions, error codes, API names, repos, issues, models/HF ids, CLI flags, stack trace fragments) + structures (release notes, issue threads, API refs, changelogs).

**4. Ideation / thinking (non-corporate)**:
  - The *best* integration is not "add a feature behind a flag". It is making the MCP *natively understand entities and structure* in everything it touches, so search results and fetched content become dramatically more useful to the agents using it.
  - spacy-gliner teaches "make it a pipeline stage with chunking + offset fix + confidence attachment + style". We don't want/need full spaCy (bloat, tokenization mismatch with our markdown), but we can steal the chunk+offset+conf pattern exactly.
  - Rust/ONNX examples (pii.engineer, fast_gliner) show that for scale you want post-processing stages (validate/dedup/merge/normalize) + accel options. Design our extraction layer with a "raw spans → cleaned entities" pipeline so we can later swap impl or add stages without rewriting callers.
  - gliner2-mcp existing means we could compose (call out to a gliner2 extraction server), but *embedding* inside this server is better: one connection, one cache layer, entities can directly influence the *search* and *fetch* behavior of *this* MCP (query protection, result ranking, cache freshness).
  - No opt-ins: run sensible defaults *always*. For content: always extract a rich default "tech_web" entity set + try common structures on the returned window. For queries: always augment the must_keep logic. For results: always attach lightweight entities from title+snippet. Internal uses (rerank features, cache guard) are "free" quality wins.
  - Config knobs (not flags): KINDLY_GLNER_MODEL=..., KINDLY_GLNER_LABELS=... or a json file for custom default schema, threshold, chunk_size. Power users override per-call if we expose params later, but defaults are strong and on.
  - Domain power: This MCP is used for code/debug/research. Pre-bake excellent defaults for exactly the literals that break regex policy today (package@version, HF model ids, "TypeError: foo", "owner/repo#123", etc.). GLiNER2 with good descriptions will catch way more than the current _PRECISION_PATTERNS.
  - Chunking for long content: pages can be 20k+ chars; GLiNER2 ~2k tokens → chunk at ~800-1200 chars with overlap + boundary (steal from gliner-spacy + our windowing.py paragraph/sentence logic). Correct offsets so spans are valid into the page_content the user receives.
  - Output shape: extend the existing GetContentResponse / WebSearchResult with `entities: list[Entity]` and `structured: dict` (or list of extracted records). Keep backward (new fields). Include per-extraction diagnostics (model, chunks, time) in the response or observability only.
  - Synergies: entities from query + entities from results → overlap score for rerank (explainable boost for "this result actually mentions the same package/version as my query"). Entities in fetched content → better page_cache? or semantic cache keying.
  - Future accel without rewrite: the extraction client can be "python-gliner2" or "onnx" or "remote-gliner2-mcp" or "rust-fast" behind an interface.
  - Risks (real talk): model RAM (~0.5-1GB), first-load time, CPU ms per 1k chars on big batches. Mitigate with lazy singleton (already pattern in rerank/embed), small default model (base), optional ONNX later, and note that fetch is already the expensive part (nodriver etc.).
  - Why this MCP specifically wins with GLiNER2: search discovers noisy web, fetch gives clean(ish) markdown from hard sources, GLiNER2 turns that into *typed grounded data* without another LLM hop. Agents get "here are the exact package/version/error spans + a structured changelog" instead of "here is some markdown, go parse it yourself".

**5. Subproblem decomposition (for execution)**:
- Sub1: Research + validate spacy-gliner chunk/offset/conf logic + other impl patterns (done).
- Sub2: Design default entity labels + structure schemas tuned for web/tech/coding content (packages, errors, releases, issues, specs, papers...).
- Sub3: Build lightweight chunk+offset+merge util (inspired by gliner-spacy + windowing.py).
- Sub4: Extraction client/abstraction + default "tech" run (flat entities + 1-2 structures).
- Sub5: Hook into content pipeline (always after markdown ready, on the window text).
- Sub6: Hook into query_policy (always augment must_keep).
- Sub7: Hook into search result path (annotate WebSearchResult from title/snippet).
- Sub8: Wire internal uses (rerank feature, cache guardrail, telemetry/analytics dimensions).
- Sub9: Update models, server tool responses/docs, settings, observability.
- Sub10: Post-processing stages (validate with regex per label, dedup, merge overlaps, normalize) — steal from pii.engineer idea.
- Sub11: Tests (golden small texts, offset correctness, integration with fetch/batch), perf notes, changelog, docs.
- Sub12: Ideate + decide on direct (always) vs any remaining controls.

**Research performed (step-by-step, tool-driven)**:
1. Listed + fully read both MD files in plans/gliner.
2. Web searches + web_fetch + open_page on official sources: github.com/fastino-ai/GLiNER2, github.com/urchade/GLiNER, HF model cards (fastino/gliner2-base-v1, large-v1), arXiv paper 2507.18546, tutorials (ner.md, json_extraction.md, relation, validators, training, adapters), HN/Reddit discussions, Vast.ai example, LangChain refs.
3. Used huggingface MCP (hub_repo_details) for model metadata, tags, READMEs, languages (base=en; large=en/fr/es), params (205M/340M+), license (Apache-2.0), demos.
4. Additional searches: performance/latency, chunking/long-text, integrations (LangChain, presidio, spacy), MCP/FastMCP mentions (none direct), limitations.
5. Codebase deep-dive (list_dir + serena list_dir/symbols + read_file + grep on src/kindly_web_search_mcp_server, tests, pyproject.toml, docs/ARCHITECTURE.md, server.py, search/*, content/*, cache/*, rerank/*, models.py, settings.py, scrape/*, utils/*, telemetry/analytics).
6. Cross-checked against AGENTS.md/CLAUDE.md (changelog required, explicit contracts, test patching under kindly_ namespace, opt-in patterns like summary, no hidden inference).
7. Critical analysis of tradeoffs (see below).

**Key corrected/refined facts from research**:
- **GLiNER (urchade)**: Zero-shot span-based NER (predict_entities). v2.1 models Apache-2.0 (fixed bugs). Still maintained; some joint ER. Context ~512 tokens historically; configurable.
- **GLiNER2 (fastino-ai, lead author same Urchade)**: *Unified single 205M/340M encoder model* for NER + text cls (single/multi-label) + structured JSON (extract_json + schema builder with ::type::desc, choices/enums, list vs str) + relations (extract_relations). Native include_confidence, include_spans (char offsets). create_schema() for multi-task composition in 1 pass. RegexValidator post-filters. Batch APIs. Training + LoRA adapters (~MB). from_api() for XL 1B (PIONEER_API_KEY, no torch). pip gliner2 (schema/API only) vs gliner2[local] (torch+transformers). Context up to 2048 tokens per paper. CPU-first: ~130-208ms latency for cls (constant w/ #labels; ~2.6x faster than GPT-4o on CPU). Strong zero-shot F1 competitive with GPT-4o on CrossNER etc. (paper tables). Apache-2.0. HF tags: Named Entity Recognition, Structured extraction, Json extraction, Relation Extraction.
- **LangChain**: GLiNERLinkExtractor / GlinerGraphTransformer exist (uses original GLiNER + GLiREL in experimental graph). Not yet updated for GLiNER2 (as of research).
- **Other impls**: presidio has gliner_recognizer.py example; spacy-gliner popular wrapper (but CPU perf complaints in issues for high volume); community ruby ONNX wrapper; many HF Spaces (financial-search-engine, metadata taggers, legal/medical demos); Pioneer.ai for prompt-based fine-tune/deploy.
- **Limitations (community + paper + issues)**: Label/desc wording sensitive for precision; boundary "bleed" possible (use validators/regex); for >2k token docs: chunk+merge+offset-correction required (no built-in "late chunking"); CPU throughput can be bottleneck for 1000s of chunks/sec without quantization/compile/FlashDeberta (GPU opt); import may pull HF; some early dep-on-gliner1 friction (mostly resolved).
- **Benchmarks validated**: Paper Table 4 (CPU cls latency independent of label count); competitive NER F1 (0.59 avg vs GPT-4o 0.599); real-user reports praise for replacing LLM extract in pipelines (cost/privacy/speed).
- **MCP fit confirmed**: Matches "coding assistant" use (package names, versions, error classes, API ids, GitHub entities, release notes, specs, stack traces). Existing "important_entities" in summary.py is precedent for opt-in grounded extraction.

**Critical tradeoffs / why GLiNER2 over pure LLM extract or regex-only**:
- Deterministic, span-grounded, low-latency, local/privacy (no data exfil), cheap (no per-token), consistent schema output.
- Complements (not replaces) LLM summary/gemini/perplexity: use GLiNER2 for fast typed spans/structures on fetched windows; LLM for synthesis.
- Cost: one-time model load (~GB RAM for large) vs repeated LLM calls. Remote API path (Pioneer) keeps server deps light.
- Risk: new optional dep surface, chunking logic complexity, potential cold-start. Mitigated by lazy singleton, remote-first, feature flag, shadow mode + analytics before behavioral changes (rerank/cache).

## Recommended Approach (not alternatives)

**Phased, opt-in, explicit, observable, minimal-core-impact** (aligns with repo's "search discovers, fetch resolves, extraction annotates" + explicit tool contracts + diagnostics everywhere).

**Phase 1 (MVP, highest value, lowest risk)**: Remote-first GLiNER2 client + opt-in extraction on content tools only.
- Add to settings.py: `entity_extraction_enabled: bool = os.environ.get("KINDLY_ENTITY_EXTRACTION_ENABLED", "false").lower() == "true"`, `gliner_model: str = os.environ.get("KINDLY_GLNER_MODEL", "fastino/gliner2-base-v1")`, `gliner_api_key: str = os.environ.get("KINDLY_GLNER_API_KEY") or os.environ.get("PIONEER_API_KEY", "")`, `gliner_threshold: float = float(os.environ.get("KINDLY_GLNER_THRESHOLD", "0.5"))`, `gliner_max_chunks: int = int(...)`, etc. + validation + docs in CONFIGURATION.md.
- New module: `src/kindly_web_search_mcp_server/entity/gliner_client.py`:
  - `class ExtractionClient(ABC): async def extract(self, text: str, request: ExtractionRequest) -> ExtractionResult: ...`
  - `class GLiNER2RemoteClient(ExtractionClient):` — uses `from gliner2 import GLiNER2; extractor = GLiNER2.from_api()` (or direct if key) for zero-torch path; or thin httpx to Pioneer/HF if needed. Fallback to local if gliner2[local] present + env.
  - Chunk helper: `def chunk_for_extraction(text: str, max_tokens: int = 1024, overlap: int = 256) -> list[tuple[int, str]]` (char-based first, token via AutoTokenizer if local; reuse logic from windowing.py _find_boundary).
  - Merge: dedup by (label, normalized_text) taking max confidence; offset correction `global_start = chunk_start + local_start`.
  - Support both flat (extract_entities with labels or {label: desc}) and structured (extract_json or schema.structure()).
- Models (add to `models.py` or `entity/models.py`, import in server/content):
  ```python
  class EntitySpan(BaseModel):
      text: str
      label: str
      start: int | None = None  # char offset in the *returned window*
      end: int | None = None
      confidence: float | None = None
  class ExtractionRequest(BaseModel):  # or use plain dict for tool simplicity
      engine: Literal["gliner2", "none"] = "gliner2"
      model: str | None = None
      labels: dict[str, str] | list[str] | None = None  # name -> desc or just names
      structures: dict[str, list[str]] | None = None  # "release_item": ["version::str::...", "change_type::[added|fixed]::str::..."]
      threshold: float = 0.5
      include_spans: bool = True
      include_confidence: bool = True
      scope: Literal["returned_window", "full_artifact"] = "returned_window"
      max_chunks: int | None = None
  class ExtractionDiagnostics(BaseModel):
      model: str
      engine: str
      chunk_count: int
      truncated: bool = False
      latency_ms: float | None = None
      warnings: list[str] = Field(default_factory=list)
  ```
- Wire points (exact):
  - `content/options.py`: extend FetchOptions with `extraction: ExtractionRequest | None = None`; update cache_fingerprint, to_dict, build_...
  - `content/fetch_pipeline.py`: in `fetch_content_artifact` (or post-return processing after markdown is ready and before/after status/window), if options.extraction: result = await gliner_client.extract( full_or_windowed_md , request); attach to artifact (new fields or side dict). For scope=returned_window, slice first then extract (or extract full + filter spans in window).
  - `content/batch_orchestrator.py`: pass extraction per batch item; aggregate.
  - `server.py:get_content(...)`: add `extraction: dict | None = None` param (after strip_selectors). Build FetchOptions(..., extraction=ExtractionRequest(**extraction) if extraction else None). After pipeline, `response["entities"] = ...; response["structured_data"] = ...; response["extraction_diagnostics"] = ...`
  - Similarly for `batch_get_content` (uniform extraction spec for batch is simplest; per-URL later).
  - `server.py` response shaping + `_normalize...` + observability emit.
- Return in GetContentResponse/BatchContentResult (add fields with | None, descriptions).
- Update @mcp.tool docstrings (detailed "When to use", Args, Returns) and feature status.
- Telemetry: `emit_observability_event(..., "content.extraction", {"entities_count": len(...), "structured_keys": list(...), ...})`; record in analytics.
- Tests: patch("kindly_web_search_mcp_server.entity.gliner_client.GLiNER2RemoteClient", ...); use small deterministic texts for golden extraction.
- No impact on web_search / search path in Phase 1.

**Phase 2**: Enrich query side (highest non-content value per plan).
- In `search/query_policy.py`: optional hybrid in `_extract_must_keep_terms` / classify. If enabled, run lightweight GLiNER on normalized query for technical literals (labels: ["package_name", "model_id", "api_function", "error_class", "version_literal", "repo", "pr_number", ...] with strong descs). Merge/prioritize with regex terms. Feed to must_keep_terms + policy reason.
- Provider steering signal: simple post-extract rules or small resolver (e.g. "github" entity + "issue" -> boost github_graphql; "arxiv" -> academic). In orchestrator or provider_config. Shadow first.
- Result annotation (optional/diag): after merge, attach `entities` to WebSearchResult (from title+snippet only; cheap). Use for debug + future rerank features. Controlled by KINDLY_SEARCH_RESULT_ENTITIES or diagnostics flag.

**Phase 3**: Rerank + cache guardrails + analytics.
- Rerank features: new `entity_overlap` or `literal_match` score in `rerank/core.py` (jaccard on entity sets by type, exact version/repo matches). Combine with dense + recency. Explainable (emit which entities drove boost).
- Cache: in query_cache lookup and semantic_cache, compare query_entities sets; demote or bypass on strong mismatch (e.g. different "FastMCP" version). Page cache unaffected (post-process).
- Expand DuckDB views/queries + Grafana for entity yield by provider, rewrite success with locked entities, extraction coverage by source_type (github_issue vs general html), etc.
- Optional PII labels (or dedicated small model) for safety redaction in previews/logs.

**Cross-cutting**:
- Chunking/offset util: new or extend `content/windowing.py` + `entity/chunk.py` (stateless, testable; support char or token via simple splitter or HF tokenizer if local).
- Config: all in settings.py following KINDLY_* pattern; validate on startup if enabled.
- Deps: gliner2 in optional "entity-extraction" extra (or document user `pip install gliner2[local]` for local mode). Remote path (from_api) requires zero extra beyond current (uses gliner2 base or thin client).
- Loading: singleton lazy extractor (thread-safe), cpu pinning/quantize/compile/FlashDeberta support via env (like existing).
- Diagnostics everywhere (KINDLY_DIAGNOSTICS): per-chunk, truncation, threshold used, model version, validator rejections.
- Explicitness: never auto-enable; always surface in response + observability. Update tool descriptions per AGENTS (e.g. "extraction: opt-in GLiNER2 entity+schema extraction on the returned window").
- Rollout: shadow mode (compute but don't return, log metrics) -> opt-in return -> use in rerank/cache (behind flags).
- Future: fine-tune domain adapters (legal/release-notes) via Pioneer/LoRA; dedicated entity cache layer; MCP resource for "supported entity schemas".

**Why this shape over alternatives**:
- Remote-first + content-only first: matches "implementation judgment" in plan + minimizes risk to search hot path + keeps core deps light.
- Opt-in dict in get_content (or FetchOptions) vs new top-level extract_entities tool: reuses existing bounded fetch+window contract; "search discovers, fetch+extract resolves".
- Hybrid regex+GLiNER in policy: enriches without replacing proven literal protection.
- Spans+conf+diagnostics mandatory: per google/langextract + presidio + urchade issues (traceability, debug).
- No change to WebSearchResult default shape: preserves "lightweight results only" contract.

## Critical Files to Modify / Add (current layout)

**New (minimal)**:
- `src/kindly_web_search_mcp_server/entity/__init__.py`
- `src/kindly_web_search_mcp_server/entity/gliner_client.py` (client + chunker + merge logic)
- `src/kindly_web_search_mcp_server/entity/models.py` (pydantic Extraction* or reuse/extend)
- `tests/test_gliner_client.py` (or test_entity_extraction.py)
- `tests/test_content_extraction_gliner.py` (integration with fetch, mocks)
- Possibly `src/kindly_web_search_mcp_server/entity/chunk.py` if windowing insufficient.

**Modify**:
- `src/kindly_web_search_mcp_server/settings.py`: add gliner_* fields, enabled, model, threshold, api_key, etc. + docs.
- `src/kindly_web_search_mcp_server/models.py`: extend GetContentResponse, BatchContentResult (add entities?, structured_data?, extraction_diagnostics?); optionally WebSearchResult (entities?); new request models if used in server sigs. Add Extraction* if complex.
- `src/kindly_web_search_mcp_server/server.py`: update get_content + batch_get_content signatures + docstrings + impl (pass extraction opts to fetch, attach results); update _normalize... if needed; register any new prompts/resources if any; feature status.
- `src/kindly_web_search_mcp_server/content/fetch_pipeline.py`: accept extraction request, post-process artifact/page_content after windowing (or before for full scope), call client, attach to artifact or return sidecar. Update fetch_content_artifact sig.
- `src/kindly_web_search_mcp_server/content/batch_orchestrator.py`: propagate extraction per-item; respect per-url budgets.
- `src/kindly_web_search_mcp_server/content/options.py`: extend FetchOptions (or new ExtractionOptions) for extraction config; fingerprint update.
- `src/kindly_web_search_mcp_server/content/windowing.py`: ensure or add offset-aware chunk helper usable by extraction.
- `src/kindly_web_search_mcp_server/search/query_policy.py`: optional gliner enrichment of must_keep_terms (behind flag); update classify_search_query, _extract... ; import lazy.
- `src/kindly_web_search_mcp_server/search/orchestrator.py`: (phase2) call policy enrichment; (phase3) attach result entities post-merge; pass signals to rerank.
- `src/kindly_web_search_mcp_server/rerank/core.py`: (phase3) entity_overlap_feature, integrate into scoring; update telemetry.
- `src/kindly_web_search_mcp_server/cache/query_cache.py` and/or `semantic_cache.py`: (phase3) entity mismatch checks on lookup/store.
- `src/kindly_web_search_mcp_server/telemetry.py` + `analytics/*`: new metrics/events, views for entities (types per query/provider, extraction stats, etc.).
- `src/kindly_web_search_mcp_server/utils/public_output.py`: whitelist new fields if filtered.
- `pyproject.toml`: optional dep group e.g. "entity-extraction = ["gliner2"]" or note in docs.
- `CHANGELOG.md`: under [Unreleased] → Added (GLiNER2 opt-in extraction...); follow Keep a Changelog.
- `docs/CONFIGURATION.md`, `docs/ARCHITECTURE.md`, `docs/DEVELOPMENT.md`, `docs/TESTING.md`: updates.
- `tests/`: update existing that hit get_content/batch (e.g. test_batch_orchestrator, test_page_content_resolver, test_content_windowing, test_server) to cover new paths or assert no breakage; add mocks for gliner (patch("kindly_web_search_mcp_server.entity.gliner_client...")).
- `server.py` (root shim if any) and cli if entry affects.

**Reuse heavily**:
- Existing optional/enabled pattern + lazy load (see rerank/core.py _rerank_results, embeddings, query_rewrite).
- Fetch pipeline staging + artifact (content/artifact.py, fetch_pipeline.py).
- Windowing + char budgets (content/windowing.py, batch_orchestrator.py).
- Diagnostics emitter (everywhere).
- Pydantic response models + Field descriptions.
- QueryPolicy / RewritePolicy as extension point.
- Summary.py LLM entity extraction as precedent + possible fallback/engine selector ("llm" | "gliner2" | "none").
- Analytics DuckDB schema evolution (add tables/columns for entities).
- Test patching convention.
- Special content resolvers pattern for adding "extraction stage".

**Do not touch (or minimal)**: core search providers, merge RRF (unless entity features later), scrape/universal (extraction *after* clean markdown), existing caches on first pass.

**FastMCP / async specifics** (from server.py analysis):
- Decorators: `@mcp.tool(annotations=ToolAnnotations(title=..., readOnlyHint=True, idempotentHint=True, openWorldHint=True))`.
- Signatures include `ctx: Context = CurrentContext()`.
- For GLiNER CPU work inside async get_content/batch: wrap with `await asyncio.to_thread(...)` inside the client impl (never block event loop).
- Use existing `await ctx.report_progress(...)`, `await ctx.info(...)`, timeout wrappers, `_timeout_markdown_note`.
- Keep web_search contract unchanged (lightweight only).
- Add extraction fields to responses with defaults that preserve backward compat for clients (None or omitted via exclude_none).
- Update root shim? No — it is 9-line redirect; primary is src/.../server.py (2673 lines).

## Recommended Direct Integration Shape (Ideated from spacy-gliner + Rust PII + gliner2-mcp + local code seams)

Make GLiNER2 a **core, always-active extraction layer** (direct, no "opt-in" theater, no staged MVP). The MCP simply gets smarter at entities and structure because that's what its users (coding agents) need.

- **Queries (query_policy.py)**: *Always* run a fast GLiNER pass inside `_extract_must_keep_terms` / classify_search_query on the short normalized query. Use a strong default "tech_literal" label set with natural language descriptions (package, model/hf_id, api/function, error/exception, version, repo, pr/issue, cli_flag, env, stack_fragment, ...). Union results with the existing regex terms. This makes rewrite protection and "bypass" decisions *much* better for real coding/debug queries without any user action. Update the reason string to mention detected entities.

- **Search results (orchestrator.py + normalize)**: After RRF merge (before or during rerank slice), *always* run lightweight GLiNER on title + snippet for every candidate. Attach `entities: list[dict with text, label, confidence?]` directly to the WebSearchResult objects. Snippets are tiny → negligible cost. Agents now get "this link talks about gliner2-base and a specific error" instead of blind text. These entities also become free features for rerank (see below).

- **Fetched content (the real power — fetch_pipeline.py + windowing)**: *Always*, after the clean markdown is produced (post special resolvers or http_extract/universal_html, on the text that will be windowed into page_content), run GLiNER2.
  - Chunking: port the practical idea from theirstory/gliner-spacy (char chunk_size ~800-1200, extend to word/paragraph boundary using our existing _find_boundary logic in windowing.py, accumulate global offset).
  - Run: gliner2 extract_entities with rich default tech + general labels (with descriptions for precision) + include_spans + include_confidence. Also attempt 1-2 default structures via extract_json / schema (e.g. release-style changes, error details, api sigs) — the model is designed for this.
  - Merge + post-process: correct offsets, dedup (by label+text or span), take max conf, simple validate/normalize (inspired by the 8-stage pipeline in gantz-ai/pii.engineer and GLiNER2's own RegexValidator).
  - Attach to the internal ContentArtifact (new entities + structured_data fields) so it flows to GetContentResponse and BatchContentResult *always*.
  - Spans are relative to the returned page_content window (exactly what the caller receives).
  - For already-structured sources (github_issue via GraphQL, stackexchange, arxiv): still run for supplementary surface entities + any missed fields; the API structure remains primary.

- **Internal loops (rerank + cache)**: 
  - In rerank/core.py add cheap entity overlap / exact literal match features (query entities vs result entities, version/package exact hits). This is explainable boost on top of embeddings + voyage/jina.
  - In query_cache and semantic_cache: when deciding reuse or scoring, factor entity set overlap or mismatch (the classic "FastMCP 2.x vs 3.x" semantic false-positive killer).

- **Config (knobs, not gates)**:
  - KINDLY_GLNER_MODEL (fastino/gliner2-base-v1 or the multi variant)
  - KINDLY_GLNER_THRESHOLD, KINDLY_GLNER_CHUNK_SIZE
  - KINDLY_GLNER_DEFAULT_SCHEMA (path to json or inline for the label descs + structures we always use)
  - Device / onnx / remote (PIONEER_API_KEY path via from_api for zero heavy deps)
  - The extraction *is on*. These just tune *how*.

- **Models / responses**: Add small Entity / Structured models in models.py. Extend WebSearchResult (entities), GetContentResponse and Batch (entities + structured_data + light extraction_info). New fields are additive; old clients keep working. Update the tool docstrings in server.py to describe the new grounded richness as standard ("the page_content comes with extracted entities and structured records grounded to it").

- **Observability**: Every path emits entity-related events/dimensions. Analytics DuckDB gets first-class entity_type counts, top entities per query, extraction stats by source_type (html vs github_issue etc.). This turns the work into measurable improvement data.

- **Primitives (build once, reuse)**:
  - entity/ package: client (lazy gliner2 load, remote or local), chunker (offset-correcting), default_schemas, postprocess (dedup/validate/merge/normalize stages).
  - Keep it small and swappable (future ONNX/Rust path from the other impls we researched).

This shape was ideated from:
- spacy-gliner's practical chunk+offset+score attachment + pipeline factory thinking (we do the chunk/offset/score part without spaCy).
- pii.engineer and similar Rust prod systems (post-processing stages are where the magic for clean output happens; design for it).
- gliner2-mcp existence (proves exposing GLiNER2 via MCP is a thing; we embed it inside the *search* MCP so entities improve discovery and fetch).
- Local seams (query_policy regex is crying out for entity help; fetch_pipeline is the perfect post-markdown hook; summary.py already proves we do grounded transforms on content; orchestrator already enriches results).

Result: the web-search-mcp doesn't "have GLiNER2 added". It *understands entities and structure* as a first-class part of searching and fetching. Agents get better results and richer data with zero extra work.

**Why better than alternatives**:
- Vs adding a separate extract_entities tool: deep integration means entities can *improve the search/fetch itself* (policy, rerank, cache).
- Vs full opt-in everywhere: for a specialized MCP, the defaults *are* the product. Power users still get the knobs.
- Vs pure LLM: grounded, cheap, span-precise, no hallucinated boundaries, works on the exact fetched window.
- Vs spacy-gliner: we take the good ideas (chunk+offset+score) without pulling spaCy or tokenization mismatch (we work on clean markdown).
- Vs standalone gliner2-mcp: we compose search discovery + extraction in the *same server* (better for the agent loop).

## Critical Files + Changes (Direct)

1. **Unit**:
   - `pytest tests/test_gliner_client.py -q` (mock gliner2 lib, test chunk+merge+offset, confidence/spans, validators, remote vs local paths, error cases).
   - Existing focused slice: `python -m pytest tests/test_server.py tests/test_page_content_resolver.py ... test_batch_orchestrator.py -q --tb=line` (ensure no regression when disabled).

2. **Integration**:
   - Live with KINDLY_ENTITY_EXTRACTION_ENABLED=true + small fixture URLs (github issue, release notes, arxiv abstract, plain html). Assert entities present with spans/conf in response; structured for known schema (e.g. version/package in changelog).
   - Query policy: unit + live queries with "FastMCP 0.1.8" vs generic; verify must_keep + bypass.
   - Shadow mode: enable compute, disable return, check analytics events + no change to public output.

3. **Perf / Guard**:
   - Measure added latency on get_content (target <200-400ms p95 for 5-10k char window on CPU; use chunking).
   - Cache correctness: same URL + extraction params → consistent; different labels → different (or separate keying).
   - Memory: singleton model, no leak on repeated calls (use tests/test_nodriver... style or simple loop).

4. **Observability**:
   - KINDLY_DIAGNOSTICS=1 + KINDLY_ANALYTICS_ENABLED → DuckDB has entity rows, extraction_diagnostics in events.
   - Grafana / queries updated (optional).

5. **Contract / Docs**:
   - Tool descriptions accurate (use `get_workflow_doc` or inspect).
   - `ruff check src/ && ruff format src/`
   - Focused core (per AGENTS.md): `python -m pytest tests/test_server.py tests/test_page_content_resolver.py tests/test_tool_descriptions.py tests/test_search_router.py tests/test_query_rewrite.py tests/test_search_orchestrator.py -q`
   - Single file example: `python -m pytest tests/test_searxng_unit.py -v`
   - Full relevant: `pytest tests/test_batch_orchestrator.py tests/test_content_windowing.py tests/test_content_resolver_universal_fallback.py -q`
   - Manual: `python -m uvicorn ...` or `uvx --from . kindly-web-search-mcp-server start-mcp-server --http --port 8000`; call tools with extraction; or use scripts/script_run_mcp_tools.py.
   - Lint/format before any commit.

6. **End-to-end with real MCP client** (after impl):
   - Enable via env, call get_content on a release-notes or GitHub issue URL with extraction={"labels": {"package": "...", "version": "..."}}, assert spans/conf in output + diagnostics.
   - Query with literal like "gliner2-base-v1 error E123": confirm must_keep includes it, bypass mode.
   - Check analytics tables for new entity events.

6. **Edge**:
   - Empty text, very long (>50k), multilingual (use multi model), low confidence, nested/overlaps (GLiNER2 supports), validator filtering, truncation.
   - Disabled by default: zero behavior change, zero dep load.
   - Remote API fallback / circuit (like HF embeddings circuit breaker).

7. **Release**:
   - Update CHANGELOG with PR ref.
   - Optional: add example in docs or scripts/ using extraction for "release notes parser".
   - If local: document `pip install kindly-web-search-mcp-server[entity-extraction]` or user-side gliner2[local].

**Risks & Mitigations** (in plan):
- Latency on content path: chunk aggressively + remote or quantized local; measure in CI.
- Model download / cold start: lazy, cache in HF_HOME, document.
- Label engineering: ship good defaults for coding/web (package, version, error, api, person, org, date, product, ...); allow caller override with descs.
- Breaking contracts: never add required params; new fields default null/omitted.
- Dep bloat: remote path + optional extra; gliner2 base has no torch.

## Open Questions for User (if any post-approval)
- Prefer "extraction" param as top-level in get_content vs nested under new options?
- Default model base or large? (recommend base for speed; large for precision tech docs)
- Remote (Pioneer) vs attempt HF Inference endpoint first? (research showed custom extractor; API or local preferred)
- Add dedicated `extract_entities(url, labels=...)` tool, or only via get_content?
- Enable entity annotation on web_search results by default in diagnostics, or fully opt-in?

## Sources / Citations (for traceability)
- Official: fastino-ai/GLiNER2 (GitHub + tutorials), urchade/GLiNER, fastino/gliner2-*-v1 (HF + model cards), arXiv:2507.18546 + EMNLP demo pdf.
- Benchmarks/community: paper tables, Reddit r/LanguageTechnology, HN thread, LangChain refs, presidio example, spacy-gliner issues.
- Codebase: direct reads of listed files + serena symbols + grep + docs/ARCHITECTURE.md + pyproject.toml + AGENTS.md.
- Cross: web searches for integrations, chunking, perf.

This plan is executable, minimal, high-ROI, and aligned with repo values (explicit, observable, bounded, testable, changelog'd). Implement via implement skill or step-by-step with check-work.

(End of plan draft. Expand sections with more code snippets from research as needed during execution.)

## Cleanup Note on Old Text
Some older verification snippets referencing "KINDLY_ENTITY_EXTRACTION_ENABLED" and "shadow mode" may still exist in the file from earlier drafts. In the final executed plan those are removed — the integration is direct ("always" with tuning knobs only). When implementing, ensure all docs, tests, and code use the direct always-on language.

## Additional Ideation Notes (from this round of research)

- spacy-gliner's chunk_size=250 is conservative; for our already-windowed 8k-20k char markdown we can safely use larger (800-1200) to reduce chunk count while staying well under GLiNER2's 2048 token context.
- The GlinerCat pattern (entities → sentence-level themes) is interesting for future "page summary by entity category" but out of scope for first direct integration.
- pii.engineer 's 8-stage pipeline + language detection + redaction is gold for any safety/PII use (aligns with original plan use #9). We can start with a 3-4 stage version (validate, dedup, merge, normalize) and grow it.
- Rust/ONNX options (fast_gliner, gline-rs, pii.engineer) mean we should keep the entity/ layer behind a small interface so swapping the inference backend is low pain later.
- gliner2-mcp shows the ecosystem is already treating GLiNER2 extraction as an MCP tool surface. By embedding it here we give agents *search + extraction* in one place — stronger than calling two MCPs.
- For this MCP's users (coding agents), the highest leverage defaults are software entities + release/issue/product structures. Invest time in good label descriptions (the research shows this is the #1 accuracy lever).

## Subproblems for Step-by-Step Execution (Decomposed)

1. Research/validation of spacy-gliner + pii.engineer + gliner2-mcp + others (this session, done).
2. Design + implement default schemas.py (labels + descs + 2-3 structures for tech content).
3. Implement chunker.py (boundary + offset correction, tests for math).
4. Implement postprocess.py (light stages).
5. Thin gliner_client.py (lazy, remote-first or local).
6. Hook query_policy.py (always augment).
7. Hook orchestrator.py (result annotation).
8. Hook fetch_pipeline.py (content extraction on markdown).
9. Models + server.py response wiring + docstrings.
10. Rerank + cache signals.
11. Settings + telemetry + analytics.
12. Tests + ruff + AGENTS commands + changelog + docs update.

All direct, no corporate scaffolding. The MCP will simply be better at its job.
