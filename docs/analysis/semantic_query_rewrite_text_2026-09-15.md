# Semantic query-rewrite text analysis — 2026-09-15

**Scope:** every input query is pulled from `search_runs.query`; every available rewrite is matched from `query_variants.query_text` by `run_key`. This analysis uses the text content only. It does not use `llm_judgments`, result labels, provider metrics, embeddings, or metadata-derived quality scores.

## Corpus pulled from the data

- Unique input query rows: **1112**.
- Inputs with at least one matched rewrite: **437**.
- Inputs without a rewrite row: **675**.
- Matched input/rewrite pairs: **2622**. Every pair has non-null input and rewrite text.
- Rewrite roles represented: `free, neural, original, original_free, paid_brave, paid_google, paid_other, semantic_exa, semantic_tavily, serp1, serp2, specialized`.

The complete paired text is stored in the Parquet artifact linked at the end of this report. The report first presents computed text distributions and exact examples; interpretation is performed separately after this data-only pass.

## What the rewrite text is doing

Text classes across all pairs: `{'identity': 1036, 'repeated_phrase_pollution': 554, 'broad_expansion': 123, 'focused_expansion': 206, 'content_contraction': 270, 'anchor_loss': 425, 'surface_rewrite': 8}`. `identity` means token-normalized text is unchanged; `anchor_loss` means at least one technical/entity anchor, URL, number, version, or constrained token from the input disappears; `repeated_phrase_pollution` means a contiguous phrase repeats inside the rewrite; `broad_expansion` means the rewrite adds more than four content terms and more than the input content-term count; `content_contraction` drops input content terms; `focused_expansion` adds content without those loss/pollution signals. These classes are text rules, not quality labels.

By rewrite role:

| variant_role | pairs | unique_inputs | identity_rate | content_recall | anchor_recall | anchor_loss_rate | mean_added_terms | median_added_terms | mean_dropped_terms | repeated_phrase_rate | frame_change_rate | operator_change_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| free | 409 | 409 | 0.28361858190709044 | 0.9511980514731125 | 0.9101951827242524 | 0.1198044009779951 | 3.229828850855746 | 3 | 0.4254278728606357 | 0.34963325183374083 | 0.03178484107579462 | 0.08557457212713937 |
| neural | 28 | 28 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| original | 409 | 409 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | 0 | 0.0 | 0.0 | 0.0 | 0.0 |
| original_free | 28 | 28 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| paid_brave | 28 | 28 | 0.03571428571428571 | 1.0 | 1.0 | 0.0 | 6.0 | 6.0 | 0.0 | 0.8571428571428571 | 0.0 | 0.0 |
| paid_google | 28 | 28 | 0.03571428571428571 | 1.0 | 1.0 | 0.0 | 5.892857142857143 | 6.0 | 0.0 | 0.8571428571428571 | 0.0 | 0.0 |
| paid_other | 28 | 28 | 0.03571428571428571 | 1.0 | 1.0 | 0.0 | 5.892857142857143 | 6.0 | 0.0 | 0.8571428571428571 | 0.0 | 0.0 |
| semantic_exa | 409 | 409 | 0.17603911980440098 | 0.8725973835998285 | 0.8514258028792913 | 0.18337408312958436 | 5.8508557457212715 | 5 | 1.075794621026895 | 0.07823960880195599 | 0.029339853300733496 | 0.11491442542787286 |
| semantic_tavily | 409 | 409 | 0.4547677261613692 | 0.8252101369155159 | 0.8225567552602437 | 0.21515892420537897 | 2.4987775061124693 | 1 | 1.5012224938875305 | 0.06601466992665037 | 0.3691931540342298 | 0.10268948655256724 |
| serp1 | 409 | 409 | 0.21271393643031786 | 0.8055775248929283 | 0.8031146179401992 | 0.23471882640586797 | 3.0048899755501224 | 2 | 1.7726161369193154 | 0.34963325183374083 | 0.029339853300733496 | 0.11735941320293398 |
| serp2 | 409 | 409 | 0.19315403422982885 | 0.7499742956038798 | 0.73015642303433 | 0.2885085574572127 | 3.449877750611247 | 3 | 2.1100244498777507 | 0.3422982885085575 | 0.029339853300733496 | 0.09046454767726161 |
| specialized | 28 | 28 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |

## Content terms added to the input

Most frequent added terms across all rewritten text:

| term | count |
| --- | --- |
| find | 230 |
| authoritative | 187 |
| official | 135 |
| sources | 128 |
| documentation | 112 |
| verify | 92 |
| current | 78 |
| technical | 62 |
| comprehensive | 56 |
| api | 52 |
| including | 52 |
| detailed | 49 |
| patterns | 47 |
| sources: | 43 |
| guide | 42 |
| detailing | 41 |
| model | 39 |
| configuration | 37 |
| query | 36 |
| graph | 35 |
| code | 34 |
| agent | 34 |
| tool | 32 |
| mcp | 31 |
| provider | 30 |
| web-search | 30 |
| search | 29 |
| quality | 29 |
| github | 29 |
| usage | 28 |

Most frequent input content terms dropped by rewrites:

| term | count |
| --- | --- |
| best | 54 |
| 2026 | 39 |
| practices | 39 |
| vs | 37 |
| rag | 27 |
| web | 26 |
| markdown | 26 |
| retrieval | 23 |
| tools | 22 |
| llm | 22 |
| comparison | 22 |
| search | 21 |
| production | 20 |
| content | 20 |
| site:docs.crawl4ai.com | 20 |
| 2025 | 19 |
| server | 19 |
| cross-encoder | 19 |
| benchmark | 19 |
| schema | 18 |
| changed | 15 |
| limit | 15 |
| site:modelcontextprotocol.io | 15 |
| code | 14 |
| documentation | 14 |
| guide | 14 |
| pipeline | 14 |
| results | 13 |
| reranking | 13 |
| api | 13 |

