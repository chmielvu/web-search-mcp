# Final Audit: web-search-mcp v0.2

Audit timestamp: 2026-06-09T21:39:35+02:00

Scope:
- Validate the existing audit findings in `plans/TODO/v02-code-audit.md` and `plans/TODO/v02-code-audit-addendum.md`.
- Use `ccc` semantic search and `cgc` code graph analysis to review the live v0.2 implementation.
- Document dead code, stubs, incomplete implementations, and logic bugs.

Tools used:
- `ccc index` refreshed the semantic index: 345 files, 4,966 chunks, 0 errors.
- `ccc search` was used for query entities, entity extraction prompts, provider arguments, CLI pipeline calls, and incomplete scaffolding.
- `cgc stats` reported the graph database contains 626 files, 2,799 functions, 369 classes, and 1,121 modules.
- `cgc analyze dead-code`, `cgc analyze complexity`, `cgc analyze callers`, and `cgc find pattern` were used for structural evidence.
- Direct source reads and focused Python reproductions were used to confirm graph/semantic findings.

## Executive Summary

The v0.2 implementation is not clean enough to call final. The previous audits were directionally right, but a second pass found two additional live regressions with direct runtime impact:

1. `web-search-cli search web` is broken because the CLI service calls `run_search_pipeline()` without the required `diagnostics` keyword.
2. Profile `provider_arguments` are not actually usable in the live pipeline because `branch_executor.py` passes them to `search_instrumented.search_single_query()`, which does not accept that keyword. The branch executor catches the resulting `TypeError` and returns empty branch results.
3. The pre-search `query_entities` path is dead, and post-search entity extraction uses the query-understanding prompt instead of a dedicated entity extraction prompt.

The main v0.2 architecture is coherent, but several seams are half-wired: provider plan tests validate lower-level helpers, while live pipeline/CLI integration does not exercise those paths. That is the root pattern behind the highest-risk findings.

## Confirmed Findings

### P0: Native CLI `search web` crashes before running the pipeline

Evidence:
- `src/kindly_web_search_mcp_server/search/pipeline.py:45` requires `diagnostics: Diagnostics | None`.
- `src/kindly_web_search_mcp_server/cli/services/search_web.py:37` calls `run_search_pipeline(...)` without `diagnostics`.
- Reproduction:

```text
TypeError
run_search_pipeline() missing 1 required keyword-only argument: 'diagnostics'
```

Impact:
- `web-search-cli search web` cannot execute the v0.2 web search path.
- Existing CLI tests mock command service payloads and do not cover the real service-to-pipeline signature.

Fix TODO:
1. Add `diagnostics=None` in `cli/services/search_web.py`.
2. Add a CLI/service regression test that calls `fetch_web_search_payload()` with pipeline dependencies mocked, not just command-level payload mocks.
3. Run `pytest tests/cli/test_native_cli_phase2.py tests/cli/test_native_cli_phase3.py` plus a focused service test.

### P0: Profile provider arguments are wired into the plan but rejected by the live instrumented runner

Evidence:
- `src/kindly_web_search_mcp_server/search/pipeline.py:148` builds `provider_arguments` from `provider_plan.options.bundles`.
- `src/kindly_web_search_mcp_server/search/branch_executor.py:130` passes `provider_arguments=spec.provider_arguments` to the configured runner.
- `src/kindly_web_search_mcp_server/search/pipeline.py:19` imports the runner from `search_instrumented.py`.
- `src/kindly_web_search_mcp_server/search_instrumented.py:157` defines `search_single_query(...)` without a `provider_arguments` parameter.
- Focused reproduction returned an empty branch and logged:

```text
Branch search failed (index=0 type=original): search_single_query() got an unexpected keyword argument 'provider_arguments'
```

Impact:
- Any profile using `provider_arguments` can silently turn a branch into zero results.
- `tests/test_provider_plan.py` validates `_search_single_provider()` forwarding, but not the live `pipeline -> branch_executor -> search_instrumented.search_single_query` path.
- This contradicts the changelog statement that provider arguments are threaded into provider calls.

Fix TODO:
1. Add `provider_arguments: dict[str, dict[str, object]] | None = None` to `search_instrumented.search_single_query()`.
2. Pass provider-specific arguments from `search_instrumented.search_single_query()` into `_search_single_provider_instrumented()`.
3. Add the same argument to `_search_single_provider_instrumented()` and forward it to `_original_search_single_provider()`.
4. Add an integration-style unit test that uses `run_search_pipeline()` with a profile containing provider arguments and asserts the provider receives them.
5. Consider making branch execution fail loudly for internal signature errors, while still tolerating provider-level failures.

### P1: Pre-search `query_entities` is stale GLiNER plumbing and should be replaced by query understanding

