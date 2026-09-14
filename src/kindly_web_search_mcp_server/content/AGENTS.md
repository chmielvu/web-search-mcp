<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-09-14 | Last verified: 2026-09-14 -->

# AGENTS.md - Content

Content acquisition, extraction, and conversion to LLM-ready Markdown.

## Pipeline Architecture

Candidate-only acquisition with one shared evaluator and one finalizer:

- **Producers** (`resolver_registry.py`, `resolvers/`, `jina_reader.py`,
  `remote_clients.py`) return `RawDocument` (or raise `AcquisitionError`);
  no sanitizing, classifying, scoring, or artifact construction inside.
- **Shared evaluation** (`markdown_processor.py`): source-range cleanup on
  the original text, rumdl stdin diagnostics (`MD056,MD075,MD070,MD031,MD058`,
  never `--fix`), measured `QualityReport`, index-only mdformat+GFM with a
  defect-only skip gate. Agent mode never formatter round-trips.
- **Orchestration** (`fetch_pipeline.py`): resolver registry → Jina (single
  faithful profile; skipped when the registry already accepted) → Crawl4AI
  (only after a rejected/failed generic attempt) → browser (Camoufox) → Wayback
  archive;
  every candidate evaluated once, best wins on acceptance, scope,
  completeness, measured quality, then earlier index.

## Key Files

| File | Role |
|---|---|
| `fetch_pipeline.py` | Single-URL orchestrator: registry → Jina → Crawl4AI → browser/archive; candidate selection only |
| `resolver_registry.py` | Ordered `ResolverSpec` list; every spec owns its `match_*` + `fetch_*_raw` |
| `constructor.py` | Sole `ContentArtifact` constructor (`finalize_artifact` + `rehydrate_cached_artifact`), atomic index writer to `outputs/` |
| `markdown_processor.py` | Shared evaluator: source-range cleanup, rumdl diagnostics, `QualityReport`, index-only mdformat+GFM |
| `models.py` | Provider-neutral contracts: `RawDocument`, `Candidate`, `FetchOptions`, `ResolverSpec`, `QualityReport` |
| `jina_reader.py` | DOM-routed Jina Reader transport: per-route engines/presets/timeouts from `dom_detector`, JSON/SSE/frontmatter decode, RawDocument adapter |
| `dom_detector.py` | Static-HTML DOM classifier: bounded preflight → `RouteDecision` (agent/research/readerlm-v2/readerlm-research/research+browser-timing/browser) with evidence trail |
| `http_utils.py` | Transport, two deliberate layers: borrowed-context (`request_with_redirect_validation`, `fetch_json`, `fetch_text`, `raise_for_status`, `bytes_with_cap`; raises `AcquisitionError`) and standalone SSRF-guarded `safe_fetch_url` (curl_cffi→httpx, raises `SafeFetchError`) |
| `html_tools.py` | HTML tooling: `soup_from_html`, `url_hostname`, `html_to_markdown` (markdownify only), `extract_metadata`, `extract_links` |
| `documents.py` | Shared builders for neutral contracts: thread reducers (`build_thread_document`, `thread_messages_from_dict/flat`), `build_package_document`, `build_repository_document` |
| `github_api.py` | GitHub GraphQL + REST helpers (`github_graphql`, `graphql_paginate_comments`, `rest_get`, `fetch_readme_markdown`, `repo_values`, `thread_values`, `resolve_github_token`) |
| `renderers.py` | Shared neutral-model renderers: `RawDocument` → Markdown exactly once |
| `link_discovery.py` | URL discovery: page links, sitemap extraction, Tavily site mapping (`discover_links`, `map_site`) |
| `machine_readable.py` | Machine-readable format detection + renderers: JSON/JSONL/YAML/TOML, feeds, CSV/TSV, RTF, subtitles, SVG, MHTML, columnar |
| `ai_summary.py` | Unified LLM summarization pipeline, models, and fallback ladder |
| `resolvers/` | One adapter per URL family, each owning `match_*` + `fetch_*_raw` + its payload builders: files (documents), raw_text, github_*, stackexchange, reddit, hackernews, discourse, wikipedia, arxiv, unpaywall, pypi, npm, crates, huggingface, youtube, telegram, twitter, wayback, llms_txt, md_twin; `_bridge.py` holds the shared text-producer scaffolding |
## Rules

- `fetch_pipeline.py` is the main single-URL path. Registry match runs first;
  on no match, Jina → Crawl4AI → browser/archive fallbacks run (always on when the
  corresponding client is configured). The
  `md_twin` spec sits last in the registry: pages with a clean `page.md`
  sibling short-circuit the cascade entirely; misses cost one bounded GET.