Added terms by role:

| variant_role | term | count |
| --- | --- | --- |
| free | find | 57 |
| free | official | 25 |
| free | verify | 18 |
| free | current | 17 |
| free | documentation | 12 |
| free | mcp | 9 |
| free | agent | 9 |
| free | query | 8 |
| free | patterns | 8 |
| free | web-search | 8 |
| free | api | 8 |
| free | server | 8 |
| free | code | 8 |
| free | practitioner | 8 |
| free | confirm | 7 |
| paid_brave | find | 8 |
| paid_brave | verify | 6 |
| paid_brave | official | 5 |
| paid_brave | authoritative | 5 |
| paid_brave | api | 4 |
| paid_brave | design | 3 |
| paid_brave | current | 3 |
| paid_brave | graph | 3 |
| paid_brave | collect | 3 |
| paid_brave | polish | 3 |
| paid_brave | cli | 2 |
| paid_brave | job | 2 |
| paid_brave | semantics | 2 |
| paid_brave | task | 2 |
| paid_brave | concrete | 2 |
| paid_google | find | 8 |
| paid_google | verify | 6 |
| paid_google | official | 5 |
| paid_google | authoritative | 5 |
| paid_google | api | 4 |
| paid_google | design | 3 |
| paid_google | current | 3 |
| paid_google | graph | 3 |
| paid_google | collect | 3 |
| paid_google | polish | 3 |
| paid_google | cli | 2 |
| paid_google | job | 2 |
| paid_google | semantics | 2 |
| paid_google | concrete | 2 |
| paid_google | patterns | 2 |
| paid_other | find | 8 |
| paid_other | verify | 6 |
| paid_other | official | 5 |
| paid_other | authoritative | 5 |
| paid_other | api | 4 |
| paid_other | design | 3 |
| paid_other | current | 3 |
| paid_other | graph | 3 |
| paid_other | collect | 3 |
| paid_other | polish | 3 |
| paid_other | cli | 2 |
| paid_other | job | 2 |
| paid_other | semantics | 2 |
| paid_other | concrete | 2 |
| paid_other | patterns | 2 |
| semantic_exa | authoritative | 156 |
| semantic_exa | sources | 113 |
| semantic_exa | documentation | 63 |
| semantic_exa | technical | 59 |
| semantic_exa | comprehensive | 56 |
| semantic_exa | detailed | 49 |
| semantic_exa | sources: | 43 |
| semantic_exa | including | 41 |
| semantic_exa | detailing | 41 |
| semantic_exa | official | 36 |
| semantic_exa | guide | 35 |
| semantic_exa | page | 21 |
| semantic_exa | comparing | 20 |
| semantic_exa | examples | 19 |
| semantic_exa | find | 16 |
| semantic_tavily | find | 17 |
| semantic_tavily | configure | 13 |
| semantic_tavily | use | 13 |
| semantic_tavily | handle | 13 |
| semantic_tavily | verify | 10 |
| semantic_tavily | official | 9 |
| semantic_tavily | patterns | 8 |
| semantic_tavily | tool | 8 |
| semantic_tavily | best | 8 |
| semantic_tavily | current | 7 |
| semantic_tavily | practices | 6 |
| semantic_tavily | higher | 6 |
| semantic_tavily | systems | 6 |
| semantic_tavily | query | 5 |
| semantic_tavily | contract | 5 |
| serp1 | find | 58 |
| serp1 | official | 32 |
| serp1 | documentation | 23 |
| serp1 | current | 18 |
| serp1 | verify | 18 |
| serp1 | definition | 11 |
| serp1 | api | 10 |
| serp1 | mcp | 9 |
| serp1 | model | 9 |
| serp1 | agent | 9 |
| serp1 | query | 8 |
| serp1 | web-search | 8 |
| serp1 | practitioner | 8 |
| serp1 | confirm | 7 |
| serp1 | provider | 7 |
| serp2 | find | 58 |
| serp2 | official | 18 |
| serp2 | current | 18 |
| serp2 | verify | 18 |
| serp2 | agent | 11 |
| serp2 | provider | 8 |
| serp2 | query | 8 |
| serp2 | patterns | 8 |
| serp2 | web-search | 8 |
| serp2 | extraction | 8 |
| serp2 | api | 8 |
| serp2 | documentation | 8 |
| serp2 | methods | 8 |
| serp2 | quality | 8 |
| serp2 | practitioner | 8 |

### Data-derived added-text observations

Added content-term occurrences: `7874`. The eight most frequent additions computed from the paired text are `find, authoritative, official, sources, documentation, verify, current, technical`.
Input content terms dropped by rewrites: `2816` occurrences. The eight most frequent dropped terms computed from the paired text are `best, 2026, practices, vs, rag, web, markdown, retrieval`.
The role table and examples below are the evidence for interpreting whether these additions clarify the input or introduce scope/template noise.

## Anchor, phrase, frame, and operator preservation

Across all pairs, mean content-term recall is `0.876`, mean anchor recall is `0.861`, and `426` pairs lose at least one extracted anchor. These are preservation diagnostics: losing an anchor is a concrete text-level risk, while preserving one does not prove semantic correctness.

Frame changes (keyword/question/imperative): `{('question', 'keyword'): 47, ('keyword', 'question'): 146, ('keyword', 'imperative'): 2, ('imperative', 'keyword'): 4, ('imperative', 'question'): 1}`. Operator-profile changes: `13` distinct input→rewrite transitions across `209` pairs. The full pair artifact retains the exact text for every transition.

Anchor-loss examples:

