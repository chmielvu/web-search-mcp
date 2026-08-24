# Role-Dialect Query Shaping — Final Design (2026-08-23)

Status: PROPOSED (refines the "heuristics expansion" draft; supersedes its cleanup section).
Scope: rewrite seam (`search/planning.py`), shaping seam (`search/retrieval.py`, `heuristics/`),
prompt ownership (`prompts/query_rewrite.py`), and total removal of the specialized-provider legacy.

---

## 0. Verified premises (this repo, today)

| Claim | Evidence |
|---|---|
| Positional 5-string JSON; one bad field → all five fall back | `_RewriteQueries.queries: list[str]` (planning.py:41-42); `len<5 → raise → queries=fallback` (:385-406); `len==4` append hack (:248-249) |
| Shaping keyed on provider ∈ {hackernews, reddit} — no branch assigns them → dead | `SPECIALIZED_AUGMENT_PROVIDERS` gate (retrieval.py:129); candidate tuples planning.py:33-38 contain neither |
| Stale specialized guidance injected into Exa slot | `<SPECIALIZED_QUERY_RULES>` block (:138-142) + `_SPECIALIZED_REWRITE_GUIDANCE` HN/Reddit/Telegram text (:188-203) |
| `specialized_fallback_query` dead in live code | Only consumers: tests + `heuristics/__init__` re-exports |
| GLiNER fields computed but unused in live path | `adapter._derive_fields` fills `compared_entities/time_sensitivity/should_decompose`; consumers = analytics/training only |
| wordninja already wired once | `text_segment.segment_query` → additive `QueryFeatures.segmented_variants`; consumed by deleted-on-removal shapers only via `_body_base` |
| Subscription machinery gates nothing relevant | `_DEFAULT_INTENT_PROVIDER_SUBSCRIPTIONS` = {telegram, reddit, hackernews, brave_news}; **candidates ∩ subscriptions = ∅** ⇒ passing `()` at planning.py:285 is behavior-identical ✓ |
| `specialized=True` catalog flags: 6 defs, zero branch assignments | jina, grok_xai, hackernews, reddit, telegram, brave_news (provider_catalog.py:214-281); `brave` itself is NOT flagged |
| Flagged adapters' other consumers | `grok_search` (cli/services/ai.py, tools/ai_search.py), `telegram_client.get_telethon_client` (content/resolvers/telegram.py) — both independent of the unrouted `search_*` wrappers |

Note: `tree_of_thoughts` was unavailable this session (session-model effort mismatch); the
axis-by-axis branch evaluation below was performed manually with explicit prune rationale.

---

## 1. Chosen method and why

**Primary:** pure-Python cascade over `python-re` (bounded alternation scanner), composed with two
existing assets — **wordninja** (glued-token segmentation, already a pinned dep) and the **GLiNER2
gateway results** already purchased by the single `/v2/query-understanding` call.

Why not the alternatives:
- spaCy Matcher / DependencyMatcher: adds a model dependency for what is operator-token syntax,
  not linguistic structure. Wrong cost class.
- FlashText/pyahocorasick: dictionaries of fixed phrases — we match parameterized operators
  (`site:<domain>`), not a static lexicon.
- LLM-side shaping (ask the rewriter to emit final per-provider queries): non-deterministic, and the
  failure mode we are deleting is precisely "LLM output shape drifts → whole plan falls back".

Dialect is keyed on **branch role**, never on provider name — that is the structural fix for the
dead provider-keyed path.

## 2. Cascade design (all five stages named)

```
S1 candidates   extract_search_ops(query)      bounded re scan → SearchOps spans (≤16)
S2 validate     per-class structural checks    site:/filetype:/ext: payloads, offset reproduction,
                                               preserved_terms overlap guard (GLiNER-grounded)
S3 resolve      longest span per class;        deterministic tiebreak (start, end, rule_id);
                cross-class coexistence        user-authored ops always outrank derived ones (n/a here:
                                               every op in a rewritten query is author-authored)
S4 normalize    parallel views                 original · cleaned · segmented(wordninja); technical
                (never destructive)            literals preserved (C++, node.js, __init__, dotted idents)
S5 score/explain AugmentResult                 rules_applied + metadata tuples; role, stripped ops,
                                               budget trims, lang gate — persisted via existing writers
```

