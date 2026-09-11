# Implementation Proposal: wordninja Segmentation + lingua Language Detection

Status: PROPOSAL (not implemented). Dependency additions to `pyproject.toml`
require explicit approval per AGENTS.md Ask-First rules — see §6.

Evidence base: `analysis/generalization_validation/{corpus.py, techniques.py,
eval_local.py, eval_live_ddgs.py, live_ddgs_results.json}` (105-query
multi-domain ground-truth corpus; live DDG A/B runs).

## 1. Library facts (verified against upstream READMEs)

### wordninja (`keredson/wordninja`, PyPI `wordninja` 2.0.0)
- API: `wordninja.split(str) -> list[str]`; also `split_with_locs(str)`.
- Model: English Wikipedia unigram frequencies + DP (Viterbi-style) split.
- Custom models supported: `wordninja.LanguageModel('lang.txt.gz')`,
  one word per line, decreasing probability; can override
  `wordninja.DEFAULT_LANGUAGE_MODEL`.
- Performance: ~41 µs/call for short strings (author timeit: 0.409 s / 10k).
- Pure Python, zero transitive deps.

### lingua-py (`pemistahl/lingua-py`, PyPI `lingua-language-detector` 2.2.0)
- Since 2.0: Rust core with PyO3 bindings; thread-safe; FST-backed models
  (few dozen MB resident). Requires **Python >= 3.12** (project runs 3.12.7 ✓).
- Build-once API:
  ```python
  from lingua import Language, LanguageDetectorBuilder
  detector = (
      LanguageDetectorBuilder.from_languages(*languages)
      .with_preloaded_language_models()   # eager load; avoids cold-path latency
      .build()
  )
  lang = detector.detect_language_of(text)   # Language | None
  iso = lang.iso_code_639_1.name.lower()     # 'en', 'pl', ...
  ```