| variant_role | input_query | rewritten_query | text_class | added_terms | dropped_anchors | repeated_phrases |
| --- | --- | --- | --- | --- | --- | --- |
| serp1 | HTML TO CLEAN MARKDOWN AND JSON BEST PRACTICES | HTML to clean Markdown conversion | anchor_loss | conversion | and, best, clean, json, markdown, practices, to |  |
| serp1 | HTML TO CLEAN MARKDOWN AND JSON BEST PRACTICES | HTML to clean Markdown conversion | anchor_loss | conversion | and, best, clean, json, markdown, practices, to |  |
| serp2 | site:sqlite.org query planner OFFSET LIMIT indexed ORDER BY primary key | SQLite vector search options brute force ANN quantization | anchor_loss | ann, brute, force, options, quantization, search, sqlite, vector | by, limit, offset, order, site:sqlite.org, sqlite.org |  |
| semantic_exa | HTML TO CLEAN MARKDOWN AND JSON BEST PRACTICES | technical guide on converting HTML to clean Markdown and JSON with schema validation | anchor_loss | converting, guide, schema, technical, validation | and, best, clean, markdown, practices, to |  |
| semantic_exa | HTML TO CLEAN MARKDOWN AND JSON BEST PRACTICES | technical guide on converting HTML to clean Markdown and JSON with schema validation | anchor_loss | converting, guide, schema, technical, validation | and, best, clean, markdown, practices, to |  |
| free | HTML TO CLEAN MARKDOWN AND JSON BEST PRACTICES | HTML to clean Markdown and JSON conversion best practices | anchor_loss | conversion | and, best, clean, markdown, practices, to |  |
| serp2 | HTML TO CLEAN MARKDOWN AND JSON BEST PRACTICES | HTML to JSON extraction best practices | anchor_loss | extraction | and, best, clean, markdown, practices, to |  |
| semantic_tavily | HTML TO CLEAN MARKDOWN AND JSON BEST PRACTICES | how to convert HTML to clean Markdown and JSON | anchor_loss | convert | and, best, clean, markdown, practices, to |  |
| free | HTML TO CLEAN MARKDOWN AND JSON BEST PRACTICES | HTML to clean Markdown and JSON conversion best practices | anchor_loss | conversion | and, best, clean, markdown, practices, to |  |
| serp2 | HTML TO CLEAN MARKDOWN AND JSON BEST PRACTICES | HTML to JSON extraction best practices | anchor_loss | extraction | and, best, clean, markdown, practices, to |  |
| semantic_tavily | HTML TO CLEAN MARKDOWN AND JSON BEST PRACTICES | how to convert HTML to clean Markdown and JSON | anchor_loss | convert | and, best, clean, markdown, practices, to |  |
| serp2 | python CPU NLP libraries 2025 2026 bm25s sqlite FTS5 lingua language detection dateparser presidio outlines instructor yake flashtext pyahocorasick duckdb fts | lingua dateparser presidio outlines instructor yake flashtext pyahocorasick | anchor_loss |  | 2025, 2026, bm25s, cpu, fts5, nlp |  |
| semantic_tavily | site:docs.crawl4ai.com core content selection css_selector fit_markdown PruningContentFilter BM25ContentFilter | What evaluation metrics are recommended for assessing crawl4ai content cleaning filters? | anchor_loss | assessing, cleaning, crawl4ai, evaluation, filters, metrics, recommended | bm25contentfilter, css_selector, docs.crawl4ai.com, fit_markdown, site:docs.crawl4ai.com |  |
| serp2 | site:nodejs.org/api/worker_threads.html ESM Worker URL worker_threads | worker thread isolation safe filesystem publication Node.js | anchor_loss | filesystem, isolation, node.js, publication, safe, thread | esm, nodejs.org, site:nodejs.org/api/worker_threads.html, url, worker_threads |  |
| serp2 | site:docs.crawl4ai.com core content selection css_selector target_elements excluded_selector | Markdown-aware chunking RecursiveChunker clustering topic segmentation | anchor_loss | chunking, clustering, markdown-aware, recursivechunker, segmentation, topic | css_selector, docs.crawl4ai.com, excluded_selector, site:docs.crawl4ai.com, target_elements |  |
| serp2 | site:docs.crawl4ai.com core content selection css_selector target_elements excluded_selector | Markdown-aware chunking RecursiveChunker clustering topic segmentation | anchor_loss | chunking, clustering, markdown-aware, recursivechunker, segmentation, topic | css_selector, docs.crawl4ai.com, excluded_selector, site:docs.crawl4ai.com, target_elements |  |
| semantic_tavily | Supabase RAG documents document_sections embedding schema SQL example site:supabase.com OR github.com | What foreign key constraints link document_sections to documents in Supabase RAG schemas? | anchor_loss | constraints, foreign, key, link, schemas | github.com, or, site:supabase.com, sql, supabase.com |  |
| serp2 | Context7 official MCP server mcp.context7.com/mcp resolve-library-id query-docs schema | DeepWiki official MCP ask_question read_wiki_structure read_wiki_contents schema mcp.deepwiki.com/mcp | anchor_loss | ask_question, deepwiki, mcp.deepwiki.com/mcp, read_wiki_contents, read_wiki_structure | context7, mcp.context7.com, mcp.context7.com/mcp, query-docs, resolve-library-id |  |
| free | Supabase RAG documents document_sections embedding schema SQL example site:supabase.com OR github.com | Supabase RAG documents table schema SQL embedding vector column example | anchor_loss | column, table, vector | document_sections, github.com, or, site:supabase.com, supabase.com |  |
| serp1 | Supabase RAG documents document_sections embedding schema SQL example site:supabase.com OR github.com | Supabase RAG documents table definition SQL | anchor_loss | definition, table | document_sections, github.com, or, site:supabase.com, supabase.com |  |
| serp2 | best cross-encoder reranker 2025 2026 BEIR benchmark bge-reranker-v2-m3 mxbai-rerank jina-reranker comparison license | mxbai-rerank cross-encoder license terms | anchor_loss | terms | 2025, 2026, beir, bge-reranker-v2-m3, jina-reranker |  |
| serp2 | site:adk.dev multi-agent workflows MCP A2A evaluation observability | agent evaluation observability | anchor_loss | agent | a2a, adk.dev, mcp, multi-agent, site:adk.dev |  |
| semantic_exa | markdownify heading_style ATX table_infer_header strip_pre | technical documentation detailing markdownify converter options for heading styles and table header inference alongside Pandoc gfm settings for code block language tags and table column validation | anchor_loss | alongside, block, code, column, converter, detailing, documentation, gfm, header, heading, inference, language, options, pandoc, settings | atx, heading_style, strip_pre, table_infer_header | and table |
| semantic_exa | Context7 official MCP server mcp.context7.com/mcp resolve-library-id query-docs schema | primary source documentation detailing the request and response contracts for Context7 MCP server, DeepWiki MCP, and YouTube Data API v3 to implement a three-mode agent-facing quick search tool without importing existing adapter functions | anchor_loss | adapter, agent-facing, api, contracts, data, deepwiki, detailing, documentation, existing, functions, implement, importing, primary, quick, request | mcp.context7.com, mcp.context7.com/mcp, query-docs, resolve-library-id |  |
| semantic_exa | Supabase RAG documents document_sections embedding schema SQL example site:supabase.com OR github.com | comprehensive guide showing the exact SQL CREATE TABLE statements for Supabase RAG documents and document_sections tables, including vector embedding columns, foreign key relationships, and index definitions | anchor_loss | columns, comprehensive, create, definitions, exact, foreign, guide, including, index, key, relationships, showing, statements, table, tables | github.com, or, site:supabase.com, supabase.com |  |