- Every resolver spec owns its `match_*` + `fetch_*_raw` pair. Producers
  return `RawDocument` (or raise `AcquisitionError`); they never sanitize,
  classify, score, or construct artifacts.
- `ContentArtifact` is constructed in exactly two places: `finalize_artifact`
  (live path) and `rehydrate_cached_artifact` (cache path). No other module
  constructs it.
- The shared `MarkdownProcessor` owns all markdown evaluation. Producers
  return source text; the processor decides whether mdformat+GFM runs, and
  only in index mode.
- `utils/content_classify.py` and `utils/text_clean.py` markdown hygiene are
  deleted. Status is derived from finalizer evidence
  (`artifact.quality.flags` + `artifact.error.code`) and from
  `Candidate.failure` (`ContentError.status`) set by acquisition.
- Index mode persists content-addressed `.md` files under `REPO_ROOT/outputs/`
  via atomic `tempfile.NamedTemporaryFile` + `os.replace`. Cache version key
  includes `policy_version + processing_mode + normalized_url`.
- `tavily_map.py` is Tavily-only (no fallback).
- Per-stage timeouts: Jina 60s (ReaderLM latency), Crawl4AI 30s, local 20s, Camoufox 35s.
- Jina Reader circuit breaker: opens after 3 failures in 60s. Single faithful
  profile: `Accept: application/json`, `X-Respond-With: frontmatter`,
  `X-Retain-Links: all`, `X-Retain-Images: all`. Caching stays on by default
  (`X-No-Cache` only for explicit retries).
- Content-type validation routes HTML, JSON/JSONL, YAML, TOML, RSS/Atom, CSV/TSV,
  XML, RTF, subtitles, SVG, plain text, Office, MHTML, and columnar documents
  without browser escalation.
- Optional summaries use the Gemini chain `gemini-3.5-flash-lite` →
  `gemini-3.1-flash-lite` → Gemma; `fetch` exposes `ai_summary: bool = false`.
  Non-empty fetched content disables URL-context; empty body + URLs still uses it.

## Adding a New Specialized Resolver

1. Add the resolver module in `content/resolvers/`
2. Add a `ResolverSpec` entry in `resolver_registry.py` with its `match_*` + `fetch_*_raw`
3. Reuse `_http.py` transport and `packages.py`/`threads.py` renderers where applicable

## Testing

Tests are frozen. Verify behavior with throwaway scripts, not the suite:

```python
import kindly_web_search_mcp_server.content.markdown_processor as mp
import kindly_web_search_mcp_server.content.constructor as art
from kindly_web_search_mcp_server.content.models import (
    Candidate,
    RawDocument,
    TextDocument,
    FetchOptions,
)

sample = "# Title\n\n| a | b |\n|---|---|\n| 1 |\n| 2 | 3 | EXTRA |\n"
proc = mp.MarkdownProcessor()
agent = await proc.process(sample, "agent")
assert "EXTRA" in index.markdown and "skipped-mdformat-canonicalization" in index.transforms

doc = RawDocument(
    input_url="https://example.com/x",
    fetched_url="https://example.com/x",
    source_type="test",
    fetch_backend="probe",
    body=TextDocument(text=sample, format="markdown"),
)
cand = Candidate(document=doc, processed=index, attempt_index=0)
a = await art.finalize_artifact(
    "https://example.com/x", cand, options=FetchOptions(processing_mode="index")
)
from pathlib import Path

assert Path(a.output_path).read_text(encoding="utf-8") == a.markdown

envelope = {"policy_version": "markdown-source-v2", "artifact": art.artifact_to_dict(a)}
rehydrated = art.rehydrate_cached_artifact(envelope, "https://example.com/x")
assert rehydrated is not None and rehydrated.markdown == a.markdown
```

### File-merge cutover (2026-09-14, later pass)
- Six modules merged into three: `_http.py` + `safe_fetch.py` + `html_convert.py`
  + `llms_txt.py` → **`content_utils.py`** (borrowed-context transport,
  standalone SSRF fetch, HTML tooling, llms.txt probe); `format_renderers.py`
  → **`typed_content.py`** (detection + rendering in one module);
  `tavily_map.py` → **`link_discovery.py`** (URL discovery incl. `map_site`).
  Public function names unchanged (`safe_fetch_url`, `map_site`,
  `discover_links`, `render_typed_content`, ...); only import paths moved.