Evidence:
- `src/kindly_web_search_mcp_server/server.py:850` creates `query_entities: list = []`.
- `src/kindly_web_search_mcp_server/server.py:973` passes that empty list into `run_web_search(...)`.
- `src/kindly_web_search_mcp_server/search/pipeline.py:54` accepts `query_entities`.
- `src/kindly_web_search_mcp_server/search/pipeline.py:57` immediately deletes it.
- `cgc find pattern query_entities` found only the server variable in the code graph; source reads show the pipeline parameter is not used.

Impact:
- Query-level entity extraction should come from `resolve_query_understanding()` now, not from the retired GLiNER seam.
- The current parameter is dead plumbing and should not remain in the API once call sites are aligned.
- The live query understanding path already supplies `understanding.entities`, so that should be the pre-search source of truth.

Fix TODO:
1. Wire pre-search entity handling to `resolve_query_understanding()` and treat `understanding.entities` as the entity source for v0.2.
2. Remove `query_entities` from `server.py`, `run_search_pipeline()`, and all call sites once the alignment is complete.
3. Add a regression test that asserts the search pipeline receives pre-search entities from query understanding, not GLiNER.

### P1: Search-result entity extraction uses the wrong prompt

Evidence:
- `src/kindly_web_search_mcp_server/search/entity_extractor.py:20` calls `build_prompt("query_understanding", ...)`.
- `src/kindly_web_search_mcp_server/prompts/query_understanding.py:18` asks the LLM to classify and annotate web search queries.
- `src/kindly_web_search_mcp_server/prompts/query_understanding.py:21` includes intent, confidence, preserved terms, time sensitivity, provider hints, rewrite hints, and rationale.
- `src/kindly_web_search_mcp_server/search/entity_extractor.py:39` then parses only `payload.get("entities", [])`.

Impact:
- Post-search entity extraction spends tokens on query classification fields it discards.
- The prompt is semantically mismatched for result title/snippet extraction.
- Accuracy is likely worse for short snippets because the model is asked to solve a broader classification task.

Fix TODO:
1. Add `prompts/entity_extraction.py` with a JSON-only schema for `{"entities": [...]}` if post-search enrichment stays enabled.
2. Register it in `prompts/registry.py`.
3. Update `search/entity_extractor.py` to call `build_prompt("entity_extraction", ...)` or otherwise trim the current prompt for snippet extraction.
4. Add a focused test that asserts the entity extractor requests the entity prompt, not the query-understanding prompt.

### P1: Profile provider names currently override caller-requested providers

Evidence:
- `src/kindly_web_search_mcp_server/search/provider_plan.py:29` starts with `profile.provider_names`.
- `src/kindly_web_search_mcp_server/search/provider_plan.py:30` only reads `context.providers` when the profile has no names.
- Current default profiles in `src/kindly_web_search_mcp_server/search/profiles/defaults.py:10` do not set `provider_names`, so this is latent for defaults.

Impact:
- A future profile that sets `provider_names` will ignore explicit user provider choices.
- This may be intentional if profiles are meant to own provider allow-lists. If so, the API contract should say that profile policy wins over caller `providers`.
- If caller `providers` is a user override, this is a logic bug.

Fix TODO:
1. Make the priority explicit in tests and docs.
2. Recommended behavior for agent-facing tools: caller `providers` should narrow or override profile defaults, not be silently ignored.
3. Add a regression test where `profile.provider_names=("brave",)` and `context.providers=("searxng",)` to lock the intended behavior.

### P2: Branch limiting silently drops variants

Evidence:
- `src/kindly_web_search_mcp_server/search/branch_executor.py:70` reads `settings.query_decomposition_max_branches`.
- `src/kindly_web_search_mcp_server/search/branch_executor.py:72` returns `branches[:max_branches]` without logging, metadata, or response diagnostics.

Impact:
- Rewrite/fan-out can generate more variants than will execute.
- Agents and evaluation traces cannot tell whether useful variants were dropped.

Fix TODO:
1. Emit an observability event when variants are truncated.
2. Add `dropped_branch_count` to branch metadata or pipeline plan event.
3. Add a unit test for truncation metadata.

### P2: `search/__init__.py` still violates the repository's modularity rule

Evidence:
- `src/kindly_web_search_mcp_server/search/__init__.py` is 486 lines.
- It contains provider registry initialization, `CircuitBreaker`, `ProviderBudget`, `_search_single_provider()`, `search_single_query()`, and `search_web()`.
- The AGENTS rule says code files should stay under 300 lines of code.
- `cgc analyze complexity --threshold 12` also flags search dispatch complexity in related live search paths.

Impact:
- The module is harder to test and review.
- The instrumented wrapper mirrors parts of this module, making signature drift more likely; the provider-arguments bug is a direct example.