## Repeated template language and textual pollution

`557` pairs contain repeated contiguous phrases inside the rewrite. Phrases reused across at least three distinct input queries are evidence of template leakage or boilerplate reuse, especially when the phrase is not present in the corresponding input.

| phrase | distinct_inputs | occurrences | roles |
| --- | --- | --- | --- |
| authoritative sources | 114 | 116 | {'paid_brave': 1, 'paid_google': 1, 'paid_other': 1, 'semantic_exa': 113} |
| how to | 56 | 63 | {'free': 1, 'serp1': 1, 'serp2': 1, 'semantic_tavily': 53, 'semantic_exa': 7} |
| authoritative sources: | 43 | 43 | {'semantic_exa': 43} |
| technical documentation | 29 | 29 | {'semantic_exa': 29} |
| what are | 22 | 22 | {'semantic_tavily': 22} |
| documentation detailing | 22 | 22 | {'semantic_exa': 22} |
| how does | 21 | 21 | {'semantic_tavily': 21} |
| comprehensive guide | 18 | 18 | {'semantic_exa': 18} |
| official documentation | 17 | 21 | {'free': 6, 'serp1': 8, 'serp2': 1, 'semantic_exa': 6} |
| guide on | 16 | 16 | {'semantic_exa': 16} |
| how to configure | 15 | 15 | {'semantic_tavily': 13, 'semantic_exa': 2} |
| to configure | 15 | 15 | {'semantic_tavily': 13, 'semantic_exa': 2} |
| authoritative sources: find | 14 | 14 | {'semantic_exa': 14} |
| sources: find | 14 | 14 | {'semantic_exa': 14} |
| comprehensive technical | 14 | 14 | {'semantic_exa': 14} |
| are the | 14 | 14 | {'semantic_tavily': 14} |
| what are the | 14 | 14 | {'semantic_tavily': 14} |
| documentation page | 12 | 14 | {'free': 1, 'serp1': 1, 'serp2': 1, 'semantic_exa': 11} |
| technical guide | 12 | 12 | {'semantic_exa': 12} |
| technical documentation detailing | 12 | 12 | {'semantic_exa': 12} |
| best practices | 11 | 13 | {'free': 2, 'serp1': 1, 'serp2': 2, 'semantic_tavily': 6, 'semantic_exa': 2} |
| 2026 authoritative | 11 | 11 | {'semantic_exa': 11} |
| detailed technical | 11 | 11 | {'semantic_exa': 11} |
| markdown conversion | 10 | 15 | {'serp2': 3, 'free': 2, 'serp1': 5, 'semantic_tavily': 1, 'semantic_exa': 4} |
| and implementation | 10 | 13 | {'semantic_tavily': 1, 'semantic_exa': 9, 'free': 1, 'serp1': 1, 'serp2': 1} |
| documentation and | 10 | 12 | {'free': 1, 'serp1': 1, 'serp2': 1, 'semantic_exa': 9} |
| examples and | 10 | 10 | {'semantic_exa': 10} |
| detailing the | 10 | 10 | {'semantic_exa': 10} |
| how do | 10 | 10 | {'semantic_tavily': 10} |
| find official | 9 | 29 | {'paid_brave': 4, 'paid_google': 4, 'paid_other': 4, 'free': 4, 'serp1': 3, 'serp2': 3, 'semantic_tavily': 4, 'semantic_exa': 3} |

Examples with repeated text inside the rewrite:

