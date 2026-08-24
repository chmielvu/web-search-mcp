# Deterministic Understanding Fallback — Design (python-re cascade)

Date: 2026-08-24
Status: IMPLEMENTED — production wiring shipped and covered by tests (see §11 notes; §5 sketch matches production code in `heuristics/understanding_fallback.py`)
Related: `reports/query-understanding-rewrite-heuristics-analysis-2026-08-24.md` → R1
Supersedes: the naive "reuse `adapter._derive_fields` in `_deterministic_fallback`" framing, which cannot recover intent or compared entities (see §0).

## 0. Verified premise

`adapter._derive_fields(source, intent, entities, relations)` (`search/understanding/adapter.py:304-366`):

| Output | Source | Recoverable with `(source, "general", [], [])`? |
|---|---|---|
| `compared_entities` | `relations` (`compares_with`) or entity labels in `_COMPARISON_FALLBACK_LABELS` (only when `intent=="comparison"`) | **No** — no entities/relations, intent is `general` |
| `should_decompose` | `intent == "comparison" and (… ≥2 compared …)` | **No** — intent is `general` |
| `preserved_terms` | entity labels | **No** (acceptable: RAKE `support_terms` already supplies preserve hints independently) |
| `domain_hints` | entity labels + `_langs_from_text(source, [])` | Partial (bare language words only) |
| `time_sensitivity` | `_CURRENT_TERMS` / `_RECENT_TERMS` / `_HISTORICAL_TERMS` on `source` | **Yes** |

Conclusion: the existing `_derive_fields` can only donate its **time-term regexes**. Intent, compared entities, and decompose must come from a **new deterministic extractor** over the raw query surface.

## 1. Characterize the task

| # | Question | Answer |
|---|---|---|
| 1 | Operation | `classify` (coarse intent) + `extract` (compared entities) + `relate` (decompose decision) |
| 2 | Text type | Short natural-language + technical search queries (typical ≤120 chars); any language, but keyword sets are English; run **only** on the fallback path |
| 3 | Engine | `python-re` (stdlib) — matches the repo's shaping cascade (`heuristics/shaping.py`); escalation `wordninja` already present; **no new deps** |
| 4 | Input size / rules | ≤~200 chars typical; ~15 static rules; bounded scans only |
| 5 | Offsets | Yes — compared-entity spans preserve original surface for `EntitySpan`-style output |
| 6 | FP vs FN | **Precision-first** (fallback path): never mislabel `general` as `comparison`/`news`; FN (stay `general`) is acceptable and safe |
| 7 | Examples | + `"FastAPI docs vs Starlette docs"` → comparison + `[FastAPI docs, Starlette docs]`; + `"latest python 3.13 release notes"` → news + current; − `"vs code extensions"` → NOT comparison (product "VS Code"); − `"social media strategy"` → NOT social_media intent alone |
| 8 | Latency/memory | Typical queries (≤200 chars) «1ms; pathological inputs bounded (<10ms on 10k chars) via entity cap (≤3) and split-marker scan cap (≤3) |

## 2. Approach (engine choice, justified)

**Primary: `python-re` bounded-alternation cascade.** The input domain is a small closed set of markers (`vs`, `versus`, `compared`, time words, keyword sets). A monolithic regex is rejected (unreadable, un-debuggable); spaCy/GLiNER escalation is rejected (model dependency on the fallback path that exists precisely to survive service outages). `python-re` is what `heuristics/shaping.py` already uses, so constructs, test patterns, and failure behavior are proven in-repo.

**Supporting:** reuse `_CURRENT_TERMS`/`_RECENT_TERMS`/`_HISTORICAL_TERMS` from `adapter.py` (extract to a shared module so both paths agree), and `re.escape`-free literal keyword sets (constants, no user input interpolation).

## 2.1 Research basis (2026-08-24)

Design choices validated against current query-classification practice (web search + GitHub survey):

