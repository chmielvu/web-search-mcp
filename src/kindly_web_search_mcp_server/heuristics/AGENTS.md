<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: keep sections and order; edit content, not structure -->
<!-- Last updated: 2026-08-21 | Last verified: 2026-08-21 -->

# AGENTS.md - Heuristics

Stdlib-first query repair, provider dialect augmentation, and cause-aware guidance helpers.

## Key Files

| File | Role |
|---|---|
| `text_clean.py` | `repair_unicode`, `clean_query`, `clean_text_for_llm` |
| `query_features.py` | `QueryFeatures`, `build_query_features`, shared repo/lang regexes |
| `shaping.py` | Role-dialect cascade: `extract_search_ops`, `shape_for_branch`, `AugmentResult` |
| `guidance_messages.py` | Empty/gap/shaping guidance strings for middleware |

## Rules

- No network I/O. Pure functions only.
- `normalize_query` delegates to `clean_query` — do not fork whitespace semantics.
- Role-dialect shaping is keyed on BRANCH ROLE and runs at the retrieve boundary (`retrieval._call_provider`) via `shape_for_branch`; providers are never dispatch keys.
- Public-code dialect shapers (GitHub/Sourcegraph/GitLab) were removed with their providers; public code search is served by the dedicated `code_search` tool, which compiles provider syntax natively in `tools/code_search/query.py`.
- Shaping metadata is diagnostic-only and is persisted with provider request metadata; the cleaned query remains the adapter input.
- Shared repo/org/user regexes live in `query_features.py`; `shape_for_branch` consumes them via `QueryFeatures`.
- Public response may echo `intent` + `query_shaping`; full diagnostics stay internal.
- `ftfy` is preferred for mojibake; `repair_unicode` no-ops if import fails.

## Testing

```bash
uv run pytest tests/test_heuristics_text_clean.py tests/test_heuristics_shaping.py -q
uv run pytest tests/test_agent_steering_middleware.py tests/test_public_output_serialization.py -q
```