| variant_role | input_query | rewritten_query | text_class | added_terms | dropped_anchors | repeated_phrases |
| --- | --- | --- | --- | --- | --- | --- |
| semantic_exa | markdownify heading_style ATX table_infer_header strip_pre | technical documentation detailing markdownify converter options for heading styles and table header inference alongside Pandoc gfm settings for code block language tags and table column validation | anchor_loss | alongside, block, code, column, converter, detailing, documentation, gfm, header, heading, inference, language, options, pandoc, settings | atx, heading_style, strip_pre, table_infer_header | and table |
| semantic_exa | bank collapse 2026 what happened SVB 2023 follow up | comprehensive analysis of the 2026 banking sector stability and the long-term regulatory consequences of the 2023 Silicon Valley Bank failure | anchor_loss | analysis, banking, comprehensive, consequences, failure, long-term, regulatory, sector, silicon, stability, valley | svb | of the |
| semantic_tavily | ALCE benchmark LLM citation fidelity attribution verification web search grounding | which benchmark achieves higher citation fidelity for LLMs, ALCE or other citation fidelity benchmarks | anchor_loss | achieves, benchmarks, higher, llms, other | llm | citation fidelity |
| semantic_exa | onnxruntime INT8 quantized model AVX2 vs VNNI AMD EPYC performance difference | onnxruntime INT8 quantized model AVX2 vs VNNI AMD EPYC performance difference authoritative sources comparing onnxruntime INT8 quantized model AVX2 and VNNI AMD EPYC performance difference | repeated_phrase_pollution | authoritative, comparing, sources |  | amd epyc; amd epyc performance; amd epyc performance difference; epyc performance |
| paid_google | Python asyncio TaskGroup | Python asyncio TaskGroup Find official Python asyncio Python asyncio TaskGroup documentation official Python asyncio TaskGroup Find official Python | repeated_phrase_pollution | documentation, find, official |  | asyncio taskgroup; asyncio taskgroup find; asyncio taskgroup find official; find official |
| paid_other | Python asyncio TaskGroup | Python asyncio TaskGroup Find official Python asyncio Python asyncio TaskGroup documentation official Python asyncio TaskGroup Find official Python | repeated_phrase_pollution | documentation, find, official |  | asyncio taskgroup; asyncio taskgroup find; asyncio taskgroup find official; find official |
| semantic_exa | Python packaging best practices 2025 src layout PEP 621 hatchling pyproject.toml | Python packaging best practices 2025 src layout PEP 621 hatchling pyproject.toml authoritative sources: Find current best practices for structuring Python codebases, src layout, PEP 621, hatchling, dependency groups, ruff/pyright config | repeated_phrase_pollution | authoritative, codebases, config, current, dependency, find, groups, ruff/pyright, sources:, structuring |  | 621 hatchling; best practices; layout pep; layout pep 621 |
| semantic_tavily | Python packaging best practices 2025 src layout PEP 621 hatchling pyproject.toml | Python packaging best practices 2025 src layout PEP 621 hatchling pyproject.toml? Find current best practices for structuring Python codebases, src layout, PEP 621, hatchling, dependency groups, ruff/pyright config | repeated_phrase_pollution | codebases, config, current, dependency, find, groups, ruff/pyright, structuring |  | 621 hatchling; best practices; layout pep; layout pep 621 |
| free | "search query" rewriter prompt "web search" LLM "site:" OR "generate search queries" | "search query" rewriter prompt "web search" LLM "site:" OR "generate search queries" Find actual LLM prompt actual LLM prompt templates web search engine queries Find actual LLM | repeated_phrase_pollution | actual, engine, find, templates |  | actual llm; actual llm prompt; find actual; find actual llm |
| serp1 | "search query" rewriter prompt "web search" LLM "site:" OR "generate search queries" | "search query" rewriter prompt "web search" LLM "site:" OR "generate search queries" Find actual LLM prompt actual LLM prompt templates web search engine queries Find actual LLM | repeated_phrase_pollution | actual, engine, find, templates |  | actual llm; actual llm prompt; find actual; find actual llm |
| serp2 | "search query" rewriter prompt "web search" LLM "site:" OR "generate search queries" | "search query" rewriter prompt "web search" LLM "site:" OR "generate search queries" Find actual LLM prompt actual LLM prompt templates web search engine queries Find actual LLM | repeated_phrase_pollution | actual, engine, find, templates |  | actual llm; actual llm prompt; find actual; find actual llm |
| paid_brave | temporal graph data modeling patterns bitemporal event sourcing interval edge | temporal graph data modeling patterns bitemporal event sourcing interval edge Survey authoritative graph modeling authoritative graph modeling references Survey authoritative graph modeling references for temporal | repeated_phrase_pollution | authoritative, references, survey |  | authoritative graph; authoritative graph modeling; authoritative graph modeling references; graph modeling |
| paid_google | temporal graph data modeling patterns bitemporal event sourcing interval edge | temporal graph data modeling patterns bitemporal event sourcing interval edge Survey authoritative graph modeling authoritative graph modeling references Survey authoritative graph modeling references for temporal | repeated_phrase_pollution | authoritative, references, survey |  | authoritative graph; authoritative graph modeling; authoritative graph modeling references; graph modeling |
| paid_other | temporal graph data modeling patterns bitemporal event sourcing interval edge | temporal graph data modeling patterns bitemporal event sourcing interval edge Survey authoritative graph modeling authoritative graph modeling references Survey authoritative graph modeling references for temporal | repeated_phrase_pollution | authoritative, references, survey |  | authoritative graph; authoritative graph modeling; authoritative graph modeling references; graph modeling |
| paid_brave | Meilisearch Typesense query synonyms related queries expansion ranking production | Meilisearch Typesense query synonyms related queries expansion ranking production search engines expose related-query production search engines expose seed-injection can stay compatible engines expose related-query | repeated_phrase_pollution | compatible, engines, expose, related-query, search, seed-injection, stay |  | engines expose; engines expose related-query; expose related-query; production search |
| paid_google | Meilisearch Typesense query synonyms related queries expansion ranking production | Meilisearch Typesense query synonyms related queries expansion ranking production search engines expose related-query production search engines expose seed-injection can stay compatible engines expose related-query | repeated_phrase_pollution | compatible, engines, expose, related-query, search, seed-injection, stay |  | engines expose; engines expose related-query; expose related-query; production search |
| paid_other | Meilisearch Typesense query synonyms related queries expansion ranking production | Meilisearch Typesense query synonyms related queries expansion ranking production search engines expose related-query production search engines expose seed-injection can stay compatible engines expose related-query | repeated_phrase_pollution | compatible, engines, expose, related-query, search, seed-injection, stay |  | engines expose; engines expose related-query; expose related-query; production search |
| free | FastMCP server registration @mcp.tool decorator | find official FastMCP documentation find official FastMCP mcp.tool decorator official FastMCP documentation FastMCP documentation about defining documentation about defining tools find official official FastMCP | repeated_phrase_pollution | defining, documentation, find, official, tools |  | about defining; documentation about; documentation about defining; fastmcp documentation |
| semantic_tavily | FastMCP server registration @mcp.tool decorator | find official FastMCP documentation, find official FastMCP, official FastMCP documentation, FastMCP documentation about defining, documentation about defining tools, mcp.tool decorator, find official, official FastMCP | repeated_phrase_pollution | defining, documentation, find, official, tools |  | about defining; documentation about; documentation about defining; fastmcp documentation |
| free | search related queries suggestions autocomplete spell correction did you mean features | search related queries suggestions autocomplete spell correction did you mean features Find advanced search features Find advanced search advanced search features | repeated_phrase_pollution | advanced, find |  | advanced search; advanced search features; features find; features find advanced |
| serp1 | search related queries suggestions autocomplete spell correction did you mean features | search related queries suggestions autocomplete spell correction did you mean features Find advanced search features Find advanced search advanced search features | repeated_phrase_pollution | advanced, find |  | advanced search; advanced search features; features find; features find advanced |
| serp2 | search related queries suggestions autocomplete spell correction did you mean features | search related queries suggestions autocomplete spell correction did you mean features Find advanced search features Find advanced search advanced search features | repeated_phrase_pollution | advanced, find |  | advanced search; advanced search features; features find; features find advanced |
| paid_brave | Python asyncio TaskGroup | python asyncio task group with semaphore Find official Python asyncio Python asyncio TaskGroup documentation official Python asyncio TaskGroup Find official Python | repeated_phrase_pollution | documentation, find, group, official, semaphore, task |  | asyncio taskgroup; find official; find official python; official python |
| free | onnxruntime BERT embedding CPU intra_op_num_threads batch size throughput optimization real numbers | onnxruntime BERT embedding CPU intra_op_num_threads batch size throughput optimization real numbers real-world ONNX Runtime CPU ONNX Runtime CPU tuning Find real-world ONNX Runtime Runtime CPU tuning results | repeated_phrase_pollution | find, onnx, real-world, results, runtime, tuning |  | cpu tuning; onnx runtime; onnx runtime cpu; real-world onnx |
| serp1 | onnxruntime BERT embedding CPU intra_op_num_threads batch size throughput optimization real numbers | onnxruntime BERT embedding CPU intra_op_num_threads batch size throughput optimization real numbers real-world ONNX Runtime CPU ONNX Runtime CPU tuning Find real-world ONNX Runtime Runtime CPU tuning results | repeated_phrase_pollution | find, onnx, real-world, results, runtime, tuning |  | cpu tuning; onnx runtime; onnx runtime cpu; real-world onnx |

