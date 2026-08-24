# Exa web_search adapter — capability + intent-wiring proposal

Date: 2026-08-24
Scope: `web_search` provider path only. The `code_search` Exa adapter
(`src/kindly_web_search_mcp_server/tools/code_search/exa.py`, `/context`
endpoint, type=code semantics) is deliberately untouched — the two adapters
stay separate.

## 1. Findings (what already exists)

**An Exa web_search adapter exists and is registered.**
`src/kindly_web_search_mcp_server/search/providers/exa.py` implements
`search_exa(query, num_results, search_options, http_client, **kwargs)`:

- POSTs `https://api.exa.ai/search` with `{"query", "numResults" (cap 100),
  "type": "fast", "contents": {"highlights": True}}`.
- Maps `SearchOptions.site_filters` → `includeDomains` (≤1200) and
  `domain_filters` → `excludeDomains` (≤1200).
- Passes through kwargs restricted to `_EXA_ARGUMENT_KEYS =
  {"type", "category", "userLocation", "moderation"}`.
- Snippet = `highlights` joined → `summary` → `text`, truncated to 4000 chars.
- Uses `run_provider()` (catalog: `max_retries=1`, `cooldown_seconds=10`,
  requires `EXA_API_KEY`); error classes `ExaError` / `ExaConfigError`.
- Registered in `search/provider_catalog.py` and `search/provider_registry.py`;
  asserted by `tests/test_provider_registry.py`.

**The intent system already wires Exa at the planner level.**
- `search/contracts.py`: `BranchRole.SEMANTIC_EXA = "semantic_exa"`.
- `search/planning.py`: `_SEMANTIC_EXA_CANDIDATES = ("exa",)`; the 5-variant
  rewrite pipeline has a dedicated `semantic_exa` slot; deterministic fallback
  shapes the query as `"<query> authoritative sources[: goal]"`. Exa receives
  an LLM semantic rewrite — aligned with Exa's natural-language strengths.
- `search/provider_call.py::build_provider_call_kwargs` passes intent-policy
  `provider_arguments` wholesale because `search_exa` accepts `**kwargs` — no
  plumbing change needed to add per-intent Exa arguments.

**The gap: intent policy never tunes Exa.**
`search/intent_policy.py` defines provider arguments per intent for
`brightdata` / `tavily` / `ddg` (and `brave_news` + `tavily` with
`freshness="week"` for `news`). No intent sets an `"exa"` entry. The
`IntentSearchPolicy.freshness` field exists but is only surfaced through
`provider_arguments` of other providers — never translated for Exa date
filters.

## 2. Exa capability facts (docs, 2026-08-24)

Sources: `exa.ai/docs/reference/search`, `search-api-guide-for-coding-agents`,
`search-best-practices`, `contents-api-guide-for-coding-agents`,
`contents-best-practices`, `faqs`, `sdks/cheat-sheet`, `agent-api/examples`.

| Capability | Facts | Use in web_search |
|---|---|---|
| `type` | `instant` (~250ms) · `fast` (~450ms) · `auto` (~1s, default, recommended) · `deep-lite` (4s) · `deep` (4–15s) · `deep-reasoning` (12–40s). Legacy `neural` deprecated. | `fast` is a sound interactive default; upgrade to `auto` per intent for quality. Deep modes are out of scope (see §5). |
| `category` | `company` · `publication` · `news` · `personal site` · `financial report` · `people` (other strings accepted as hints). `company`/`people` reject `startPublishedDate`, `endPublishedDate`, `excludeDomains` → 400. | Map intents: `news`→`news`, `digital_humanities`→`publication`, `social_media`→`personal site` (blogs). |
| `startPublishedDate` / `endPublishedDate` | ISO 8601. `IntentSearchPolicy.freshness` (day/week/month/year) maps naturally to `startPublishedDate`. | Add `freshness` translation (mirrors `translate_brave_freshness` pattern). |
| `contents.highlights` | Recommended for agent workflows; ~10x token efficiency. `{query, maxCharacters}` optional. | Already used — keep as the default content mode. |
| `contents.text` / `summary` | Full markdown (`maxCharacters` cap) / LLM summary. | Not needed for web_search candidates (pipeline has its own content pipeline + snippet caps). Keep out unless a research path needs it. |
| `contents.maxAgeHours` | `0` always livecrawl · `-1` cache-only · omit recommended (livecrawl as fallback). Pair with `livecrawlTimeout` (10–15s). | Default omit; allow opt-in `maxAgeHours: 0` for freshness-critical news via kwargs. |
| `moderation` | Bool; filters unsafe content. | Consider default True for a public MCP server (behavior change — flag it). |
| `outputSchema` / `systemPrompt` / `additionalQueries` / `stream` | Synthesized output; `outputSchema` adds ~2s to any type; grounding/citations returned automatically in `output.grounding`. | Out of scope — overlaps pipeline's own synthesis/rerank and deep_research tool. Keep pass-through allowlist for future opt-in only. |
| `userLocation` | Two-letter ISO country code. | Keep passthrough; optionally map from a future geo setting. |
| Response | `results[]` with `title, url, id, publishedDate, author, image, favicon, text, highlights, highlightScores, summary, subpages, extras`; top-level `output{content, grounding}`, `costDollars.total`, `requestId`. **No top-level `score` field anymore** — current `raw_score = item.get("score")` is always None. | Keep tolerant mapping; note `raw_score` is vestigial. |
| `/contents` endpoint | Top-level `text/highlights/summary` (NOT nested in `contents`); per-URL `statuses` must be checked; HTTP 200 even on per-URL failures. Deprecated params (`useAutoprompt`, `numSentences`, `highlightsPerUrl`, `livecrawl`, `tokensNum`, `stream`) must not be used. | Out of scope for web_search (belongs to the content pipeline); noted as future work. |

