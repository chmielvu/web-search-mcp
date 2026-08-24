# Plan: Apify-backed X/Twitter + Reddit resolvers

**Date:** 2026-08-23 · **Status:** implemented · **Decision:** user requested Reddit inclusion after observing unreliable free Reddit layers.

## Decision

Add Apify-backed specialized resolution for **X/Twitter and Reddit**.
X/Twitter is a new Tier-1 resolver. Reddit keeps its existing free cascade and
adds Apify as Layer 4 by default; `APIFY_REDDIT_FIRST=true` makes Apify primary.
There is no platform-agnostic fallback tier.

## Rationale (verified)

| Fact | Source |
| --- | --- |
| Zero twitter/x.com handling in `src/`; Tier‑2 cascade hits X login walls | grep across `src/`, `fetch_pipeline.py` |
| Sync endpoint returns dataset items in one POST; ≤5 min hold | docs.apify.com/academy/api/run-actor-and-retrieve-data-via-api.md |
| Free plan $5/mo credits; PPE actors ~$0.25–0.50/1k tweets ⇒ ~$0.0004–0.005 per URL fetch | apify.com/pricing + actor pages |
| X API official: Basic ~$200/mo ⇒ orders more expensive at resolver volumes | web search 2026‑08 |
| House style: thin httpx > vendor SDK; env-gated singleton returning None = fail-open | semanticscholar precedent; `remote_clients.py` |

## Design

### 1. Settings (`settings.py`)
- `APIFY_API_TOKEN` — absent ⇒ all Apify layers are inert.
- `APIFY_TWITTER_ACTOR` — pinned, swappable actor name.
- `APIFY_REDDIT_ACTOR` — pinned, swappable actor name.
- `APIFY_REDDIT_FIRST` — optional priority switch; default preserves free layers first.
- `APIFY_TIMEOUT_SECONDS` and `APIFY_EXTRA_INPUT_JSON` — bounded timeout and actor-schema escape hatch.

### 2. Client (`content/remote_clients.py`)
- `ApifyClientError(RuntimeError)` (retryable flag like siblings).
- `get_apify_client()` singleton → `None` if token unset; mirrors `get_crawl4ai_client`.
- One method: `run_sync_get_dataset_items(actor: str, run_input: dict) -> list[dict]`
  - `POST https://api.apify.com/v2/actors/{actor}/run-sync-get-dataset-items`
  - Auth: `Authorization: Bearer {token}` (token-in-query also valid; header preferred).
  - Timeout ~90s (runs are seconds-scale); map HTTP 401/402/403/429 → non-retryable-or-backoff per provider resilience contract.
  - No polling needed below the 5-min sync ceiling.

### 3. Resolver (`content/resolvers/twitter.py`)
- `TwitterError`, `@dataclass TwitterTarget(tweet_id, screen_name | None)`.
- `parse_twitter_url`: accept `x.com` / `twitter.com` / `mobile.twitter.com`;
  `/status/<id>`, `/<user>/status/<id>`, `/i/web/status/<id>`; profile `/<user>` (optional phase 2).
- `fetch_twitter_markdown(url) -> str`:
  - run_input = minimal defaults + `{ "urls": [url] }` (per pinned actor's schema).
  - Render markdown: author handle/name, date, tweet text, engagement stats, permalink; media URLs as links.
  - Any error ⇒ raise `TwitterError`; pipeline converts to `None` ⇒ Tier‑2/3 cascade proceeds.
- Never accept/store user cookies; guest-access actors only.

### 3b. Reddit Layer 4 (`content/resolvers/reddit.py`)
- `_fetch_reddit_via_apify` runs the configured actor with `{ "urls": [url], "includeComments": true }`.
- Tolerantly maps post/comment result aliases into the existing Reddit Markdown renderer.
- Default order: direct JSON → old.reddit HTML → Arctic Shift → Apify.
- `APIFY_REDDIT_FIRST=true` changes order to Apify → free layers.
- Apify failure remains fail-open: the resolver raises only after all layers fail.

### 4. Wiring (`content/specialized_pipeline.py::_resolve_tier1`)
- Insert the X/Twitter resolver after Reddit and before YouTube.
- Reddit's Apify fallback is internal to `resolvers/reddit.py`, so existing routing remains stable.

### 5. Tests
- `tests/test_apify_resolvers.py`: status/profile parsing, mocked X rendering, missing-token/empty-result behavior.
- Reddit layer-order tests cover default free-first and `APIFY_REDDIT_FIRST=true`.
- Reddit mapping tests cover post/comment conversion into the existing renderer shape.

## Failure & cost matrix

| Condition | Behavior |
| --- | --- |
| Token unset | X resolver and Reddit Apify layer are inert; existing behavior unchanged |
| X actor broken/empty result | Tier-1 resolver fails open to Jina → curl_cffi → Crawl4AI → Camoufox → Wayback |
| Reddit free layers fail | Apify Layer 4 is attempted when configured |
| Credits exhausted (402/blocked) | Apify layer fails open; error is logged at debug level |
| Typical single-URL run | Approximately $0.0004–0.005, actor-dependent |

## Out of scope

- Instagram/TikTok/etc., bulk crawl modes, cookie-based actor auth, and live Apify calls in unit tests.

## Verification

1. `uv run pytest tests/test_apify_resolvers.py tests/test_reddit_unit.py -q` — **13 passed, 7 subtests**.
2. LSP diagnostics for all touched source/test files — **clean**.
3. `.env` contains the supplied `APIFY_API_TOKEN`; `.env` is gitignored and untracked.