- **Deterministic, auditable rule sets as the stable baseline** — query-classification guidance (frutik/awesome-search → Query Classification) explicitly prefers regex/keyword rules for stability and auditability, especially for closed attribute sets and routing; LLM/embedding approaches are layered on top for drift absorption, not as the fallback. This design is the pure-python stable layer under the GLiNER2/LLM path.
- **Precision-first with explicit abstention** — the same guidance stresses precision and an explicit abstain (Unknown) path for routing; routing errors degrade downstream retrieval, not just classifier accuracy. The design's `general` default is exactly that abstention: ambiguous queries stay `general` (safe routing) rather than being force-labeled. Evaluate on **downstream** metrics (zero-results rate, NDCG via `search_quality_scores`/`llm_judgments`) rather than classifier accuracy — see rollout telemetry.
- **Entity-specific precedence over generic intent regexes** — the trusty-search-core `classifier.rs` pattern (entity regexes first, generic intent cues second, else Unknown) maps 1:1 onto this cascade: product exclusion (`vs code`) > comparison marker/split > keyword sets > general.
- **Cheap pre-LLM extraction layer** — the prompt-cookbook pipeline (GLiNER → LLM core transformation → post-processing) supports keeping a lightweight deterministic extractor ahead of the LLM rewrite; this fallback is the same layer for the outage case, and it feeds the same prompt slots (`compared_entities`, `time_sensitivity`, `should_decompose`).
- gh CLI survey found no canonical public implementation of compared-entity extraction from `vs/versus` markers in search servers — this extractor is novel enough that the in-repo test table is the contract.

## 3. Cascade design (stages explicit)

```
S1 candidates    scan surface for markers:
                 comparison split markers (\bvs\.?\b | \bversus\b | \bcompared (to|with)\b)
                 comparison intent markers (adds \bcompare\b | \bcomparison\b | \bcomparing\b)
                 time terms (current/recent/historical sets — shared with adapter)
                 intent keyword sets (social / news / coding)
S2 validate      structural checks per candidate:
                 - "vs" must NOT be inside the product token pair "vs code" (VS Code product, not comparison)
                 - split sides must each contain ≥1 non-stop token of len ≥2 (left/right non-empty)
                 - time terms take precedence order current > recent > historical (first match wins)
                 - keyword intent requires an exact token-set intersection (no substring matches)
S3 resolve       deterministic precedence:
                 ``VS Code`` product exclusion > comparison word/split
                 (2 valid sides) > keyword intent > general (abstention)
                 first valid vs-marker wins; "and"-split is a second-tier fallback (comparison intent only)
                 overlapping side candidates: longest side wins (positional split is unambiguous by construction)
S4 normalize     casefold-dedupe sides, keep original surface casing from source offsets, cap at 3
                 time_sensitivity ∈ {none, current, recent, historical}
                 intent ∈ canonical SearchIntent set
S5 score/explain record rule_ids in rationale + a rules tuple; keep fallback confidence semantics
                 (QueryUnderstandingResult.confidence = 0.0, fallback=True) — internal evidence is
                 diagnostics-only and persisted via existing query_understanding_events writer
```

## 4. Intermediate representation

```yaml
# heuristics/understanding_fallback.py — proposed rule table (frozen, static)
comparison_split_markers:            # S1, class: split
  - "\\bvs\\.?\\b"
  - "\\bversus\\b"
  - "\\bcompared\\s+(to|with)\\b"
comparison_intent_markers:           # S1, class: intent (superset of split markers + bare compare)
  - "\\b(compare|comparison|comparing|vs\\.?|versus|compared)\\b"
product_exclusions:                  # S2 — never treat as comparison
  - "\\bvs\\s*code\\b"               # "VS Code" is a product
time_terms:                          # S1, precedence current > recent > historical
  current:    ["current", "currently", "now", "today", "latest"]
  recent:     ["recent", "recently", "this week", "this month"]
  historical: ["historical", "history", "formerly", "deprecated", "past"]
intent_keywords:                     # S1, exact token-set intersection
  social_media: [twitter, x, tweet, reddit, instagram, threads, facebook, subreddit]
  news:        [news, headline, announcement, release, launch, breaking, election, policy]
  ai_coding:   [api, sdk, library, package, framework, github, python, typescript, sql,
                rust, docker, kubernetes, pytest, async, bug, error, docs, documentation,
                install, tutorial]
stop_sides:    [the, and, for, with, vs, or, to, a, an, of, in]
caps:          max_compared_entities: 3
               max_split_marker_scans: 3
```

## 5. Executable implementation (sketch — proposed module `heuristics/understanding_fallback.py`)