Fix TODO:
1. Extract `CircuitBreaker` to `search/circuit_breaker.py`.
2. Extract `ProviderBudget` to `search/provider_budget.py`.
3. Extract provider registration to `search/provider_registry.py`.
4. Extract dispatch to `search/dispatcher.py`.
5. Keep `search/__init__.py` as thin re-exports only.

### P2: Stale CLI scaffolding remains after operational CLI implementation

Evidence:
- `ccc search` found `src/kindly_web_search_mcp_server/cli/errors.py:28`.
- `src/kindly_web_search_mcp_server/cli/errors.py:31` still says commands are "planned but not implemented in the scaffolding phase."
- `cgc analyze callers scaffold_error` found no callers.
- `src/kindly_web_search_mcp_server/cli/commands/scaffold.py:6` is a no-op registered from `cli/app.py:63`.

Impact:
- Not a live runtime failure, but it is stale DX surface.
- It can confuse future audits and users because the CLI now has operational command modules.

Fix TODO:
1. Remove `scaffold_error()` if no longer used.
2. Remove the no-op `commands/scaffold.py` registration unless there is a planned command group that needs it.
3. Update tests if any still assume scaffolding-phase language.

### P2: Query understanding/classifier router has no fallback

Evidence:
- `src/kindly_web_search_mcp_server/llm/router.py:48` returns `LLMRouter((build_classifier_endpoint(),))`.
- `src/kindly_web_search_mcp_server/llm/config.py:9` builds only the Vercel endpoint for classification.
- `src/kindly_web_search_mcp_server/llm/router.py:53` gives the worker path a multi-endpoint ladder.

Impact:
- Query understanding currently depends on a single Vercel AI Gateway endpoint.
- The intended correction is to reuse the LLM worker/liteLLM ladder first, then fall back to deterministic `general` classification if all LLM routes fail.
- Rewrite workers are resilient; the classifier path needs the same kind of fallback story.

Fix TODO:
1. Add classifier fallback endpoints that use the worker/liteLLM ladder.
2. Add a deterministic `general` classification fallback when all LLM routes fail.
3. Add tests proving query understanding falls back cleanly and returns `general` as the final degraded state.

### P3: Dead `asyncio.gather()` defensive branches remain

Evidence:
- `src/kindly_web_search_mcp_server/search/__init__.py:477` assigns `asyncio.gather(...)`.
- `src/kindly_web_search_mcp_server/search/__init__.py:478` checks `hasattr(free_results, "__await__")`.
- `src/kindly_web_search_mcp_server/search_instrumented.py:229` and `:265` repeat the same pattern.
- `asyncio.gather()` returns an awaitable future, so the `else` branch is dead.

Impact:
- Low runtime risk.
- The defensive branch adds noise in already complex dispatch code.

Fix TODO:
1. Replace the branch with direct `await asyncio.gather(...)`.
2. Keep exception handling through `return_exceptions=True`.

## Prior Audit Validation

| Prior audit claim | Verdict | Notes |
|---|---|---|
| `query_entities` is dead | Partially confirmed | Empty list is created in `server.py`, immediately deleted in `pipeline.py`; the intended replacement is LLM query understanding via `understanding.entities`. |
| `entity_extractor.py` uses the wrong prompt | Confirmed | Uses `query_understanding` prompt and discards all fields except `entities`. |
| Profile `provider_names` override user providers | Confirmed as latent/contract risk | Defaults do not trigger it, but code order makes profile names win. |
| `flow_observability.py` has no module docstring | Invalid/currently stale | Current file has a module docstring. |
| `pipeline_builders.py` uses bare `list` for entities | Confirmed | `entities: list` should be `list[EntitySpan]`. Low severity. |
| `diagnose_providers(providers)` names are ambiguous | Mostly informational | It diagnoses requested providers, while `providers_used` is computed separately. Rename would help clarity. |
| Branch limiting silently discards variants | Confirmed | No truncation event or metadata. |
| Inline `QueryVariant` import in `pipeline.py` | Confirmed but low | Works, but consistency cleanup is reasonable. |
| `rewrite_model = "disabled"` string sentinel | Confirmed but low | Could use `None` or `"__disabled__"` for clarity. |
| Env-var-only entity path is dead | Invalid as stated | `settings.entity_extraction_enabled` still reads `KINDLY_ENTITY_EXTRACTION_ENABLED`; the env path is not dead. |
| `asyncio.gather()` `hasattr("__await__")` branch is dead | Confirmed | Present in both search dispatch modules. |
| Profile system underutilized | Confirmed | Default profiles inherit from general with no provider names/options/arguments. |
| Classifier router is single-provider | Confirmed | Worker has fallback; classifier should be corrected to use the worker/liteLLM ladder and deterministic `general` fallback. |
| `repomix-output.*` committed at root | Invalid as stated | `git ls-files repomix-output.md repomix-output.xml .claude .kindly` returned nothing at root. However `src/kindly_web_search_mcp_server/repomix-output.md` exists and should be reviewed separately. |
| Merge shallow profile files | Optional | Do not do this before fixing runtime regressions; small files are less urgent than broken seams. |