Repeated-phrase rows are `557/2622`. Reused phrases and the exact text pairs below are the evidence to review; no external or judge-derived quality score is assigned.

## Focused expansion examples

| variant_role | input_query | rewritten_query | text_class | added_terms | dropped_anchors | repeated_phrases |
| --- | --- | --- | --- | --- | --- | --- |
| semantic_tavily | official Serper API search q num gl hl tbs site search operators | official Serper API search q num gl hl tbs site search operators Map provider-specific query syntax, structured filters, and native expansion features for the remaining registered web providers, especially the SERP2 round-robin set and the semantic Tavily/LangSearch set. | broad_expansion | especially, expansion, features, filters, map, native, provider-specific, providers, query, registered, remaining, round-robin, semantic, serp2, set |  |  |
| semantic_exa | official Serper API search q num gl hl tbs site search operators | official Serper API search q num gl hl tbs site search operators Map provider-specific query syntax, structured filters, and native expansion features for the remaining registered web providers, especially the SERP2 round-robin set and the semantic Tavily/LangSearch set. | broad_expansion | especially, expansion, features, filters, map, native, provider-specific, providers, query, registered, remaining, round-robin, semantic, serp2, set |  |  |
| semantic_exa | HKUDS LightRAG official query modes local global hybrid mix naive query_data API | HKUDS LightRAG official query modes local global hybrid mix naive query_data API authoritative sources: Establish the current LightRAG retrieval contract and distinguish zero-cost raw retrieval from LLM-synthesized querying so the proposed oh-my-pi tool can safely wrap the existing lrctl CLI. | broad_expansion | authoritative, cli., contract, current, distinguish, establish, existing, llm-synthesized, lrctl, oh-my-pi, proposed, querying, raw, retrieval, safely |  |  |
| semantic_exa | TypeScript NLP keyword extraction stemming synonyms query expansion npm natural wink-nlp compromise official | TypeScript NLP keyword extraction stemming synonyms query expansion npm natural wink-nlp compromise official authoritative sources: Identify lightweight non-LLM TypeScript packages that can extract code identifiers, normalize tokens, or expand queries without adding a full RAG framework or another model call. | broad_expansion | adding, another, authoritative, call., code, expand, extract, framework, full, identifiers, identify, lightweight, model, non-llm, normalize |  |  |
| semantic_tavily | official Brave Search API LLM Context query parameters search operators Goggles | official Brave Search API LLM Context query parameters search operators Goggles Verify which query forms and provider-side controls are supported by Brave, Exa, Tavily, and SearXNG so the heuristic design can separate query text from provider arguments and avoid emitting unsupported syntax. | broad_expansion | arguments, avoid, controls, design, emitting, exa, forms, heuristic, provider, provider-side, searxng, separate, so, supported, syntax. |  |  |
| semantic_exa | official Brave Search API LLM Context query parameters search operators Goggles | official Brave Search API LLM Context query parameters search operators Goggles Verify which query forms and provider-side controls are supported by Brave, Exa, Tavily, and SearXNG so the heuristic design can separate query text from provider arguments and avoid emitting unsupported syntax. | broad_expansion | arguments, avoid, controls, design, emitting, exa, forms, heuristic, provider, provider-side, searxng, separate, so, supported, syntax. |  |  |
| semantic_exa | LangChain JS ChatGoogleGenerativeAI @langchain/google-genai gemini-3.1-flash-lite official docs | LangChain JS ChatGoogleGenerativeAI @langchain/google-genai gemini-3.1-flash-lite official docs authoritative sources: Confirm the current TypeScript import, constructor, invocation method, API key environment variable, and model naming before integrating query decomposition. | broad_expansion | api, authoritative, before, confirm, constructor, current, decomposition., environment, import, integrating, invocation, key, method, model, naming |  |  |
| semantic_exa | web search MCP server tool design 2026 | comprehensive technical overview of MCP server web search tool design in 2026 covering public structured output schema, citation handling, fetch continuation mechanisms, multi-provider fusion strategies, and RRF reranking quality | broad_expansion | citation, comprehensive, continuation, covering, fetch, fusion, handling, mechanisms, multi-provider, output, overview, public, quality, reranking, rrf |  |  |
| semantic_tavily | HKUDS LightRAG official query modes local global hybrid mix naive query_data API | HKUDS LightRAG official query modes local global hybrid mix naive query_data API? Establish the current LightRAG retrieval contract and distinguish zero-cost raw retrieval from LLM-synthesized querying so the proposed oh-my-pi tool can safely wrap the existing lrctl CLI. | broad_expansion | cli., contract, current, distinguish, establish, existing, llm-synthesized, lrctl, oh-my-pi, proposed, querying, raw, retrieval, safely, so |  |  |
| semantic_tavily | TypeScript NLP keyword extraction stemming synonyms query expansion npm natural wink-nlp compromise official | TypeScript NLP keyword extraction stemming synonyms query expansion npm natural wink-nlp compromise official? Identify lightweight non-LLM TypeScript packages that can extract code identifiers, normalize tokens, or expand queries without adding a full RAG framework or another model call. | broad_expansion | adding, another, call., code, expand, extract, framework, full, identifiers, identify, lightweight, model, non-llm, normalize, packages |  |  |
| semantic_tavily | LangChain JS ChatGoogleGenerativeAI @langchain/google-genai gemini-3.1-flash-lite official docs | LangChain JS ChatGoogleGenerativeAI @langchain/google-genai gemini-3.1-flash-lite official docs? Confirm the current TypeScript import, constructor, invocation method, API key environment variable, and model naming before integrating query decomposition. | broad_expansion | api, before, confirm, constructor, current, decomposition., environment, import, integrating, invocation, key, method, model, naming, query |  |  |
| semantic_exa | FastAPI ONNX embedding inference run_in_executor dynamic batching keep model loaded CPU | A detailed tutorial on building a FastAPI service that serves ONNX embedding models on CPU with persistent sessions, using run_in_executor for thread‑pool offload and implementing dynamic request batching via ONNX Runtime SessionOptions | broad_expansion | building, detailed, implementing, models, offload, persistent, pool, request, runtime, serves, service, sessionoptions, sessions, thread, tutorial |  |  |
| semantic_exa | 什么是模型上下文协议？ | detailed technical article describing the specifications, design patterns, and implementation examples of model context protocols for Chinese large language models | broad_expansion | article, chinese, context, describing, design, detailed, examples, implementation, language, large, model, models, patterns, protocols, specifications |  |  |
| semantic_exa | source code search overlapping context windows maximum lines | detailed overview of implementations and specifications for managing overlapping source code search context windows under a hard line count limit, including trimming strategies and strict disjointness requirements | broad_expansion | count, detailed, disjointness, hard, implementations, including, limit, line, managing, overview, requirements, specifications, strategies, strict, trimming |  |  |
| semantic_exa | Cohen kappa intent classification evaluation | comprehensive survey paper discussing inter-annotator agreement metrics for intent classification, including Cohen's kappa, Krippendorff's alpha, and Fleiss' kappa, with analysis of their statistical properties and usage guidelines | broad_expansion | agreement, alpha, analysis, comprehensive, discussing, fleiss, guidelines, including, inter-annotator, krippendorff, metrics, paper, properties, statistical, survey |  |  |
| semantic_tavily | site:docs.exa.ai/reference/search Exa POST search query type contents highlights includeDomains excludeDomains | site:docs.exa.ai/reference/search Exa POST search query type contents highlights includeDomains excludeDomains Confirm official semantic-query and filtering capabilities for Exa and Tavily, including whether provider operators belong in query text or structured request fields. | broad_expansion | belong, capabilities, confirm, fields., filtering, including, official, operators, provider, request, semantic-query, structured, tavily, text, whether |  |  |
| semantic_exa | site:docs.exa.ai/reference/search Exa POST search query type contents highlights includeDomains excludeDomains | site:docs.exa.ai/reference/search Exa POST search query type contents highlights includeDomains excludeDomains Confirm official semantic-query and filtering capabilities for Exa and Tavily, including whether provider operators belong in query text or structured request fields. | broad_expansion | belong, capabilities, confirm, fields., filtering, including, official, operators, provider, request, semantic-query, structured, tavily, text, whether |  |  |
| semantic_exa | site:github.com/HKUDS/LightRAG /documents/upload source_conflicts scan input directory canonical basename | site:github.com/HKUDS/LightRAG /documents/upload source_conflicts scan input directory canonical basename authoritative sources: Verify whether the observed 409 input-directory conflicts and serial processing are caused by the local CLI orchestration or by LightR | broad_expansion | 409, authoritative, caused, cli, conflicts, input-directory, lightr, local, observed, orchestration, processing, serial, sources:, verify, whether |  |  |
| semantic_exa | Claude Code MCP tool documentation | comprehensive official guide detailing the Model Context Protocol tools and API reference for Claude Code MCP, including setup instructions and usage examples | broad_expansion | api, comprehensive, context, detailing, examples, guide, including, instructions, model, official, protocol, reference, setup, tools, usage |  |  |
| semantic_exa | web search MCP server | detailed recent blog article covering step‑by‑step setup, configuration options, and performance testing of a web search MCP server, published within the past week | broad_expansion | article, blog, configuration, covering, detailed, options, past, performance, published, recent, setup, step, testing, week, within |  |  |
| semantic_exa | production RAG hybrid retrieval benchmark reranking | detailed engineering benchmark report comparing hybrid retrieval and reranking methods for production RAG, including reproducible datasets, BM25 versus dense retrieval results, and rigorous practitioner analysis | broad_expansion | analysis, bm25, comparing, datasets, dense, detailed, engineering, including, methods, practitioner, report, reproducible, results, rigorous, versus |  |  |
| semantic_exa | LlamaIndexTS TypeScript QueryFusionRetriever SubQuestionQueryEngine query decomposition official docs | LlamaIndexTS TypeScript QueryFusionRetriever SubQuestionQueryEngine query decomposition official docs authoritative sources: Identify ready-to-use TypeScript-native LlamaIndex modules for query expansion, decomposition, and fusion, including package imports and runtime requirements. | broad_expansion | authoritative, expansion, fusion, identify, imports, including, llamaindex, modules, package, ready-to-use, requirements., runtime, sources:, typescript-native |  |  |
| semantic_exa | query document click graph temporal decay | query document click graph temporal decay authoritative sources: Find complete evidence and implementation examples for time-bounded query-document graph feedback used for bounded web-search query expansion. | broad_expansion | authoritative, bounded, complete, evidence, examples, expansion., feedback, find, implementation, query-document, sources:, time-bounded, used, web-search |  |  |
| semantic_exa | how do I add an MCP server to claude code | comprehensive tutorial page detailing step‑by‑step instructions for adding an MCP server to Claude code, including configuration files, authentication setup, and example code snippets | broad_expansion | adding, authentication, comprehensive, configuration, detailing, example, files, including, instructions, page, setup, snippets, step, tutorial |  |  |
| semantic_exa | MCP server OAuth 2 authentication guide | detailed guide published after January 2026 covering MCP server OAuth 2 authentication setup, client registration, scope selection, and token refresh procedures | broad_expansion | 2026, after, client, covering, detailed, january, procedures, published, refresh, registration, scope, selection, setup, token |  |  |