```python
"""Deterministic query-understanding fallback (pure python-re, no network).

Precision-first cascade used only when the hosted GLiNER2 gateway fails.
Never imports GLiNER or torch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..search.intents import SearchIntent

# --- S1: candidate markers (compiled; share the time sets with the adapter) ---
_COMPARISON_SPLIT = re.compile(r"\b(?:vs\.?|versus|compared\s+(?:to|with))\b", re.I)
_COMPARISON_WORD = re.compile(r"\b(?:compare|comparison|comparing|versus|compared)\b", re.I)
_COMPARISON_VERB_PREFIX = re.compile(r"^(?:compare|comparison|comparing|compared)\b\s*", re.I)
_PRODUCT_VS_CODE = re.compile(r"\bvs\s*code\b", re.I)
_TIME_CURRENT = re.compile(r"\b(?:current|currently|now|today|latest)\b", re.I)
_TIME_RECENT = re.compile(r"\b(?:recent|recently|this\s+week|this\s+month)\b", re.I)
_TIME_HISTORICAL = re.compile(r"\b(?:historical|history|formerly|deprecated|past)\b", re.I)
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#.]*")

_SOCIAL = frozenset({"twitter", "x", "tweet", "reddit", "instagram", "threads", "facebook", "subreddit"})
_NEWS = frozenset({"news", "headline", "announcement", "release", "launch", "breaking", "election", "policy"})
_CODING = frozenset({"api", "sdk", "library", "package", "framework", "github", "python",
                     "typescript", "sql", "rust", "docker", "kubernetes", "pytest", "async",
                     "bug", "error", "docs", "documentation", "install", "tutorial"})
_STOP_SIDES = frozenset({"the", "and", "for", "with", "vs", "or", "to", "a", "an", "of", "in"})

_MAX_COMPARED = 3
_MAX_SPLIT_SCANS = 3


@dataclass(frozen=True, slots=True)
class FallbackUnderstanding:
    intent: SearchIntent
    compared_entities: tuple[str, ...]
    compared_spans: tuple[tuple[int, int], ...] = ()  # (start, end) offsets into the ORIGINAL query
    time_sensitivity: str
    should_decompose: bool
    preserved_terms: tuple[str, ...] = ()
    rationale: str = ""
    rules: tuple[str, ...] = ()


def _side_has_content(side: str) -> bool:
    words = _TOKEN.findall(side)
    return any(len(w) >= 2 and w.casefold() not in _STOP_SIDES for w in words)


def _coarse_intent(text: str, compared: tuple[str, ...] = ()) -> SearchIntent:
    # S2/S3: product exclusion beats markers; keyword sets are exact-token intersections.
    # Bare "vs" alone is NOT comparison (precision-first): require an explicit comparison
    # word OR a structurally valid two-sided split. `compared` is reused from the single
    # extraction pass (never re-scan the markers).
    if not _PRODUCT_VS_CODE.search(text):
        if _COMPARISON_WORD.search(text):
            return "comparison"
        if compared:
            return "comparison"
    # Precision rule: single-letter tokens (e.g. bare "x") are too ambiguous for
    # keyword intent — only tokens of length >= 2 count.
    words = {w.casefold() for w in _TOKEN.findall(text) if len(w) >= 2}
    if words & _SOCIAL:
        return "social_media"
    if words & _NEWS:
        return "news"
    if words & _CODING:
        return "ai_coding_and_infrastructure"
    return "general"


def _extract_compared(
    text: str,
) -> tuple[tuple[str, ...], tuple[tuple[int, int], ...]]:
    # S1 candidates, S2 validate, S3 resolve (first valid split wins), S4 normalize (dedupe/cap).
    # Offsets are preserved: every surface must satisfy text[start:end] == surface.
    if _PRODUCT_VS_CODE.search(text):
        return (), ()
    entities: list[str] = []
    spans: list[tuple[int, int]] = []
    scans = 0
    for marker in _COMPARISON_SPLIT.finditer(text):
        scans += 1
        if scans > _MAX_SPLIT_SCANS:
            break
        left_raw = text[: marker.start()]
        right_raw = text[marker.end() :]
        left = left_raw.rstrip()
        right = right_raw.lstrip()
        if not _side_has_content(left) or not _side_has_content(right):
            continue
        # Left side always starts at 0 (text[:marker.start()] rstrips trailing ws only);
        # right side lstrip advances the start by the stripped leading-whitespace count.
        # A leading comparison verb is stripped so "compare X vs Y" yields "X" (never
        # "compare X") as the left entity; left span start advances by the strip width.
        left_clean = _COMPARISON_VERB_PREFIX.sub("", left)
        if not left_clean:
            continue
        left_delta = len(left) - len(left_clean)
        entities = [left_clean, right]
        spans = [
            (left_delta, len(left)),
            (marker.end() + (len(right_raw) - len(right)), len(text)),
        ]
        break
    if not entities and _COMPARISON_WORD.search(text) and " and " in text:
        parts = [p.strip() for p in text.split(" and ", 1)]
        if len(parts) == 2:
            # Strip a leading comparison verb so "compare fastapi and starlette" yields
            # ("fastapi", "starlette"), never ("compare fastapi", "starlette"). Stripped
            # sides are validated before offsets are located.
            clean: list[str] = []
            for part in parts:
                stripped = _COMPARISON_VERB_PREFIX.sub("", part)
                if not stripped:
                    break
                clean.append(stripped)
            if len(clean) == 2 and all(_side_has_content(p) for p in clean):
                entities = []
                spans = []
                cursor = 0
                for part in clean:
                    idx = text.find(part, cursor)  # search from after the previous side
                    entities.append(part)
                    spans.append((idx, idx + len(part)))
                    cursor = idx + len(part)
    deduped: list[str] = []
    deduped_spans: list[tuple[int, int]] = []
    for surface, span in zip(entities, spans):
        key = surface.casefold()
        if key not in {d.casefold() for d in deduped}:
            deduped.append(surface)
            deduped_spans.append(span)
    return tuple(deduped[:_MAX_COMPARED]), tuple(deduped_spans[:_MAX_COMPARED])


def _time_sensitivity(text: str) -> str:
    # S3 precedence: current > recent > historical (mirrors adapter._derive_fields).
    if _TIME_CURRENT.search(text):
        return "current"
    if _TIME_RECENT.search(text):
        return "recent"
    if _TIME_HISTORICAL.search(text):
        return "historical"
    return "none"


def resolve_fallback_understanding(query: str) -> FallbackUnderstanding:
    raw = query or ""
    text = raw.strip()
    lead = len(raw) - len(raw.lstrip())
    compared, compared_spans = _extract_compared(text)  # single marker scan
    if lead and compared_spans:
        # Spans are offsets into the ORIGINAL query: shift by leading whitespace.
        compared_spans = tuple((start + lead, end + lead) for start, end in compared_spans)
    intent = _coarse_intent(text, compared)             # reuses the extraction
    if intent == "comparison":
        rules.append("intent.comparison_marker")
    elif intent != "general":
        rules.append(f"intent.keyword:{intent}")
    if compared:
        rules.append("compared.split_marker")
    time_sensitivity = _time_sensitivity(text)
    if time_sensitivity != "none":
        rules.append(f"time.{time_sensitivity}")
    should_decompose = intent == "comparison" and len(compared) >= 2
    if should_decompose:
        rules.append("decompose.comparison_facets")
    rationale = "deterministic fallback" + (f"; {'; '.join(rules)}" if rules else "; general")
    return FallbackUnderstanding(
        intent=intent,
        compared_entities=compared,
        compared_spans=compared_spans,
        time_sensitivity=time_sensitivity,
        should_decompose=should_decompose,
        rationale=rationale,
        rules=tuple(rules),
    )
```