Top-level guard (before S1): `features.lang not in ("", "en")` → return cleaned/segmented view only,
rule `skip.non_english`. Generalized from the old HN/Reddit-only gate to **every** role (live
behavior for the five routed branches is unchanged; the rule tag becomes visible).

## 3. Intermediate representation

```yaml
# heuristics/shaping.py — compiled table, one entry per rewritten slot
- role: FREE                       # ddg, qdrant, searxng, degoog
  ops_strip: [site, filetype, exclude, engine_only]   # phrases kept verbatim
  ops_allowed: []
  word_budget: 12                  # llm-query-expansion style, enforced post-segmentation
  body: segmented_variant || cleaned
  fallback_terms: yake_top4
- role: SERP_KEYWORD               # shared base for serp1(brave) + serp2(google-canonical)
  ops_allowed: [phrase, site, filetype, exclude]      # LCD set (repo audit: brave/brightdata/serper)
  ops_strip: [intitle, inbody, inpage, lang, loc, plus_term, boolean]
  structured_time: true            # freshness NEVER in text; flows via policy/options plumbing
  brave_bound: {chars: 400, words: 50}
- role: SEMANTIC_TAVILY
  ops_strip: [all]
  style: answer_question           # natural sentence + research goal
  time_gate: time_sensitivity != none
- role: SEMANTIC_EXA
  ops_strip: [all]
  style: evidence_request          # replaces deleted specialized guidance
  compared_facet: compared_entities >= 2
```

Rule IDs: `OP.PHRASE OP.SITE OP.FTYPE OP.EXCL OP.ENGINE STRIP.<class> BUDGET.TRIM SEGMENT.GLUED SKIP.NON_ENGLISH`.

## 4. Design decisions (one per axis, with pruned alternatives)

### D1 — wordninja gets three real jobs (was: one decorative variant)
1. **Features variant** (exists): `QueryFeatures.segmented_variants`.
2. **Fallback bodies**: `FREE`/`SERP` deterministic fallbacks prefer the segmented form — this is
   where `specialized_fallback_query`'s capability moves when it dies (lang-gate test's
   `"toplawyersinnewyork" → "top lawyers in new york"` case ports to `branch_fallback_queries`).
3. **Rewrite-output repair**: planning normalizes each rewritten slot through a new
   `normalize_branch_query(q)` = `normalize_query(segment_query(q) ?? q)` — additive `SEGMENT.GLUED`
   rule recorded when changed. Scoped to the five rewritten slots only.
   *Pruned:* hooking segmentation inside global `normalize_query` — it would silently alter
   original-query normalization, relevance_query, and cache/analytics strings. Blast radius rejected.
4. Hardening: `MAX_TOKEN_LEN = 40` upper bound in `text_segment` so a pathological whitespace-free
   run can't trigger unbounded DP (perf guard, cheap).

### D2 — GLiNER: consume what we already paid for (zero new inference)
The single `/v2/query-understanding` call already yields four unused-in-live-path fields.
- **Prompt**: `_rewrite_queries` gains `compared_entities`, `time_sensitivity`, `should_decompose`
  (+ `preserved_terms` as an exact-preserve list). Per-slot instruction blocks consume them:
  serp2 splits into per-entity facets when `should_decompose`; temporal wording gated on
  `time_sensitivity ∈ {recent, current}`.
- **Shaping**: extend `QueryFeatures` with the three fields (same `getattr` fail-open pattern it
  already uses for intent/preserved/domain_hints). `shape_for_branch` validation rejects stripping
  inside a `preserved_terms` span unless the op is being removed wholesale (engine-only class).
- **Structured time, not text**: `time_sensitivity` maps onto the EXISTING structured plumbing
  (`IntentSearchPolicy.freshness` / `apply_search_options`) instead of baking `{current_year}` into
  SERP text. Adapter-side enum mapping (serper `tbs`, Brave `freshness`, Tavily `time_range`,
  Exa date range) is an explicitly listed non-goal for THIS change set — the contract point is the
  prompt instruction + policy field.
