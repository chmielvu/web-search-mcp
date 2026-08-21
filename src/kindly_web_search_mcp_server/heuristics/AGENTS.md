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
| `augment.py` | `augment_query_for_provider`, `specialized_fallback_query` |
| `guidance_messages.py` | Empty/gap/shaping guidance strings for middleware |

## Rules

- No network I/O. Pure functions only.
- `normalize_query` delegates to `clean_query` — do not fork whitespace semantics.
- Specialized dialect shaping runs at retrieve boundary (`retrieval._call_provider`), not inside each provider.
- GitHub removes unsupported Sourcegraph qualifiers and wildcard repository selectors; Sourcegraph exposes literal/regexp mode separately; GitLab strips web-search operators and retains only a project hint.
- Shaping metadata is diagnostic-only and is persisted with provider request metadata; the cleaned query remains the adapter input.
- Shared repo/org/user regexes live in `query_features.py`; github imports them.
- Public response may echo `intent` + `query_shaping`; full diagnostics stay internal.
- `ftfy` is preferred for mojibake; `repair_unicode` no-ops if import fails.

## Testing

```bash
uv run pytest tests/test_heuristics_text_clean.py tests/test_heuristics_augment.py -q
uv run pytest tests/test_agent_steering_middleware.py tests/test_public_output_serialization.py -q
```