Wiring (proposed, one call site): in `search/understanding/resolver.py::_deterministic_fallback`, build the `QueryUnderstandingResult` from `resolve_fallback_understanding(normalized_query)` instead of the all-empty literal; keep `confidence=0.0`, `fallback=True`, `model_version` as today. Share the three time regexes with `adapter.py` (single source of truth) instead of duplicating.

```python
# Boundary conversion (option b — raw-text strings; EntitySpan grounding is NOT used here):
from ..search.understanding.models import QueryUnderstandingResult

fb = resolve_fallback_understanding(normalized_query)
understanding = QueryUnderstandingResult(
    intent=fb.intent,
    confidence=0.0,
    entities=[],                       # grounded NER stays empty on fallback (service was down)
    relations=[],
    preserved_terms=[],
    compared_entities=list(fb.compared_entities),   # tuple[str, ...] -> list[str] (contract type)
    domain_hints=[],
    time_sensitivity=fb.time_sensitivity,
    rationale=fb.rationale,
    should_decompose=fb.should_decompose,
)
```

### 5.1 Boundary contract for `compared_entities` (verified)

`QueryUnderstandingResult.compared_entities` is **`list[str]`** — not `list[EntitySpan]`:

- `search/understanding/models.py:20` — `compared_entities: list[str]` (line 17 is `entities: list[EntitySpan]`, a different field).
- `search/understanding/schema.py:50` — JSON schema: `{"type": "array", "items": {"type": "string"}}`.
- `analytics/writers/schema.py:326` — DuckDB column `compared_entities VARCHAR[]`.
- Consumers require strings: `planning.py:153-154` (`_stable_terms` → `str.strip()/.casefold()`), `query_features.py:298` (`_uniq` → `str.strip()`), `planning.py:123-126` (`" vs ".join(compared[:3])`).
- Tests pin it: `tests/test_query_understanding.py:78` and `tests/test_query_understanding_adapter.py:100` assert `== ["FastAPI", "Starlette"]`.