## 3. Recommended design (phased, additive)

### Phase 1 — Intent wiring + freshness translation (smallest, highest value)

No signature change beyond one new optional kwarg.

1. **`search/providers/exa.py`**
   - Accept `freshness: str | None = None` in `search_exa` kwargs and translate
     to `startPublishedDate` (ISO 8601, UTC now minus day/week/month/year).
     Reuse the value grammar of `translate_brave_freshness`
     (`search/providers/brave_common.py`) for consistency: pass through
     `day|week|month|year`; reject unknown values with `ExaError` (Brave
     raises on invalid — match that contract).
   - Add `startPublishedDate` / `endPublishedDate` / `maxAgeHours` /
     `livecrawlTimeout` to the kwargs allowlist (keep `type`, `category`,
     `userLocation`, `moderation`).
   - Keep `type: "fast"` as the module default; intent policy overrides it.

2. **`search/intent_policy.py`** — add an `"exa"` entry per intent:

   | Intent | `exa` provider_arguments | Rationale |
   |---|---|---|
   | `general` | `{"type": "auto"}` | Docs-recommended default; semantic strength. |
   | `ai_coding_and_infrastructure` | `{"type": "auto"}` | Semantic retrieval for docs/blog posts; highlights keep tokens low. |
   | `digital_humanities` | `{"type": "auto", "category": "publication"}` | Scholarly publications (papers, preprints, journals). |
   | `comparison` | `{"type": "auto"}` | Neural/semantic strengths for vs.-style queries. |
   | `social_media` | `{"type": "auto", "category": "personal site"}` | Blogs/personal pages (Exa's stated strength). |
   | `news` | `{"type": "auto", "category": "news", "freshness": "week"}` | Specialized news index + date filter (news supports date filters — safe from the company/people 400 rule). |

   `freshness` for `news` is already set at the policy level (`freshness="week"`)
   and duplicated per provider today; passing it through `provider_arguments`
   for Exa matches the existing brave_news/tavily pattern exactly.

3. **Tests**
   - `tests/test_intent_policy.py`: assert per-intent Exa arguments
     (`news` → category news + freshness week; `digital_humanities` →
     publication; `general` → type auto).
   - New `tests/test_exa_providers.py` (httpx `MockTransport`): freshness →
     `startPublishedDate` mapping, allowlist passthrough, include/exclude
     domains, numResults cap, snippet fallback, 400/429/5xx error contract.

### Phase 2 — Adapter hardening (small, safe)

1. `moderation` — decide one of:
   - **Recommended**: default `True` in the adapter payload (public server,
     filters unsafe content; cost-neutral). Behavior change — call out in
     CHANGELOG.
   - Conservative alternative: leave default off, add opt-in via intent
     policy `"exa": {"moderation": True}`.
2. `raw_score` — keep the tolerant `item.get("score")` mapping (harmless,
   backward-compatible with older API responses) but stop documenting it as a
   real signal; the current API returns `highlightScores` per highlight, not a
   result score.
3. Diagnostics — optionally log `requestId` and `costDollars.total` at debug
   via `ProviderRequestMetadata` extras (see code_search adapter's
   `_response_details` pattern). Purely additive; not required for behavior.
4. Auth header — keep `x-api-key` (documented; both `x-api-key` and
   `Authorization: Bearer` are accepted by the API).

### Phase 3 — Opt-in deep synthesis (future work, NOT in default path)

- `deep-lite`/`deep`/`deep-reasoning` (4–40s) overlap the pipeline's own LLM
  synthesis, rerank, and the existing `deep_research` tool. Do not wire them
  into interactive `web_search`.
- If ever wanted: gate behind a settings flag (e.g.
  `settings.exa_deep_enabled`) and a per-intent opt-in
  `"exa": {"type": "deep-lite"}` — never a default.
- `outputSchema`/`systemPrompt`/`additionalQueries`/`stream` remain on the
  pass-through allowlist only; no policy sets them.

### Out of scope (noted, not proposed here)

- Exa `/contents` as a content-pipeline resolver (the repo already has
  `get_content`/`batch_get_content` with Jina/others). A future adapter would
  use top-level `text`/`highlights`/`summary` + `statuses` handling — a
  separate change.
- Reusing the `code_search` Exa adapter for web_search — explicitly kept
  separate per task constraint.

## 4. Payload examples

News intent (Phase 1):

```json
{
  "query": "<semantic_exa rewrite>",
  "numResults": 15,
  "type": "auto",
  "category": "news",
  "startPublishedDate": "2026-08-17T00:00:00.000Z",
  "contents": {"highlights": true}
}
```

Digital humanities intent (Phase 1):

```json
{
  "query": "<semantic_exa rewrite>",
  "numResults": 15,
  "type": "auto",
  "category": "publication",
  "contents": {"highlights": true}
}
```

Freshness-critical news with forced livecrawl (Phase 2 opt-in):

```json
{
  "query": "...",
  "type": "auto",
  "category": "news",
  "moderation": true,
  "contents": {"highlights": true, "maxAgeHours": 0},
  "livecrawlTimeout": 12000
}
```

## 5. Risks & mitigations

| Risk | Mitigation |
|---|---|
| `company`/`people` categories + date filters/excludeDomains → HTTP 400 | Only map `news`, `publication`, `personal site` (all support date filters + excludeDomains). Never emit `startPublishedDate` for company/people. |
| `auto` (~1s) vs `fast` (~450ms) latency per branch | `fast` stays the module default; only quality-critical intents upgrade. Exa is one of 6 branches run concurrently — bounded impact. |
| Intent-policy behavior change (type/category) alters live results | Additive per-intent tuning; existing tests pin the registry; run the targeted pytest set + optional live smoke with `EXA_API_KEY`. |
| `moderation` default change | Call out in CHANGELOG; or keep opt-in (conservative path). |
| Double synthesis if deep modes ever wired | Excluded from default path; deep synthesis owned by `deep_research` tool. |
| Deprecated params leaking in (`useAutoprompt`, `numSentences`, …) | Allowlist only known-good params; reject unknown kwargs (`ExaError`) so stale payloads fail loudly, mirroring the `/contents` "common mistakes" list. |

## 6. Verification

1. `uv run pytest tests/test_provider_registry.py tests/test_intent_policy.py tests/test_exa_providers.py` (new file).
2. Existing suite spot-check: `uv run pytest tests/test_provider_resilience.py tests/test_retrieval_budget.py`.
3. Optional live smoke (only if `EXA_API_KEY` is set): one `web_search` call
   with `news` intent, assert `category=news` payload + non-empty highlights.
4. CHANGELOG entry noting the intent-wiring + any moderation default change.

## 7. Files touched

- `src/kindly_web_search_mcp_server/search/providers/exa.py` — freshness
  translation, allowlist expansion, optional moderation default, debug
  diagnostics.
- `src/kindly_web_search_mcp_server/search/intent_policy.py` — per-intent
  `"exa"` provider_arguments.
- `tests/test_exa_providers.py` — new adapter tests.
- `tests/test_intent_policy.py` — Exa policy assertions.
- `CHANGELOG.md` — behavior notes.

No changes needed: `search/planning.py` (branch wiring exists),
`search/provider_call.py` (`**kwargs` flow already delivers policy args),
`search/provider_catalog.py` (retry/cooldown fine),
`search/providers/base.py` (error/retry contract reused).