Example selection criterion: added content terms with no extracted anchor loss and no repeated phrase. The examples are shown as text pairs without assigning a quality score.

## Conclusions drawn after the data-only pass

These conclusions are generated from the paired text measurements above after the data-only artifacts were written and inspected. They are not judge labels.

- Token-normalized text is unchanged in `1036/2622` pairs (39.5%); the remaining `1586` pairs change at least one token.
- The extracted-anchor diagnostic flags `426/2622` pairs (16.2%) with at least one input anchor absent from the rewrite. This is a text-preservation risk indicator, not a semantic-failure label.
- Rewrites add `7874` content-term occurrences and drop `2816`. The most frequent additions are `find, authoritative, official, sources, documentation, verify, current, technical`, while the most frequent drops are `best, 2026, practices, vs, rag, web, markdown, retrieval`.
- Repeated contiguous phrases occur in `557/2622` pairs (21.2%). This is a direct textual repetition signal, not a quality score.
- Among rewrite groups with the maximum observed pair count (n=`409`), `serp2` has the highest extracted-anchor-loss rate (`0.289`), while `semantic_exa` has the highest mean number of added content terms (`5.851`).
- The most common request-frame change is `keyword → question` (146 pairs); this records surface framing change without asserting intent change.
- The most widely reused added phrase is `authoritative sources`, appearing across `114` distinct inputs and `116` occurrences (roles: `paid_brave, paid_google, paid_other, semantic_exa`). This is evidence of shared wording in the rewrite text.