- Dead code removed in the same pass: `extract_map_urls`,
  `TavilyMapConfigError` (raise folded into `TavilyMapError`),
  `paginate_rest`/`fetch_raw_blob`/`_next_link` (`_github_client.py`),
  `build_resolver_target` (`_http`), `SUPPORTED_TYPED_FORMATS`,
  `is_raw_text_url` + `NON_RAW_TEXT_EXTENSIONS` (`raw_text.py`),
  `_rendered_markdown`, `_FENCE_MARKER_RE`, `_ORIGIN_BLOCK_STATUSES`,
  `_clean_html_to_text` pass-through shim.
- Duplicated logic extracted: `_AttemptLog.record_outcome` (9 attempt sites),
  `graphql_paginate_comments` (issues/pulls/discussions pagination),
  `thread_values` (owner/repo/number coercion), `bridge_text_producer` +
  `_bridge_document` (5 Markdown-returning producers), `_content_type_allowed`
  (safe_fetch content-type gate, was duplicated across curl_cffi/httpx paths),
  Wikipedia bs4 fallback now uses shared `soup_from_html`.
- Inline imports hoisted to module top in `producers/remaining.py`,
  `fetch_pipeline.py` (except true optional-dependency gates), and the
  arXiv `_get_int_env` shim replaced by a direct `get_int_env` import.
- Telemetry provider label `tavily_map` in `tools/sitemap.py` kept verbatim:
  it is an analytics string contract, not a module reference.

### Recent Changes (2026-09-14)
- `content/renderers.py` added: the one boundary that converts neutral
  `RawDocument` payloads (threads, packages, repositories, declared HTML/literal
  text) to Markdown before `MarkdownProcessor` evaluation. Structured bodies no
  longer collapse to title-only text.
- Registry default wired into `fetch_content_artifact` (`registry=None` now
  uses `resolver_registry.REGISTRY`); the tool path no longer bypasses it.
- Rejection-ordered ladder enforced: accepted registry candidates skip Jina;
  Crawl4AI/browser/archive run only when no accepted candidate exists.
- Stage attempts carry measured `chars_kept` + `quality_score`; outcomes map
  into the analytics CHECK domain (`success|partial|blocked|unsupported|error|skipped`).
- `RawDocument.coverage`, `ContentArtifact.coverage`, `Candidate.failure`, and
  `ContentError.status` added; failure status flows to public mapping unchanged.
- `PROCESSING_POLICY_VERSION` bumped to `markdown-source-v2` (cache keys and
  envelopes from v1 reject as misses).
- Deleted dead code: `utils/content_classify.py` and `utils/text_clean.py`
  markdown-hygiene block (`sanitize_markdown`, `strip_boilerplate`,
  `polish_prose`, `strip_jina_frontmatter`, `parse_jina_frontmatter`).
### Resolver-adapter cutover (2026-09-14, final pass)
- `producers/` dissolved: every fetch producer folded into its resolver
  module as `fetch_*_raw`; `resolver_registry.py` is a pure ordered list of
  `ResolverSpec(name, match, fetch)` with no embedded stubs. `match_*`
  functions moved out of the registry into their resolvers; `check_llms_txt`
  moved to `resolvers/llms_txt.py` (its `match_llms_txt` claims root URLs and
  explicit `/llms.txt` paths — the explicit-path branch now works).
- Pipeline llms stage removed: the registry resolver owns root URLs, so
  `_llms_txt_candidate`/`_safe_llms_probe`/`_is_root_url` are gone;
  `_STAGE_ORDER` keeps the `llms_txt` key for historical analytics rows.
- `content_utils.py` split into `http_utils.py` (transport) and
  `html_tools.py` (HTML); `safe_domain` renamed `url_hostname`.
- `threads.py` + `packages.py` merged into `documents.py` (shared builders
  only: thread reducers, `build_package_document`, `build_repository_document`);
  per-registry payload fetchers/builders moved into their single-consumer
  resolvers (pypi/npm/crates/huggingface) for locality.
- `_github_client.py` renamed `github_api.py`; `graphql` → `github_graphql`,
  `resolve_token` → `resolve_github_token`; github resolvers import the real
  names (no `shared_*` aliases).
- Predicate renames: `_binary_target` → `_is_binary_target`,
  `_browser_opt_in` → `_browser_opt_in_enabled`, `_archive_opt_in` →
  `_archive_opt_in_enabled`, `_clean_hn_html` → `_hn_html_to_text`;
  `resolvers/document.py` → `resolvers/files.py` with public converters
  (`convert_pdf_to_markdown`, `convert_ipynb_to_markdown`,
  `convert_office_with_markitdown`, `detect_doc_type`).
- Spec names in `REGISTRY` are unchanged (23 specs, telemetry contract).

