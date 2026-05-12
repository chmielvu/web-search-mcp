# PRD: `get_content` and `batch_get_content` Re-Architecture

Date: 2026-05-12  
Scope: `get_content` / `batch_get_content` only  
Compatibility: no backward compatibility required

## 1. Objective

Build a reliable, bounded, agent-ready content retrieval layer with:

- Structured fetch status (not string-only failures)
- Deterministic output windowing (`char_offset` + `char_length`)
- Strong URL/network safety guards
- Automatic backend routing (direct HTTP -> Jina Reader -> browser, optional Bright Data)
- Robust batch orchestration with continuation cursor
- Optional AI summary as a derived output (not replacement for source content)

## 2. Current Problems (validated in repo)

- `server.py:get_content` returns `page_content` string from `resolve_page_content_markdown()` with errors flattened to Markdown notes.
- `server.py:batch_get_content` is a thin parallel wrapper (`urls[:10]`, no cursor, no output budget, weak status model).
- `content/resolver.py` returns `str | None`, so caller loses backend/method/status/quality context.
- `scrape/http_extract.py` has no SSRF/private-IP/redirect validation contract.
- Browser error pages can be misclassified as successful content.

## 3. Product Requirements

### 3.1 `get_content`

Inputs:

- `url: str`
- `char_offset: int = 0`
- `char_length: int = 20000` (clamped to safe max)
- `summary_mode: Literal["none", "brief", "detailed"] = "none"`
- `focus_query: str | None = None`

Outputs:

- `status`: `success | partial | blocked | unsupported | error`
- `page_content`: bounded slice only
- `window`: `{offset, length, returned_chars, total_chars, has_more, next_offset}`
- `input_url`, `normalized_url`, `fetched_url`, `source_type`, `fetch_backend`, `content_type`
- `error` object when non-success
- optional `summary` object when requested

### 3.2 `batch_get_content`

Inputs:

- `urls: list[str]`
- `max_concurrency: int = 4` (bounded)
- `per_item_char_length: int = 8000` (bounded)
- `total_char_budget: int = 120000` (bounded)
- `cursor: str | None = None`

Outputs:

- `results: list[BatchContentResult]` with per-item status/error/window
- `total_requested`, `total_returned`, `total_chars_returned`
- `has_more`, `cursor`

## 4. Architecture

### 4.1 New modules

- `content/artifact.py`
  - `ContentArtifact`, `ContentError`, `ContentStatus`, quality fields

- `content/safe_fetch.py`
  - URL scheme validation (`http|https`)
  - Block localhost/private/link-local/reserved IPs
  - DNS resolution + resolved-IP validation
  - Redirect final URL re-validation
  - Response byte cap (header + streamed bytes)

- `content/status_classifier.py`
  - Detect browser/network error pages, access denied, captcha/login walls, empty shell pages
  - Decide cache eligibility

- `content/windowing.py`
  - Deterministic content slicing + continuation metadata

- `content/fetch_pipeline.py`
  - Unified routing:
    1. Specialized resolvers (StackExchange, GitHub Issue/Discussion, Wikipedia, arXiv)
    2. Generic PDF detection + extraction
    3. Safe direct HTTP extraction
    4. Jina Reader fallback
    5. Browser fallback
  - Return `ContentArtifact`

- `content/batch_orchestrator.py`
  - Deduplicate URLs, isolate per-item failure, enforce total budget, produce cursor

- `content/summary.py`
  - Chutes-backed structured summary generation over extracted source content
  - `none/brief/detailed` modes

### 4.2 Server wiring

- `server.py:get_content` becomes a thin adapter:
  - fetch artifact -> window slice -> optional summary -> response model

- `server.py:batch_get_content` becomes:
  - orchestrator call -> structured response (with cursor)

## 5. Data and Caching

- Keep canonical source extraction separate from derived representations.
- Reuse existing page cache for canonical content short-term.
- Cache only classified-valid content (not blocked/error page shells).
- Derived summary/window cache keyed by `content_hash + params_hash` (phase 2 if needed).

## 6. AI Summary Design

### 6.1 Contract

- Summary is optional and derived from extracted source.
- It never replaces `page_content`.
- It must be schema-constrained JSON and source-grounded.

### 6.2 Prompt requirements

- Use only provided source text.
- Preserve entities, numeric facts, versions, dates, identifiers, and explicit uncertainty.
- Do not fabricate facts.
- Return strict JSON schema only.
- Use `CHUTES_API_TOKEN` and default model `zai-org/GLM-5-Turbo`.

### 6.3 Long content

- Map-reduce summarization for oversized content:
  - chunk summaries -> final merge preserving contradictions and uncertainty markers

## 7. FastMCP Context Usage

- Use `ctx.info()` progress updates for long fetch/batch runs.
- Store short-lived batch continuation state in session state when available.
- Do not rely on FastMCP built-in list pagination for tool payload pagination; tool contract handles this.

## 8. Implementation Plan

### Phase A: Core fetch contract

1. Add artifact/status/window models
2. Build `safe_fetch.py`
3. Build `status_classifier.py`
4. Build `windowing.py`

Acceptance:

- URL safety and window unit tests pass

### Phase B: Pipeline and tool rewiring

1. Build `fetch_pipeline.py`
2. Rewrite `get_content` to artifact + window response
3. Build `batch_orchestrator.py`
4. Rewrite `batch_get_content` to structured batch response with cursor

Acceptance:

- Direct tool tests pass for status/window/cursor/budget

### Phase C: Summary integration

1. Build `summary.py` with structured outputs
2. Wire `summary_mode` + `focus_query` into `get_content`

Acceptance:

- Summary schema tests pass

## 9. Test Plan (required)

- `tests/test_content_safe_fetch.py`
  - private/localhost rejection, redirect revalidation, byte-limit enforcement

- `tests/test_content_status_classifier.py`
  - Chromium/network error pages, blocked pages, valid content separation

- `tests/test_content_windowing.py`
  - offset/length slices, continuation correctness

- `tests/test_get_content_contract.py`
  - structured response fields and statuses

- `tests/test_batch_get_content_contract.py`
  - dedupe, per-item failure isolation, total budget, cursor continuation

## 10. Non-Goals

- Metadata-heavy evidence pack outputs
- New search/rerank/query-rewrite redesign in this phase
- Client-facing backend selector knobs

## 11. Definition of Done

- `get_content` and `batch_get_content` use the new artifact pipeline
- string-only failure contract removed
- windowing and cursor behavior implemented and tested
- safety guards enforced before network extraction
- focused `pytest` and `ruff` for changed modules pass