## External baselines used

The external baseline is methodological, not a local judge score: query reformulation work separates semantic preservation from downstream retrieval usefulness, and recommends inspecting both content retention and the retrieval dimensions introduced by a rewrite.

- McQueen, fully specified query rewriting and entity preservation: https://aclanthology.org/2022.emnlp-main.320/
- Search-Oriented Conversational Query Editing (EdiRCS), overlap and search-oriented editing: https://aclanthology.org/2023.findings-acl.256/
- ConvGQR, reformulation versus downstream search usefulness: https://aclanthology.org/2023.acl-long.274
- CONQRR, length-aware conversational query rewriting: https://aclanthology.org/2022.emnlp-main.679.pdf
- ReDI, decomposition and interpretation for search queries: https://arxiv.org/html/2509.06544v4
- MiniELM, rewrite relevance/diversity evaluation: https://arxiv.org/html/2501.18056v2
- Stanford IR ranked retrieval evaluation: https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-of-ranked-retrieval-results-1.html

## Artifacts

- All input/rewrite text pairs: `docs\analysis\semantic_query_rewrite_pairs_2026-09-15.parquet`
- All unique input query rows: `docs\analysis\semantic_query_inputs_2026-09-15.parquet`
- This report: `docs\analysis\semantic_query_rewrite_text_2026-09-15.md`
- Machine-readable summary: `docs\analysis\semantic_query_rewrite_text_2026-09-15.json`