- README guidance adopted: restrict the candidate language set (accuracy +
  speed); avoid low-accuracy mode for texts < 120 chars (queries qualify);
  `None` return = unreliable detection (treat as "unknown", don't guess).

### Real-world usage patterns found (GitHub code search, this session)
- Query tokenization for retrieval: `metildachee/persrv`
  `refinement.py::tokenise_english_query` = `" ".join(wordninja.split(text))`.
- Title/tag humanizing with camelCase pre-check then wordninja fallback:
  `Michaelunkai/study--Dev_Toolchain .../apps/o.py::normalize_game_title`.
- Hashtag-segmentation research benchmarks: `ruanchaves/hashformers`.
- lingua restricted-set detector singletons: `nextprocurement/RAG_tool`
  `src/acronyms/acronym_expander.py` ("the less you choose the faster"),
  `elsevierlabs-os/build-ltr-models-using-llm` `src/query_sampler.py`,
  `AppleIpx/live_chat` validators, `NeonGeckoCom/neon-lang-plugin-linguapy`.

## 2. Empirical justification (from this session's validation)

| Signal | Result |
|---|---|
| wordninja exact-match on glued cases | 9/9 (100%), 0.25 ms avg |
| Live DDG A/B | glued `toplawyersinnewyork`/`buyusedcarnearme`/`nearbycoffeeshops` returned squatted-domain junk; segmented forms returned Justia/Best Lawyers, Cars.com/Edmunds, Yelp |
| lingua accuracy (105-query corpus) | 91.4% vs langdetect 79.0% / langid 75.2%; ~4 ms/call warm |
| Overcorrection guardrail finding | symspell-style blind correction harmful (33.3% flip rate) → segmentation is preferred over spell-correction for this failure class |

Honest scope limits:
- Production `specialized`-branch zero-result queries are well-formed
  multi-word sentences — segmentation does NOT address that failure.
  Empirically confirmed: running `wordninja.split()` over every token of the
  longest real zero-result query (364-char NetworkX sentence) yields ZERO
  genuine splits — of 10 alpha tokens ≥10 chars, all are already dictionary
  words; the only output differences are punctuation artifacts
  (`co-occurrence` → `co occurrence`, trailing commas stripped).
  Root cause there (verified this session): `BranchRole.SPECIALIZED`
  providers come straight from intent-policy subscriptions
  (`search/planning.py:297`, `:431` ← `search/intent_policy.py:34-48`);
  `general`/`comparison`/`digital_humanities` subscribe ZERO providers
  (guaranteed no-op branch), and for `ai_coding_and_infrastructure`,
  `gitlab` fails 401 Unauthorized on 7/7 calls while github/hackernews/
  reddit/sourcegraph return success-but-empty (their actual wire queries
  are NOT recorded — `provider_calls.request_query` is empty for all
  specialized rows — so per-provider truncation/reshaping vs re-routing
  needs adapter-level instrumentation; open follow-up).
- lingua adds ~4 ms warm to the feature-extraction path vs the <2 ms
  aspiration for the pure-regex core. Reconciled by phasing (§7): Phase 1
  ships segmentation only (~41 µs/token author-measured; 0.25 ms/call in
  our harness — inside budget); Phase 2 gates lingua behind its flag and,
  if adopted, moves detection off the synchronous hot path (cached per-
  query-prefix or async pre-compute alongside the existing understanding
  merge), never blocking the sub-ms regex core. Flags default off either way.


## 3. Design decisions

### D1 — Segmentation hook point: additive variant, not mutation (Option B)
Rejected mutating `clean_query()` (Option A): it is the shared ingress for
LLM-bound content paths too, and normalization must stay lossless.
Rejected augment-only wiring (Option C): variants would be invisible to
planning/analytics.

**Selected:** new `src/kindly_web_search_mcp_server/heuristics/text_segment.py`;
called from `build_query_features()` right after `cleaned = clean_query(raw)`
(query_features.py:285). Output stored additively:
```python
# QueryFeatures gains two fields:
segmented_variants: tuple[str, ...] = ()   # e.g. ("top lawyers in new york",)
lang: str = ""                             # "" = unknown/disabled
```
`cleaned` remains byte-identical → zero regression surface for every existing
consumer; `augment_query_for_provider()` may prefer `segmented_variants[0]`
for keyword-dialect providers (github/sourcegraph/hackernews/reddit) behind
its own rule check.

### D2 — Ordering: detect → (gate) → segment
Language detection runs FIRST on the full query; segmentation runs only when
`lang == "en"` (or detection returned None AND an eligible token ≥ 12 chars —
conservative fallback, since a lone glued token both breaks detection and is
the exact case segmentation fixes).
Rationale (measured): multi-word queries detect reliably despite one glued
token; splitting Polish/German text with an English unigram model produces
garbage; the chicken-and-egg resolves conservatively (worst case: no
segmentation, never a wrong-language split).

### D3 — Trigger gating (which tokens may be split)
A whitespace token is eligible iff ALL hold:
1. `tok.isalpha()` and `len(tok) >= QUERY_SEGMENTATION_MIN_TOKEN_LEN` (default 10)
2. `tok == tok.lower()` (camelCase belongs to `_CAMEL` handling, not wordninja)
3. no `_` (snake_case → `_SNAKE`), no `.` (domains/dotted idents → `_DOTTED_IDENT`),
   no digits (version/model numbers like `iphone15vs...` stay intact)
4. token does not start with an operator prefix (`repo:`, `lang:`, `language:`,
   `path:`, `file:`, `site:`) and is not inside a quoted phrase span
Splitting rewrites ONLY the eligible token in place; everything else passes
through unchanged. Variant emitted only when the split changed something.

### D4 — Rollout: flags off by default, silent no-op without deps
Follows the `entity_extraction_enabled` / `rerank_entity_overlap_enabled`
measured-rollout precedent in settings.py:
```python
query_segmentation_enabled: bool = os.environ.get("QUERY_SEGMENTATION_ENABLED", "false").lower() == "true"
query_segmentation_min_token_len: int = int(os.environ.get("QUERY_SEGMENTATION_MIN_TOKEN_LEN", "10"))
lang_detect_enabled: bool = os.environ.get("LANG_DETECT_ENABLED", "false").lower() == "true"
lang_detect_languages: tuple[str, ...] = _parse_csv_env(os.environ.get("LANG_DETECT_LANGUAGES", "en,pl,de,es,fr"))
```
Optional imports guarded exactly like the `ftfy` precedent in
`repair_unicode()` (text_clean.py:35-44): `try: import wordninja / from lingua
import ... except Exception: mark unavailable`. With flags off OR deps absent,
behavior is byte-identical to today. Detector singleton built lazily on first
enabled call, preloaded, reused (lingua is thread-safe per README).

Telemetry: append `"segmented.glued"` / `"lang.detected:<iso>"` /
`"lang.unavailable"` to `QueryFeatures.notes` → flows into existing analytics
payloads for A/B measurement before any default flip.

## 4. Concrete changes

```
pyproject.toml                          # (Ask-First, §6): +wordninja, +lingua-language-detector
src/.../heuristics/text_segment.py      # NEW ~70 lines: _is_eligible_token(), segment_tokens(), segment_query()
src/.../heuristics/lang_detect.py       # NEW ~50 lines: singleton builder, detect_lang(iso lower) -> str
src/.../heuristics/query_features.py    # +2 dataclass fields; +8 lines in build_query_features(); +notes
src/.../heuristics/__init__.py          # lazy __getattr__ exports (existing pattern)
src/.../settings.py                     # +4 flag fields (§D4)
tests/test_heuristics_text_segment.py   # NEW: eligibility gates, en-gate, no-op-without-deps, idempotence
tests/test_heuristics_lang_detect.py    # NEW: restricted set, None-handling, flag-off passthrough
```

Sketch — `text_segment.py` core:
```python
def segment_query(text: str, *, min_len: int) -> str | None:
    """Return query with eligible glued tokens split, or None if unchanged."""
    if wordninja is None or not text:
        return None
    out, changed = [], False
    for tok in text.split():
        if _is_eligible(tok, min_len):
            parts = wordninja.split(tok)
            if len(parts) > 1:
                out.append(" ".join(parts)); changed = True; continue
        out.append(tok)
    return " ".join(out) if changed else None

def _is_eligible(tok: str, min_len: int) -> bool:
    core = tok.strip("\"'")
    return (
        len(core) >= min_len and core.isalpha() and core == core.lower()
        and "_" not in core and "." not in core
        and not core.startswith(("repo:", "lang:", "language:", "path:", "file:", "site:"))
    )
```

Sketch — `build_query_features()` insertion (after line 285):
```python
lang = ""
if settings.lang_detect_enabled:
    lang = detect_lang_iso(cleaned)          # "" when unknown/unavailable
    if lang: notes.append(f"lang.detected:{lang}")
    elif lang is None: notes.append("lang.unknown")
if settings.query_segmentation_enabled and (lang == "en" or not cleaned_has_spaces(cleaned)):
    seg = segment_query(cleaned, min_len=settings.query_segmentation_min_token_len)
    if seg: segmented_variants = (seg,); notes.append("segmented.glued")
```

## 5. Test plan (acceptance)

1. `segment_query("toplawyersinnewyork") == "top lawyers in new york"`; identity on
   `duckdb`, `read_only`, `site:example.com`, `iPhone15VS`, `best.lawyers.nyc`,
   `snake_case_token`, short tokens.
2. Non-English gate: Polish query never produces a variant.
3. Flag-off / dep-missing: outputs byte-identical to current behavior.
4. `detect_lang_iso("najlepsza restauracja w warszawie") == "pl"`;
   `""`-or-`None` handling on symbols-only input.
5. Full existing suite green (no consumer of QueryFeatures changes shape).

## 6. Approval gate (AGENTS.md Ask-First)

Code lands inert (flags default false; imports guarded), so the repo works
without the new deps. Adding `wordninja` and `lingua-language-detector` to
`pyproject.toml` is a dependency change requiring explicit sign-off —
user previously chose "document only". This proposal is that documentation;
do not edit pyproject.toml until approved.

## 7. Two-track fix ranking (distinct problems, distinct fixes)

| Track | Failure class | Highest-ROI fix | Status |
|---|---|---|---|
| A: production `specialized` branch | Empty provider subscriptions for `general`/`comparison`/`digital_humanities`; GitLab 401; success-but-empty from code hosts on long queries (wire query unrecorded) | (1) skip emitting the SPECIALIZED branch when its provider list is empty (`planning.py:297/431` guard); (2) fix or drop the GitLab credential; (3) instrument `provider_calls.request_query`, then decide truncation/operator-injection vs re-routing per adapter | Separate workstream — NOT this proposal |
| B: general glued-token queries | Concatenated searchstrings return domain-squatting junk (live-DDG proven) | This proposal: wordninja Phase 1 → lingua Phase 2, behind flags | Designed here |

Track A is higher production impact (658 guaranteed-zero branch executions
in the telemetry window); Track B is the generalization win validated on
the synthetic corpus + live DDG. They share no mechanism.

## 8. v2 addendum — implemented as breaking / non-gated (user directive)

User approved deps and overrode the flag-based rollout: **breaking,
non-gated, no backward compatibility**. Deviations from §3/§4 as shipped:

- **No settings flags.** `QUERY_SEGMENTATION_ENABLED`, `LANG_DETECT_ENABLED`,
  `LANG_DETECT_LANGUAGES`, `QUERY_SEGMENTATION_MIN_TOKEN_LEN` all dropped.
  Language set is a module constant in `lang_detect._SUPPORTED`
  (en/pl/de/es/fr); threshold is `text_segment.MIN_TOKEN_LEN = 10`.
- **Unconditional imports** — wordninja/lingua are hard deps
  (`pyproject.toml:55-56`, uv.lock updated via `uv add`; missing dep fails at
  import, no try/except shim).
- **Detector calibrated, not naive**: short keyword-style English queries
  produce confidently-wrong lingua picks ('async context manager python' → de
  @0.43/0.13 margin). `detect_lang()` therefore requires top confidence
  ≥0.70 AND runner-up margin ≥0.40 (probe-calibrated; true detections ≥0.75/
  0.63), else returns ''. Callers treat '' as unknown.
- **Segmentation gate**: runs when lang == en OR lang == '' (ambiguous).
  Confident non-English queries are never segmented.
- **augment.py consumption (the breaking part)**:
  `_body_base()` prefers `segmented_variants[0]` for the five code-host
  dialects (rule `segment.glued`); `augment_query_for_provider` returns a
  clean passthrough with rule `skip.non_english` for confident non-English
  queries instead of code-operator shaping;
  `specialized_fallback_query()` prefers the variant too.
- **QueryFeatures**: new trailing defaulted fields `lang: str = ""`,
  `segmented_variants: tuple[str, ...] = ()`; notes gain
  `lang.detected:<iso>` / `lang.unknown` / `segmented.glued`. Only one
  construction site existed (query_features.py), so no other callers break.

Verification: heuristics suite + search-path tests green (37 heuristic tests
incl. 3 new files; full run 1358 passed). The 13 remaining suite failures are
pre-existing/environmental and outside this diff's blast radius — verified by
consumer grep (recommendation/llm_router/serpapi/intent_policy import no
heuristics surface) and standalone passes (cli/test_runtime 4/4); fitz/pytz
are absent-optional-deps issues predating this work. ruff clean on all
touched files.