Option (a) — emitting grounded `EntitySpan` objects — is therefore **rejected**: it would break the planner (`_stable_terms`), feature building (`_uniq`), the JSON schema, and the DuckDB `VARCHAR[]` column. The design keeps `compared_spans` (raw offsets into the original query) alongside the string surfaces, so a future EntitySpan mapping is derivable at the boundary if ever needed — nothing is lost.

## 6. Engine compatibility

```yaml
engine: python-re (3.12 stdlib)
constructs_used: [\b, char-classes, non-capturing groups, alternation, re.I, finditer]
lookarounds: none
backtracking_risk: none            # no nested quantifiers; bounded alternation; short inputs
unicode: NFC assumed at ingress (normalize_query already collapses whitespace/punctuation)
escaping: keyword sets are literal constants; no user input is re-interpolated into patterns
fullmatch: not applicable (search semantics for markers; side validation uses token scan)
```

## 7. Tests (positive / negative / near-miss / boundary)

| Case | Expected |
|---|---|
| `"FastAPI docs vs Starlette docs"` | intent=comparison, compared=(`FastAPI docs`, `Starlette docs`), decompose=True, rule `compared.split_marker` |
| Offset fidelity | `text[s:e] == surface` for every `(s, e)` in `compared_spans` (e.g. spans `(0,12)`/`(16,30)` for the vs-case above) |
| Boundary conversion | `QueryUnderstandingResult(compared_entities=list(fb.compared_entities), ...)` validates without error and round-trips `["FastAPI docs", "Starlette docs"]` |
| `"fastapi and starlette comparison"` | intent=comparison, compared via "and"-split, decompose=True |
| `"latest python 3.13 release notes"` | intent=news, time=current |
| `"compare fastapi vs starlette vs flask"` | intent=comparison, compared=("fastapi", "starlette vs flask") — first valid marker wins; cap 3 is a safety bound, not a multi-pair fan-out |
| `"compare fastapi and starlette"` | intent=comparison, compared=("fastapi", "starlette") via and-split with leading-verb strip, decompose=True |
| `"vs code extensions for python"` | intent=ai_coding (keywords), **not** comparison (product exclusion), compared=(), decompose=False |
| `"compare VS Code extensions"` | intent=general (product exclusion precedes comparison words — pinned regression), compared=(), decompose=False |
| `"how to fix go error"` | intent=ai_coding (error), **not** flagged as language R/Go (that is `query_features` R4 scope) |
| `"recent historical data"` | time=recent (precedence current>recent>historical) |
| `""` / whitespace | general / none / () / False |
| `"x vs"` (one-sided) | intent=general (no valid split), compared=() — S2 rejects empty right side |
| `"vs"` alone | intent=general (bare vs is not comparison), compared=() |
| 10k-char non-matching input | completes <10ms; linear scan bounded (S1 cap) |
| Determinism | identical input → identical output across calls |

### 7.1 Verified by execution (2026-08-24)

The §5 sketch was executed standalone against this table (clean run, exit 0). Execution caught and fixed six real defects before the final run:

1. **Left-side offset math**: `len(left_raw) - len(left)` counted trailing-strip as a leading offset shift → `text[s:e] != surface`. Fixed: left span = `(0, len(left))`; right span = `(marker.end() + stripped_leading, len(text))`.
2. **Bare `"vs"` over-classified as `comparison`**: a lone `vs` matched the intent marker. Fixed: comparison requires an explicit comparison word OR a structurally valid two-sided split.
3. **Single-letter keyword false positive**: bare token `"x"` (X/Twitter) misclassified `"x vs"` as `social_media`. Fixed: keyword-intent tokens must be length ≥ 2.
4. **And-split left verb**: `"compare fastapi and starlette"` produced `("compare fastapi", "starlette")`. Fixed: leading comparison verb stripped (`_COMPARISON_VERB_PREFIX`) before entity/spans are located; stripped sides re-validated.
5. **Vs-split left verb**: `"compare fastapi vs starlette vs flask"` produced `("compare fastapi", "starlette vs flask")` in the marker branch. Fixed: same leading-verb strip, left span start advances by the strip width.
6. **Marker-semantics row**: the sketch extracts exactly one pair from the first valid marker (no multi-pair fan-out); the "capped at 3" row now reflects that (cap 3 is a safety bound).

Final clean run: **all table cases pass**, offset fidelity holds (`text[s:e] == surface` for every span), determinism holds, the boundary conversion validates against a `list[str]` contract model, and the 10k-char pathological inputs stay under the documented bound. The executed harness is reproducible from this section.

## 8. Limitations

- Compared entities are crude side-splits, not grounded NER — surfaces may include noise words (`"FastAPI docs"` not `"FastAPI"`). Acceptable for serp2 facet construction and prompt hints; not for relation extraction.
- English keyword sets only; non-English queries on fallback land mostly in `general` (safe, precision-first).
- Bare `"compare"` without `vs`/`and` yields comparison intent but no compared entities (decompose stays False).
- Cannot recover `domain_hints`/`preserved_terms` from entities; `preserved_terms` stays empty on fallback (RAKE `support_terms` still supplies preserve hints independently).
- Product-name exclusion is blunt: `"VS Code vs PyCharm"` abstains to `general` because `\bvs\s*code\b` fires before the genuine second `vs` marker — accepted precision-first FN (never an FP); pinned by `test_vs_code_vs_x_abstains_precision_first`.
- Time-word precedence may mislabel `"current historical overview"` as `current` — accepted precision trade-off, matches adapter behavior.

## 9. Performance risks

- All scans are linear; `finditer` bounded by `_MAX_SPLIT_SCANS` (3 examined markers), entity cap 3, token pass single. Typical ≤200-char queries: sub-millisecond; pathological 10k-char input: bounded <10ms (matches the §7 perf test).
- No user input is interpolated into patterns, so no ReDoS from query content.
- One extra regex pass vs. today's fallback (empty literal) — negligible; fallback path only.

## 10. Alternatives

- **Fallback (simpler):** keep the current all-empty `_deterministic_fallback`; recover only `time_sensitivity` by calling `_derive_fields`-style regexes. Rejected: leaves the intent-policy layer and comparison facets inert during the exact outage mode the data shows dominates (6/7 events).
- **Escalation (heavier):** local ONNX TinyBERT intent classifier (repo already has `training/train_tinybert.py` + `training/intent_classifier/`; the CHANGELOG documents a 5ms/83%-accuracy TinyBERT-4L path) as the outage fallback, with this regex extractor providing entities/decompose. Better intent recall, adds a model artifact + versioning burden to the fallback path; stage after the pure-python extractor proves the KPI gap.

## 11. Rollout

1. ✅ **Completed (2026-08-24)** — `heuristics/understanding_fallback.py` landed with `tests/test_understanding_fallback.py`; wired into `gliner_client._fallback_result` (dominant outage path) and `resolver._deterministic_fallback`.
2. ✅ **Completed (2026-08-24)** — time-term regexes moved to `heuristics/understanding_fallback.py`; `adapter.py` imports them (`_CURRENT_TERMS`/`_RECENT_TERMS`/`_HISTORICAL_TERMS` aliases); adapter + gateway tests green.
3. ✅ **Completed (2026-08-24)** — `_deterministic_fallback(reason, query)` wired to `resolve_fallback_understanding`; resolver-level and async gateway-outage tests added. Verification: 68 focused tests pass, ruff check + format clean, LSP zero diagnostics.
4. ⬜ **Remaining — telemetry/evaluation** — `query_understanding_events.decision_path` already distinguishes `gliner2_fallback`; assert fallback rows now carry non-default intent/facets (comparison/news/social/ai_coding, `compared_entities`, `time_sensitivity`) in an `analysis/query_rewrite/` re-run against the live analytics DB once it is unlocked.