## Dead Code and Stub Inventory

Actionable:
- `src/kindly_web_search_mcp_server/cli/errors.py:28` `scaffold_error()` has no callers.
- `src/kindly_web_search_mcp_server/cli/commands/scaffold.py:6` registers nothing but is still registered.
- `src/kindly_web_search_mcp_server/server.py:850` `query_entities` is dead.
- `src/kindly_web_search_mcp_server/search/pipeline.py:54` `query_entities` parameter is dead.
- `src/kindly_web_search_mcp_server/search/__init__.py:480` and `search_instrumented.py:232`/`:268` contain dead `asyncio.gather()` fallback branches.

Heuristic/noisy:
- `cgc analyze dead-code` returned many hits under `plans/research_repos/`, which are vendored/reference research code and should not be treated as production dead-code findings.
- `cgc analyze dead-code` also flagged Streamlit dashboard render functions. Those may be callback/entrypoint functions; review only if dashboard cleanup is in scope.

## Complexity Hotspots

`cgc analyze complexity --threshold 12` found these source-tree hotspots:
- `scrape/universal_html.py::fetch_html_via_nodriver` complexity 66.
- `scrape/nodriver_worker.py::_fetch_html` complexity 61.
- `content/github_discussions.py::render_discussion_thread_markdown` complexity 57.
- `search/searxng.py::search_searxng` complexity 56.
- `search/gemini_search_tool.py::gemini_search_with_grounding` complexity 45.
- `agent/runner.py::run_agentic_web_research` complexity 41.
- `rerank/core.py::rerank_results` complexity 35.
- `content/fetch_pipeline.py::fetch_content_artifact` complexity 34.
- `search/merge.py::merge_search_results` complexity 31.
- `search/pipeline.py::run_search_pipeline` complexity 29.

These are not all v0.2 blockers, but `run_search_pipeline`, `search/__init__.py`, `search_instrumented.py`, and `branch_executor.py` should be prioritized because they are already showing signature drift.

## Recommended Fix Order

1. Fix the CLI `diagnostics=None` regression and add a real `search web` service test.
2. Fix provider argument forwarding through `search_instrumented.py`; add an end-to-end pipeline test for provider arguments.
3. Wire pre-search entities to `resolve_query_understanding()` and remove the stale `query_entities` parameter once aligned.
4. Add a dedicated entity extraction prompt and test prompt selection.
5. Decide and test provider priority semantics: caller providers vs profile provider names.
6. Add truncation observability for branch limiting.
7. Remove stale CLI scaffold/no-op files.
8. Refactor `search/__init__.py` into focused modules to get under the 300-line rule and reduce future drift.
9. Add classifier fallback through the worker/liteLLM ladder and finish with deterministic `general` classification.
10. Clean low-risk dead branches and type hints.

## Suggested Verification After Fixes

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_provider_plan.py tests/test_search_orchestrator.py tests/test_entity_response_fields.py tests/cli/test_native_cli_phase2.py tests/cli/test_native_cli_phase3.py -q
.\.venv\Scripts\python.exe -m ruff check src/kindly_web_search_mcp_server/search src/kindly_web_search_mcp_server/cli tests/test_provider_plan.py tests/test_search_orchestrator.py tests/test_entity_response_fields.py tests/cli
```

Add at least these new tests before claiming the fixes complete:
- CLI service calls `run_search_pipeline(..., diagnostics=None, ...)`.
- Live pipeline forwards profile `provider_arguments` through `search_instrumented.py`.
- Entity extractor uses an `entity_extraction` prompt.
- Provider priority behavior is explicit when both profile and caller providers are set.
- Branch truncation emits metadata or observability.

## Correction Pass Status

Implemented in this repository state:
- `web-search-cli search web` now passes `diagnostics=None`.
- The live instrumented provider runner now accepts and forwards `provider_arguments`.
- `query_entities` was removed from the live search pipeline.
- Query understanding now degrades to the worker/LLM ladder and then to a conservative `general` fallback when the classifier path fails.
- Search-result entity extraction now uses a dedicated `entity_extraction` prompt.

Still open as follow-up cleanup:
- Make the `provider_names` override contract explicit and test it.
- Add truncation observability for branch limiting.
- Split the large `search/__init__.py` dispatcher module when you want the modularity cleanup.
- Remove stale CLI scaffold/no-op code if you want the CLI surface fully de-noised.
- Replace the dead `asyncio.gather()` defensive branches when you touch the search dispatch module next.