*Pruned:* calling GLiNER `/ner` on rewritten outputs — violates the one-request rule and adds
~latency to the hottest path for marginal gain.

### D3 — Zero legacy: delete, don't shim
New module **`heuristics/shaping.py`** (name aligns with existing `query_shaping` diagnostics;
the draft's `role_dialect.py` title is equivalent). `heuristics/augment.py` is **deleted outright** —
a rename makes any stale import fail loudly at import time instead of silently preserving old
semantics. Public surface after:

```python
SearchOps, SearchOpSpan, extract_search_ops(query) -> SearchOps
RoleDialect (frozen IR table), shape_for_branch(role, query, features) -> AugmentResult
branch_fallback_queries(...) -> tuple[str, ...]   # 5 slots, deterministic
AugmentResult                                     # unchanged shape (query, changed, rules_applied, metadata)
```

**Deletion manifest** (each row verified above):

| Delete | Where |
|---|---|
| `SPECIALIZED_AUGMENT_PROVIDERS`, `_augment_hackernews`, `_augment_reddit`, `augment_query_for_provider`, `_GH_CODE_MARKERS`, `_CODE_OPS`, `_body_base`, `_has_qualifier`, `_strip_code_ops`, `specialized_fallback_query`, `_specialized_fallback_query`, `features_for_query` | heuristics/augment.py (file removed) |
| augment exports in `heuristics/__init__.py` (TYPE_CHECKING block + lazy loader + `__all__` rows) + AGENTS.md row | heuristics/__init__.py, heuristics/AGENTS.md |
| `{specialized_guidance}` slot, `<SPECIALIZED_QUERY_RULES>` block, `_DEFAULT_SPECIALIZED_GUIDANCE`, `_SPECIALIZED_REWRITE_GUIDANCE` | planning.py |
| Re-export shim importing private planning names | prompts/query_rewrite.py (module inverted: templates MOVE here) |
| `_RewriteQueries` positional model, `len==4` append hack, `len<5` all-or-nothing raise, `q0..q4` unpack | planning.py |
| Inline fallback tuple (:304-326) | planning.py → `branch_fallback_queries` |
| `_DEFAULT_INTENT_PROVIDER_SUBSCRIPTIONS`, `_INTENT_SUBSCRIPTIONS`, `get_subscribed_specialized_providers`, `register_provider_subscription`, `IntentSearchPolicy.specialized_providers` field | intent_policy.py |
| `select_provider_names(specialized)` argument + second selection loop → `select_provider_names()` | provider_registry.py |
| `ProviderDefinition.specialized` flag + six flagged definitions (**jina, grok_xai, hackernews, reddit, telegram, brave_news**) | provider_catalog.py |
| Unrouted adapter wrappers: `search_jina`, `search_grok_xai`, `search_hackernews`, `search_reddit`, `search_telegram`, `search_brave_news` | providers/*.py — KEEP modules `providers/grok.py` (`grok_search` has live CLI/tool consumers) and `providers/telegram_client.py` (content resolver); delete `jina.py`, `hackernews.py`, `reddit.py`, `brave_news.py` after the import-check below |
| `getattr(definition, "per_call_timeout_seconds", None)` compat fallback comment path | retrieval.py:104-107 — catalog is typed now; read the attribute directly |
| Tests: subscription tests in `test_intent_policy.py`; positional-schema asserts in `test_query_rewrite_5variants.py`; provider-keyed asserts in `test_heuristics_augment*.py` | rewritten, not dual-pathed |

Pre-delete verification commands (run before removing module files):
```bash
uv run python -c "import ast,glob;[print(f) for f in glob.glob('src/**/*.py',recursive=True) for n in ast.walk(ast.parse(open(f,encoding='utf-8').read())) if isinstance(n,(ast.Import,ast.ImportFrom)) and 'providers.jina' in ast.dump(n)+ast.dump(n)]"
grep -rn "providers\.jina\|providers\.hackernews\|providers\.reddit\|providers\.brave_news\|search_grok_xai\|search_telegram\b" src --include=*.py   # expect: catalog only
grep -rn "pattern_type" src --include=*.py   # retrieval.py:138-139 propagation dies with the shapers; confirm qdrant adapter doesn't require it
```

*Pruned:* keeping the six definitions "unrouted but available" — that is exactly the dead weight
being removed; `brightdata_bing/yandex` are likewise unrouted but NOT part of the specialized
concept (out of mandate; noted as optional follow-up, not touched here).

### D4 — Named-slot rewrite contract
```python
class RewrittenQueries(ContractModel):
    free: str
    serp1: str
    serp2: str
    semantic_tavily: str
    semantic_exa: str
```
- `complete_json(response_model=RewrittenQueries, ...)` — Pydantic enforces keys; no positional
  indexing anywhere.
- Per-slot sanitize: blank/whitespace slot → **that slot alone** takes its deterministic fallback;
  the other four survive. Deletes the all-five-fallback cliff and the `len==4` hack.
- Cache key: `f"v{REWRITE_PROMPT_VERSION}:{sha256(user_content)}"`; `REWRITE_PROMPT_VERSION`
  lives beside the templates in prompts/query_rewrite.py.
- Prompt ownership inversion: `_REWRITE_SYSTEM/_REWRITE_USER` move INTO prompts/query_rewrite.py as
  `REWRITE_SYSTEM`, `REWRITE_USER`, plus a `SLOT_BLOCKS: dict[SlotId, str]` of slot-granular
  instruction blocks (QueryGym-ensemble idea at slot level): FREE block adopts llm-query-expansion
  wording ("additional keywords for each key aspect, ≤N words"); SERP blocks enumerate the LCD
  operators; TAVILY block answer-style; EXA block evidence/source-quality phrasing. planning imports
  them — private-name leakage dies.

### D5 — Deterministic fallbacks per slot (`branch_fallback_queries`)
Inputs: `normalized_query, terms(YAKE≤4), suggestions, research_goal, understanding`.

| Slot | Derivation |
|---|---|
| free | segmented variant ‖ keyword_query (= base + YAKE additions, unchanged math) |
| serp1 | autosuggest-derived `brave_fallback` (moved verbatim from planning) |
| serp2 | `"{a} vs {b}"` facet when `len(compared_entities) ≥ 2` (first two, order-stable) else keyword_query — guarantees distinct-from-serp1 without relying on comments (:316-319) |
| semantic_tavily | answer-style: `"{normalized_query}? {research_goal}"` cleaned; year marker iff `time_sensitivity ∈ {recent,current}` |
| semantic_exa | evidence-style: `"{normalized_query} — authoritative sources: {research_goal}"`; adds comparison framing when compared_entities ≥ 2; same time gate |

## 5. Wiring (end state)

- `retrieval._call_provider`: unconditional
  `aug = shape_for_branch(branch.role.value, query, features)`; drop the
  `SPECIALIZED_AUGMENT_PROVIDERS` branch, the `else: clean_query` arm, and the `pattern_type`
  propagation (:138-139). `query_shaping` + `query_transform_rows` writers untouched.
- `planning.plan_search`: `available = select_provider_names()`; fallback tuple built by
  `branch_fallback_queries(...)`; rewrite resolution loop iterates named slots.
- `intent_policy.resolve_intent_policy`: loses the subscriptions line (:128-129).

## 6. Engine compatibility

```yaml
engine: python-re (3.12 stdlib)
constructs_used: [alternation, \b, char_classes, non_capturing_groups, re.I]
lookarounds: none           # exclude-sign detected by inspecting preceding char, RE2-friendly habit
backtracking_risk: none     # no nested quantifiers; bounded alternation; linear scan capped ≤16 hits
unicode: NFC input assumed; matching on casefolded view; surfaces kept verbatim
fullmatch_use: site/filetype payload validators use re.fullmatch
escaping: operator keywords are literal constants; domain/ext payloads validated, never re-interpolated
```

## 7. Tests (rewrite, no dual paths)

| File | Covers |
|---|---|
| `tests/test_heuristics_shaping.py` (replaces test_heuristics_augment*.py) | positives (FREE strips site:, SERP keeps phrase+site), near-miss (`filetype:` w/o ext token rejected), confounder (`"site: docs"` inside quoted phrase untouched), boundary (op at start/end, adjacent punctuation), overlap (exclude sign inside phrase → phrase wins, longest-span policy), unicode (NFC accents), determinism, long-input failure perf (10k-char non-matching query under budget), round-trip (offsets reproduce `text[s:e]`) |
| `tests/test_heuristics_shaping_lang_gate.py` | ported skip.non_english cases against `shape_for_branch` for all roles; segmented-fallback case moves to fallback tests |
| `tests/test_query_rewrite_named_slots.py` (replaces test_query_rewrite_5variants.py) | schema keys; per-slot degradation (mock one empty slot → only it falls back); prompt-block assertions: no `<SPECIALIZED_QUERY_RULES>`, no "Hacker News"/"Reddit"/"Telegram" strings anywhere under prompts/, EXA evidence block present, FREE budget wording present |
| `tests/test_planning_fallbacks.py` | slot table of §4-D5 incl. compared_entities facet + time-gate |
| `test_intent_policy.py` | subscriptions tests deleted; assert `select_provider_names()` includes brave when env stubbed |
| `test_search_branch_assignments.py` / analytics tests | update expected rule tags (`clean.query` → `role.*` family) and named-slot persistence |

Zero-legacy gate (CI-able):
```bash
grep -rnE "SPECIALIZED_|specialized_fallback|specialized_providers|register_provider_subscription|augment_query_for_provider|SPECIALIZED_AUGMENT_PROVIDERS|_REWRITE_USER\b|queries\"\s*:" src --include=*.py   # expect: no hits (last pattern scoped to planning rewrite models)
grep -rn "Hacker News\|Telegram discussions" src/kindly_web_search_mcp_server/prompts src/kindly_web_search_mcp_server/search/planning.py   # expect: none
```

## 8. Expected limitations

- LCD means user-authored `intitle:`/`inbody:` on SERP slots is stripped by design — visible in
  `rules_applied` (STRIP.ENGINE), not silent.
- wordninja is English-Wikipedia-trained; non-English runs hit `skip.non_english` first, ambiguous
  (`''`) runs rely on wordninja leaving real words unchanged (already true today).
- GLiNER gateway down → understanding fails open to defaults → serp2 facet degrades to
  keyword_query. Identical to today (fields currently unused); strictly no regression.
- Brave LLM Context operator tolerance is undocumented for some LCD members; they're harmless if
  ignored upstream, and `brave_bound` caps stay.

## 9. Performance risks

- Regex scan: linear, bounded alternation, candidate cap 16 — failure cost dominated by input scan.
- wordninja DP: bounded by new MAX_TOKEN_LEN=40.
- Rewrite path latency unchanged (same single LLM call, same single GLiNER call); cache versioning
  causes a one-time cold cache.

## 10. Alternatives

- **Fallback (simpler):** keep `clean_query`-only shaping for every provider and just delete the
  dead code — recovers hygiene, forfeits per-role dialects and the GLiNER/wordninja payoff.
- **Escalation (heavier):** spaCy Matcher op grammar or GLiNER-based operator extraction, and
  adapter-side freshness mapping in the same change — rejected for scope/latency; revisit if LCD
  strip-rate telemetry shows real loss.

## 11. Rollout order

1. Add `heuristics/shaping.py` + tests (pure, no callers yet) → green.
2. Flip planning (named slots, prompt inversion, fallbacks, understanding passthrough) + rewrite tests.
3. Flip retrieval to `shape_for_branch`; delete augment.py; update `__init__`/AGENTS docs.
4. Delete subscription machinery + catalog flags/definitions + unrouted wrappers (import-check first).
5. Full gate: `uv run ruff check src tests && uv run pytest tests -q` + zero-legacy greps.
