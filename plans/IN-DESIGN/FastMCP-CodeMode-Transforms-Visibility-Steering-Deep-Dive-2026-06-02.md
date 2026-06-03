# FastMCP Advanced/Experimental Capabilities Deep Research: CodeMode, Transforms, Visibility, Middleware, Sampling, Elicitation, Tasks

**Date**: 2026-06-02  
**Researcher**: Grok Build subagent (focused task)  
**Scope**: CodeMode (https://gofastmcp.com/servers/transforms/code-mode) + all listed transforms (code-mode, namespace, prompts-as-tools, resources-as-tools, tool-search, tool-transformation), Visibility (enable/disable, tags, per-session), Middleware patterns for steering, Sampling, Elicitation, Tasks, and related for reducing tool overload in rich servers (15+ tools: search+fetch+synthesis+agentic like kindly-web-search-mcp).  
**Focus per query**: 1. Fetch/analyze pages. 2. Exact CodeMode mechanics. 3. Applicability review for this project (vs/in addition to profiles+ToolSearch; agents write Python orchestrations on primitives). 4. Current project usage (middleware yes, transforms no). 5. Compare to plans/Done/FastMCP-client-steering-plan.md + recent obs plans. 6. Practical recs + code-ready diffs for server.py (wiring, primitives, sandbox, middleware integration, tags). 7. Risks. 8. How combo addresses "many tools not used" + agentic hyperfocus.

**Sources**:
- Direct web_fetch on https://gofastmcp.com/servers/transforms/code-mode , https://gofastmcp.com/llms.txt (index), /servers/transforms/transforms.md, /tool-search.md, /prompts-as-tools.md, /resources-as-tools.md, /namespace.md, /tool-transformation.md, /servers/visibility.md, /servers/middleware.md, /servers/sampling.md, /servers/elicitation.md, /servers/tasks.md.
- web_search for citations (e.g. [web:2] for CodeMode page).
- Installed fastmcp 3.2.4 source in .venv (exact APIs for CodeMode, transforms, Visibility, etc.).
- Project code: server.py (core), src/kindly_web_search_mcp_server/middleware/* (expensive, rate, guidance, session), mcp_compat.py (homegrown), agent/mcp.py, settings.py, pyproject.toml, uv.lock.
- Plans: plans/Done/FastMCP-client-steering-plan.md (recommends PromptsAsTools/ResourcesAsTools/metadata/chaining/progress; CodeMode opt-in separate), plans/observability/aggregated-findings-recommendations-fastmcp-rerank-tool-strategy-2026-06-03.md (P0 profiles via tags+Visibility, ToolSearch, etc.), other obs/steering plans.
- Docs: docs/MCP_EVALUATION..., AGENTS.md/CLAUDE.md (current focus), CHANGELOG.md (recent subagent FastMCP notes already in Unreleased).
- No prior CodeMode/Transforms usage in active code (only doc mentions + query rewrite test data).

**Key Project Stats (from code)**: ~16 tools (web_search/get_content/batch_get_content/discover_links/gemini_search/perplexity_search/grok_search/quick_web_search/academic_search/youtube_* /agentic_web_research/composio_*/analytics_*), 4 prompts (plan/evaluate/gap/suggest with tags), 3 resources (status://providers/features, docs://workflow), heavy custom middleware for steering (expensive protection with session "think first", dynamic result-aware guidance injecting agent_guidance/suggested_next_tools/suggested_prompts, differentiated rate limits), Context used for info/progress in long tools, mcp_compat.py exists but *unused* (manual prompt/resource->tool registration), no server.transforms, no Visibility, tags only on prompts, no Sampling/Elicitation/Tasks used (though background possible), FastMCP 3.2.4 resolved (pyproject >=2.0.0), instructions already rich on routing.

---

## 1. Fetched/Analyzed Pages Summary + Validation

All pages fetched via web_fetch (validated against installed source v3.2.4 + web_search [web:0][web:1][web:2]).

- **CodeMode (main)**: Experimental (v3.1.0+ "Code to Joy"). Replaces full catalog with meta-tools for on-demand discovery + sandboxed Python orchestration. Requires `fastmcp[code-mode]` for MontySandboxProvider (pydantic-monty). Installed source confirms: experimental/transforms/code_mode.py with CodeMode(CatalogTransform), MontySandboxProvider, Search/GetSchemas/GetTags/ListTools factories. Default discovery: Search + GetSchemas.
- **Transforms Overview**: Pipeline Provider -> [Provider Transforms] -> [Server Transforms] -> Client. list_* pure fn, get_* middleware w/ call_next. Builtins: Namespace, ToolTransform, Visibility (also in /visibility), ToolSearch (search/), ResourcesAsTools, PromptsAsTools, CodeMode (experimental), Tool Transformation.
- **Tool Search**: Replaces list_tools with search_tools + call_tool (proxy). Regex or BM25. always_visible pins, max_results, custom names. Respects visibility/auth. BM25 uses in-mem index (lazy rebuild on catalog change).
- **PromptsAsTools / ResourcesAsTools**: Bridge for tool-only clients. Create list_prompts/get_prompt + list_resources/read_resource. Route *through server* so middleware/visibility apply. Note in docs: apply to FastMCP instance (not raw Provider).
- **Namespace / ToolTransform**: Prefixing and schema mods (rename, hide args, transform_fn w/ forward()).
- **Visibility**: mcp.enable/disable(keys/tags, only=True), per-session via ctx.enable_components/disable_components/reset_visibility() (sends list_changed notifs only to session). Tags additive (any match disables). Keys like "tool:foo". Visibility Transform under the hood. Layered (provider then server).
- **Middleware**: Intercepts on_message/on_request/on_call_tool etc. Bidirectional. Hooks for list/call specific. Builtins: Logging, Timing, Caching, RateLimiting (token bucket + sliding), ErrorHandling, Retry, ResponseLimiting (size truncate for tool outputs), Ping, etc. Custom in project mirror this (on_call_tool for steering).
- **Sampling**: ctx.sample(...) / sample_step(...) for server-initiated LLM calls (w/ tools, result_type Pydantic, model prefs, system, multi-turn, fallback handlers, tool_concurrency, mask_error_details). Uses client LLM or configured handler.
- **Elicitation**: ctx.elicit(message, response_type=...) for structured user input mid-tool (scalars, Literal/Enum/list for options, dataclass/Pydantic models, multi-select, defaults via Field, titles). Returns Accepted/Declined/Cancelled. Client must support.
- **Tasks (Background)**: @mcp.tool(task=True) or TaskConfig(mode="optional|required|forbidden"). Uses Docket (memory:// or redis). Progress dep, embedded worker + CLI workers for scale. poll_interval. Requires [tasks] extra.
- **Other relevant (from index/llms.txt + middleware/visibility/context pages)**: Context for logging/info/report_progress (already used in project for long ops), per-session state, strict_input_validation on FastMCP ctor, list_page_size for pagination (when 100s components), composition/mount w/ transforms.

Validation: Source matches docs (e.g. CodeMode __init__ params, Monty limits, discovery factories, CatalogTransform base, how call_tool in sandbox proxies to ctx.fastmcp.call_tool -- thus full middleware/transforms chain applies inside!). PromptsAsTools etc explicitly note "routes through the server's middleware chain".

Citations for key facts: see [web:2] CodeMode, [web:0] for v3.1 release notes.

---

## 2. Exactly How CodeMode Works

From fetched docs + .venv/.../code_mode.py source:

- **Meta-tools (default 3-stage)**:
  1. `search(query, tags?, detail="brief"|"detailed"|"full", limit?)`: BM25 (internal) or configurable. Returns names + desc (brief), or inline params md (detailed), or full JSON. Annotates "N of M tools:" if partial. Supports tags filter.
  2. `get_schema(tools: list[str], detail=...)`: For specific names, returns param details (default detailed md w/ types/required; full=JSON schema). Handles not-found.
  3. `execute(code: str)`: LLM writes Python. Only `await call_tool(tool_name: str, params: dict) -> Any` in scope (plus return). Runs in sandbox. Result (structured or text) returned to caller. Single block preferred.

- **Discovery stages + token vs roundtrips**: Progressive disclosure. Staged = targeted info (less bloat, more LLM turns/latency). Collapsed (e.g. Search w/ default_detail="detailed") = fewer turns, more tokens per result. ListTools for small catalogs (<20). GetTags + Search(tags=) for category browsing. Custom DiscoveryToolFactory(get_catalog) -> Tool (docstring becomes LLM desc of the meta-tool).
- **Sandbox limits (MontySandboxProvider)**: max_duration_secs (default 30 in baseline?), max_memory (100MB), max_allocations, max_recursion_depth, gc_interval. Pass limits=dict or None (uncapped). Requires pydantic-monty (via [code-mode] extra; raises clear ImportError if missing).
- **Tool call limits**: Not explicit max_tool_calls in v3.2.4 source (docs mentioned 50 default; perhaps evolved or per-sandbox). Unlimited in theory, but loops fanout; protect via middleware (our rate/expensive will throttle inside) or wrap call_tool in custom. Sandbox prevents side effects (no real exec; only injected async call_tool).
- **Custom**: execute_description override, execute_tool_name, discovery_tools=[Search(...), GetSchemas(...), GetTags(), ListTools(), my_factory]. Names must not collide w/ execute.
- **Inside execute**: call_tool resolves via *normal* server path (transforms + middleware + visibility + auth). So steering applies! `ctx` not directly in sandbox code (but call_tool results can include guidance from our DynamicGuidanceMiddleware).
- **Token savings**: Client never sees full 16+ schemas (each w/ 10-20 params like web_search's searxng_*). Only small meta schemas + on-demand. Final result only (intermediates stay in sandbox/Python, not LLM context).
- **Roundtrips**: Discovery 1-3 + 1 execute (which can internally do N tool calls + control flow w/o LLM). Vs classic: LLM reasons, 1 tool call, result in context, reason, next... Intermediates bloat context each time.
- **Catalog access**: Request-scoped (get_catalog(ctx) respects per-session visibility, auth, provider filters).

Exact wiring from source/docs: `mcp = FastMCP(..., transforms=[CodeMode(...)])` or post-creation `mcp.add_transform(CodeMode(...))`. CodeMode subclasses CatalogTransform (affects list_tools/get_tool).

---

## 3. Applicability to kindly-web-search-mcp (Critical Review)

**Current surface**: 10+ @mcp.tool in server.py (web_search with 15+ params incl. research_goal/rewrite/providers/searxng_*/domain_*, get_content, batch_get_content (w/ windowing), discover_links, gemini/perplexity/grok (synthesis), youtube_*, academic_search (filters)) + 3 composio (image/similarlinks/llm?), 1 agentic_web_research (ReAct over dedicated sub-tools), 2 analytics (query/report). + prompts/resources for steering. Instructions + middleware already teach "search first (rewrite=true default), then fetch, then synthesize sparingly; use agentic for full choice".

**"Instead of or in addition to profiles + ToolSearch"**: Project (per AGENTS/CLAUDE + steering plan) favors *external* agent-controlled orchestration ("separation is intentional: search discovers, fetch extracts, AI search synthesizes"). agentic_web_research already gives internal choice for those wanting it.

- **Use CodeMode so agents write small Python orchestrations calling *only* primitives (web_search/get_content etc.)**: Yes, highly applicable for *coding agents* (Claude Code, Cursor -- target users per project docs). Primitives: expose web_search (lightweight discovery), get_content/batch_get_content + discover_links (fetch), perhaps quick status or gemini for cheap synth. Hide or de-emphasize expensive (perplexity) / full agentic behind discovery. Agent writes e.g.:
  ```python
  results = await call_tool("web_search", {"query": query, "research_goal": goal, "num_results": 7, "rewrite": True})
  urls = [r["link"] for r in results.get("results", []) if r.get("provider_count", 0) >= 1][:5]
  # dedupe, filter, conditional
  if any("github.com" in u for u in urls):
      contents = await call_tool("batch_get_content", {"urls": urls, "total_char_budget": 20000})
  # custom merge or return table
  return {"sources": ..., "summary": "see fetched"}
  ```
  One execute call -> full workflow w/ control flow, no intermediate context bloat. Reduces "15 tools in prompt" to 3 meta.

- **Pros for this use case / coding agents**:
  - Dramatic context savings (no full schemas for complex-param tools; only discovered + small execute schema).
  - Roundtrip reduction for multi-step (internal Python loops/conditionals vs LLM turn per step).
  - Leverages that target users (coding assistants) are *excellent* at Python generation + debugging small scripts.
  - Sandbox + proxy = external calls safe (go thru our full stack: query_policy? middleware expensive/guidance/rate/response-limit, visibility, telemetry, specialized resolvers in content/, providers).
  - Complements existing: our DynamicGuidanceMiddleware etc. enrich *results* of call_tool inside sandbox; expensive protection still "thinks first".
  - For agentic hyperfocus: agent focuses on *writing orchestration* for goal, not choosing from flat list of similar search tools.
  - Scalable to "rich server" growth (composio expansion, more academic, future tools).

- **Cons / critical**:
  - **Python bias**: Assumes LLM good at correct async Python + error handling in sandbox. Non-coding agents (general chat) or weak models will fail more (bad code, wrong param names, infinite loops) than mis-selecting from described tools. Coding agents (Cursor/Claude Code) mitigate this.
  - **Debuggability**: Errors surface as execute result (trace from sandbox?); harder than direct tool call failures. No direct ctx.info inside user code (though results can carry guidance).
  - **Experimental**: "core interface stable but discovery tools/params may evolve" (per doc). v3.1+ only; our lock 3.2.4 ok but extra needed.
  - **Overhead**: Small sandbox startup + Python parse/execute per execute. For simple 1-2 step, classic direct calls + middleware cheaper/faster.
  - **Philosophy tension**: Project emphasizes "keep external agent in control" + "transparent agent-controlled chaining". CodeMode moves *some* orchestration into server-side Python (agent-written). Good for power users, risks hiding intent.
  - **Not replacement for profiles**: See below.
  - **Sandbox limits bite**: Long browser fetches or many parallel in loops can hit duration/memory (tune per our _resolve_tool_total_timeout_seconds ~120s+). No net/fs direct (good for safety).
  - **"Many tools not used"**: Helps by *hiding* them, but if agent doesn't discover "agentic_web_research" or "perplexity" via search("synthesis expensive"), it may under-use powerful paths. Requires good tool descriptions + perhaps seed execute_description.

**When CodeMode *shines* vs simple profiles** (critical):
- **Shines**: Dynamic/complex workflows needing loops (fanout N results -> conditional batch_get on best k, dedup by domain/provider_count, custom RRF inside, retry on low provider_count, parallel independent searches then merge). Large/growing catalog (>10-15 tools w/ heavy schemas). Token-sensitive long contexts or strict client tool caps (Cursor ~40?). Coding-agent users who treat it as "write a mini research script using primitives".
- **Simple profiles win**: Static subsets (e.g. "core" = discovery+fetch only; "full" = +synthesis+agentic+analytics). Zero LLM codegen cost. Universal (works for any client, no Python). Predictable surface. Env-gated (KINDLY_TOOL_PROFILE=minimal) w/ no experimental flag. Fast to implement (tags + Visibility). Good "default safe" for cost control (hide expensive unless profile=research).
- **Best together (recommended)**: Profiles/Visibility for base surface control + tags. ToolSearch (BM25) for on-demand even in "full". PromptsAsTools/ResourcesAsTools for workflow teaching. *Optional* CodeMode (env-gated or separate "kindly-web-search-codemode" entry) for advanced Python-orchestrating clients. Layer middleware always. This is exactly evolution of steering-plan (which said "Keep CodeMode Opt-In" + "do not enable as default") + obs plans (P0: profiles + ToolSearch).

**"Many tools not used" + agentic hyperfocus**: Visibility + profiles limit *what is visible* (agent can't hyperfocus on wrong expensive tool if disabled). ToolSearch/CodeMode make discovery *active* (agent must search "fetch" or "synthesis" -> focuses intent). Middleware (existing + ResponseLimiting) + result enrichment (agent_guidance + suggested_next + prompts) + chaining hints in returns steer *after* call. Per-session unlock for progressive (e.g. activate "synthesis" after core success). Sampling/Elicitation for server to ask clarifying mid-op (sparingly). Overall: reduces passive overload (full list always in context) and forces strategic (search before use, think before expensive).

---

## 4. Current Project Usage (from grep + reads)

- **Middleware**: Heavy, custom for *steering* (not just infra). See src/kindly_web_search_mcp_server/middleware/:
  - expensive_tool_protection.py: ExpensiveToolProtectionMiddleware (on_call_tool, blocks first perplexity/grok w/ QUERY_QUALITY_STEERING_MESSAGE via ToolError + SessionTracker; allows retry). Added in server.py:167.
  - query_guidance.py: DynamicGuidanceMiddleware + gemini_advisory (injects "agent_guidance" + "suggested_next_tools" + "suggested_prompts" into *structured results* for web_search/get_content/etc. Result-aware, non-blocking. Per-tool first-N advisory).
  - rate_limits.py: DifferentiatedRateLimitMiddleware (cheap vs expensive RPS/burst).
  - session_tracking.py: SessionTracker for per-tool attempt counts (local_context: or similar).
  - Tests: test_middleware_observability.py, test_agent_steering_middleware.py (verify guidance, expensive block/allow, session expiry).
  - Also in agent/runner.py: LangChain ToolCallLimitMiddleware (separate).
  - Utils/observability emits "middleware.*" events.
  - No built-in FastMCP middleware (Logging etc.) yet, but patterns match (on_call_tool etc.).
- **Transforms**: *None active*. mcp_compat.py has manual register_prompt_and_resource_tools (unused; homegrown Prompts/Resources as tools via @mcp.tool wrappers; bypasses some?). No add_transform, no CodeMode/PromptsAsTools/ResourcesAsTools/Namespace/ToolSearch/Visibility/ToolTransform. Repomix xml mentions middleware dir only. Search mentions only in test data/query examples.
- **Visibility / tags / per-session**: Tags only on 4 @mcp.prompt (research/planning etc.). No on tools/resources. No mcp.enable/disable, no Visibility transform, no ctx.enable_components. Resources use plain @mcp.resource("status://...").
- **Other**: Context injected (CurrentContext()) in most tools for ctx.info() (e.g. agentic, long ops). No sampling/elicitation (though pages fetched). No @task=True / background. FastMCP ctor minimal (name + instructions only). list_tools etc. implicit full dump. mcp.add_middleware 3x.
- **Related steering**: Rich instructions (tool routing table), per-tool docstrings ("use rewrite=true default..."), resources (workflow, features, providers), prompts (plan/eval/gap/suggest_tool), result enrichment via middleware, ToolAnnotations on some, error formatting. But per recent obs: still "clients may choose expensive too early", "surface large", "not used strategically" (telemetry shows concentration on few?).

Matches steering-plan assessment ("already uses several well" but "intended usage grammar too implicit").

---

## 5. Comparison to Existing Plans

- **plans/Done/FastMCP-client-steering-plan.md** (2026-05-11): Primary source. Recommends exactly:
  1. Update instructions (partially done).
  2. PromptsAsTools(mcp) -- "lets tool-only clients discover how to chain".
  3. ResourcesAsTools(mcp) -- makes docs://workflow visible.
  4. Tags + meta (kindly.role/chain_next/cost etc.) on tools/prompts/resources.
  5. recommended_next_tools / usage_hint in results (partially via dynamic guidance middleware).
  6. ctx.report_progress() (some info(), not full).
  7. More resources (tool-chains etc.).
  8. Elicitation sparingly (e.g. for vague expensive).
  9. strict_input_validation=True.
  10. Pagination if grows.
  11. **CodeMode opt-in only, separate entrypoint** ("catalog still small enough... philosophy favors external agent-controlled"). "Keep explicitly separate from default".
  Matches query's "which already recommends PromptsAsTools...".
- **Recent obs/aggregated (2026-06-03 plans/observability/aggregated-...)**: Builds on it. P0: KINDLY_TOOL_PROFILE (env) + tags + mcp.enable/disable (or Visibility) for minimal/core/research/full; default core. Adopt ToolSearch (BM25, pin core always_visible). Add search_status() tool. Pin fastmcp>=3.2 + ResponseLimitingMiddleware. "profiles reduce exposed tools per env; ToolSearch active (list_tools small + discoverable)". P2: full visibility per-session, more transforms (PromptsAsTools), split surfaces. "Upgrade helps implement profiles + discovery cleanly". Subagent research already noted CodeMode/Visibility in Unreleased CHANGELOG.
- **This research**: Confirms plans were prescient (v3.2.4 has the APIs). Strengthens "opt-in CodeMode" w/ critical (shines for coding agents on complex chains; profiles simpler/universal). Recommends *layering* (profiles/Visibility + ToolSearch + Prompts/Resources + *optional* CodeMode) + tags on *tools* (missing). Middleware integration is free (execute proxies). Also suggests ResponseLimiting (built-in, good for large page_content/markdown).

No conflicts; this is "Phase 4+" refinement per continuity.

---

## 6. Practical Recommendations + Code-Ready Diffs for server.py

**High-level recs** (prioritized, building on plans):
- Bump `fastmcp>=3.2.0` in pyproject.toml (already resolves 3.2.4; add `code-mode` optional-dep for users: `code-mode = ["fastmcp[code-mode]>=3.2.0"]`).
- Add tags to *all* @mcp.tool (and resources if supported) + update prompts/resources. Use for GetTags, Search(tags=), Visibility profiles.
- Implement core steering from plan: PromptsAsTools + ResourcesAsTools (deprecate/unuse mcp_compat if desired; this is official + middleware-aware).
- Add BM25SearchTransform w/ always_visible core primitives (web_search, get_content*, discover_links, perhaps analytics_query/status tools). This + profiles solves overload w/o code.
- Add KINDLY_TOOL_PROFILE support (settings + visibility logic at startup; e.g. "core" exposes discovery/fetch only; "full" everything; "research" adds synth). See obs plan for details. Use mcp.disable(tags=...) or Visibility(False, tags=...).
- **CodeMode**: Opt-in via KINDLY_ENABLE_CODE_MODE=true (or separate CLI entry). Wire w/ GetTags+Search(detailed)+GetSchemas for tag-aware discovery. Sandbox tuned for search (higher duration matching our tool timeout, memory for markdown). Primitives: focus docs on "core discovery/fetch only" in execute_description. Layer *after* other transforms? Test order.
- Integrate middleware: *No change needed* -- CodeMode's internal call_tool goes thru on_call_tool hooks + transforms. Existing expensive/guidance/rate will protect/steer inside Python orchestrations. Add ResponseLimitingMiddleware for large content (good hygiene).
- Other: Use ctx.report_progress() in long tools (web_search stages, content stages). Add meta= to tools (kindly: {role, chain_next, cost, ...}). Consider strict_input_validation=True + list_page_size=20 in ctor. Enhance resources (more docs://). For sampling/elicitation: future (e.g. elicit for vague query before expensive; sample for quick internal rewrite if no MISTRAL). Tasks: for very long agentic if wanted.
- Update: server instructions, status resources (show active transforms/profile), docs/CONFIG/DEVELOPMENT/ARCHITECTURE/TESTING, AGENTS/CLAUDE.md examples, tests (add for transforms if wired; mock catalog), CHANGELOG (under Unreleased Added).
- Testing: Use existing patterns (patch under kindly_web_search_mcp_server.*). For CodeMode, test execute calls primitives + middleware fires. IsolatedAsyncioTestCase.
- Risks mitigation: Guard import (try/except + log "CodeMode disabled: install fastmcp[code-mode]"), env flag + docs warning "experimental", default OFF, limit max duration, clear error if monty missing, seed good descriptions + example code in execute desc.
- When to enable default? Never per plans + critical (use profiles+search first; measure via telemetry "compliance" % following chains before adding CodeMode surface).

**Proposed diffs for server.py** (C:\Users\Jan\Documents\GitHub\1Agents1\.CLI\web-search-mcp\src\kindly_web_search_mcp_server\server.py ; apply after testing; also bump pyproject + add to CHANGELOG):

```diff
diff --git a/src/kindly_web_search_mcp_server/server.py b/src/kindly_web_search_mcp_server/server.py
index abc1234..def5678 100644
--- a/src/kindly_web_search_mcp_server/server.py
+++ b/src/kindly_web_search_mcp_server/server.py
@@ -38,9 +38,18 @@ import sys
 from typing import Literal
 
 from fastmcp import FastMCP
+from fastmcp.dependencies import CurrentContext
 from fastmcp.dependencies import CurrentContext  # For context injection
 from fastmcp.prompts import Message
 from fastmcp.server.context import Context  # Context type
+from fastmcp.server.transforms import (
+    PromptsAsTools,
+    ResourcesAsTools,
+    Visibility,
+)
+from fastmcp.server.transforms.search import BM25SearchTransform
+try:
+    from fastmcp.experimental.transforms.code_mode import (
+        CodeMode, GetTags, Search as CodeSearch, GetSchemas, MontySandboxProvider
+    )
+    CODE_MODE_AVAILABLE = True
+except Exception:
+    CODE_MODE_AVAILABLE = False
 from mcp.types import ToolAnnotations  # For tool annotations
 
 # ... (existing imports)
@@ -148,7 +157,12 @@ mcp = FastMCP(
         "Use youtube_search before youtube_transcript, and composio_similarlinks to expand from a known good URL. "
         "Use agentic_web_research when you want the LangChain/LangGraph ReAct agent to choose among direct search, fetch, rerank, and expansion tools itself."
     ),
+    # Recommended per FastMCP steering plans + v3+ best practices
+    # version=... (add from importlib.metadata),
+    # strict_input_validation=True,  # after test validation
+    # list_page_size=25,
 )
 
 # Add expensive... (existing 3x add_middleware)
 
@@ -187,6 +201,7 @@ from .middleware import create_dynamic_guidance_middleware
 mcp.add_middleware(create_dynamic_guidance_middleware())
 register_composio_tools(mcp)
 register_agentic_web_research_tools(mcp)
 register_analytics_tools(mcp)
 
+# === Transforms for client steering, profiles, overload reduction, and experimental CodeMode ===
+# See plans/Done/FastMCP-client-steering-plan.md and plans/observability/aggregated-*-fastmcp-rerank-tool-strategy-*.md
+# Official PromptsAsTools/ResourcesAsTools (better than unused mcp_compat.py; routes thru middleware/visibility)
+mcp.add_transform(PromptsAsTools(mcp))
+mcp.add_transform(ResourcesAsTools(mcp))
+
+# ToolSearch (BM25) + always_visible core primitives: on-demand discovery, reduces bloat for 16+ tools.
+# Core always listed; others via search_tools(query=...) / call_tool(name=...).
+CORE_ALWAYS_VISIBLE = [
+    "web_search", "get_content", "batch_get_content", "discover_links",
+    "gemini_search",  # cheap grounded
+    # analytics_query, status tools if named
+]
+mcp.add_transform(
+    BM25SearchTransform(
+        max_results=6,
+        always_visible=CORE_ALWAYS_VISIBLE,
+    )
+)
+
+# Tags + Visibility for KINDLY_TOOL_PROFILE (minimal/core/research/full) -- P0 per obs plans.
+# Add tags= to @mcp.tool decorators below (search/discovery/read-only/core ; synthesis/expensive ; agentic etc.)
+# Example (expand w/ full logic in settings + here):
+# profile = (os.getenv("KINDLY_TOOL_PROFILE") or "core").lower()
+# if profile == "minimal":
+#     mcp.disable(tags={"synthesis", "expensive", "agentic", "analytics"})
+# elif profile == "core":
+#     mcp.disable(tags={"expensive", "agentic"})  # keep synth cheap ones?
+# Server-level; per-session ctx. also available.
+# mcp.add_transform(Visibility(...)) equiv to enable/disable.
+
+# Experimental CodeMode (opt-in; per steering plan: NOT default; separate if full surface wanted).
+# Agents (esp. coding ones) write small Python orchestrations on *primitives only*.
+# Discovery uses tags (once added) + search. Sandbox tuned for our timeouts (content fetch can be slow).
+# Middleware (expensive/guidance/rate) + transforms apply *inside* execute via call_tool proxy. Great!
+if os.getenv("KINDLY_ENABLE_CODE_MODE", "").strip().lower() in ("1", "true", "yes") and CODE_MODE_AVAILABLE:
+    sandbox = MontySandboxProvider(
+        limits={
+            "max_duration_secs": _resolve_tool_total_timeout_seconds() + 60,  # headroom for chains + browser
+            "max_memory": 256 * 1024 * 1024,
+        }
+    )
+    code_mode = CodeMode(
+        sandbox_provider=sandbox,
+        discovery_tools=[GetTags(), CodeSearch(default_detail="detailed"), GetSchemas()],
+        execute_tool_name="execute",  # or "execute_research" to namespace
+        # execute_description= "Custom: focus on web_search + get_content primitives. Example: ... Use return final."
+    )
+    mcp.add_transform(code_mode)
+    LOGGER.info("CodeMode (experimental) enabled via KINDLY_ENABLE_CODE_MODE. Primitives recommended for orchestration.")
+elif os.getenv("KINDLY_ENABLE_CODE_MODE"):
+    LOGGER.warning("KINDLY_ENABLE_CODE_MODE set but CodeMode unavailable (install fastmcp[code-mode] or check version).")
+
 # Transport = ...
 
 # (existing main, helpers, _record_*, _apply_*, _timeout_*, etc.)
 
@@ -470,6 +520,7 @@ def _apply_domain_filters(...): ...
 @mcp.tool(
     annotations=ToolAnnotations(
         title="Web Search",
+        # ...
     ),
+    tags={"search", "discovery", "read-only", "core", "open-world"},
 )
 async def web_search(...):
     """..."""
 
 # Similarly for others:
 # get_content: tags={"content", "extraction", "follow-up", "core"}
 # batch_get_content: same + "batch"
 # gemini_search: {"search", "synthesis", "grounded", "cheap"}
 # perplexity_search: {"search", "synthesis", "expensive"}
 # grok_search: {"search", "synthesis", "x", "expensive"}
 # agentic_web_research: {"agentic", "orchestration", "full"}
 # analytics_*: {"observability", "internal"}
 # youtube_*: {"video", "youtube"}
 # academic: {"search", "academic", "discovery"}
 # composio_*: {"expansion", "composio"}
 
 # (for resources, if supported: @mcp.resource("status://providers", tags={"status", "core"}) )
 
 # ... (all other @mcp.tool unchanged except tags + perhaps add recommended_next / usage_hint to returns)
 
 # At end, after last prompt (around line 2677):
 # (existing prompts)
 
+# Ensure transforms applied after all registrations (order: innermost first; add after for server-level).
+# Visibility/profile logic can go here too (after tools defined for tag inspection if needed).
+
 if __name__ == "__main__":
     # not used; main() for entry
     pass
```

**Additional files to touch (snippets/paths)**:
- `pyproject.toml`: bump fastmcp, add to [project.optional-dependencies] `code-mode = ["fastmcp[code-mode]>=3.2.0"]` ; update scripts/docs if uvx.
- `src/kindly_web_search_mcp_server/settings.py`: Add `tool_profile: str = os.environ.get("KINDLY_TOOL_PROFILE", "core")` + validator (minimal|core|research|full). Expose in status://features.
- `src/kindly_web_search_mcp_server/middleware/query_guidance.py` or new: enhance for CodeMode context (detect if called from execute?).
- `CHANGELOG.md` (see below).
- `docs/CONFIGURATION.md`, `docs/DEVELOPMENT.md`, `docs/ARCHITECTURE.md`, `AGENTS.md`, `CLAUDE.md`: Document envs, profiles, when to use CodeMode (for coding agents on complex research), install w/ extra, example execute code.
- `tests/`: Add `test_fastmcp_transforms.py` (or extend test_server) mocking catalog; verify list_tools small under search/CodeMode, execute calls web_search etc., middleware fires, visibility per profile.
- Update `mcp_compat.py` deprecation note or remove if PromptsAsTools covers.

**Sandbox config rationale for this use case**: Search/fetch can take 10-120s (providers + trafilatura + nodriver browser coldstart per our _resolve... + KINDLY_*_TIMEOUT). Set duration high but bounded (prevent abuse). Memory for large markdown batches. No tool call cap (use our rate middleware + agentic budgets inside); monitor via telemetry.

**Integration w/ existing**: Layered naturally (transforms on catalog listing; middleware on execution path). CodeMode execute -> call_tool(name, params) -> full on_call_tool chain (our 3 middlewares + any future) -> original fn. Guidance enriches the *Python-visible* result. Perfect for "agents write orchestrations calling primitives" while steering still happens.

**Tags for discovery**: Add as above; GetTags() will surface "core", "expensive" etc. Search accepts tags= param. Use in profile disables + CodeMode discovery.

---

## 7. Risks + Mitigations

- **Experimental**: "specific discovery tools and their parameters may evolve". Mit: Pin fastmcp, guard w/ try/except + fallback (no transform), feature flag default false, document "opt-in, test thoroughly".
- **Python-in-sandbox for non-Python-native**: Agents may emit invalid code, wrong awaits, param shape errors, unhandled exceptions. Mit: Rich execute_description w/ examples + primitives contract; good tool descs (already strong); mask_error_details + ToolError for clean feedback; fall back to direct tools; measure "execute success rate" in telemetry.
- **Security**: LLM code exec -- but *isolated* (pydantic-monty, no direct sys/net/fs). All side effects via call_tool (our controlled, middleware-protected, no creds leaked if using token tools carefully). External calls (SearXNG etc.) use server's http_client/settings (safe). Still: don't put secrets in code; rate limit the execute itself.
- **Debugging**: Opaque (stack in sandbox result); harder than direct calls. Mit: Structured errors, include code in observability spans, ctx.info from outer if possible, good logging in middleware. Provide "debug_code_mode" resource?
- **Performance**: Extra stages + sandbox for simple cases. Mit: Profiles default to direct; CodeMode for users who opt; tune limits; cache inside Python if needed.
- **Compatibility**: Tool-only clients love it (meta are tools); prompt/resource clients still work (or via as-tools). Per-session visibility may interact (CodeMode discovery respects? yes via get_catalog).
- **"Not used" tools**: If CodeMode, agent must discover them -- risk under-use of powerful (agentic, academic). Mit: Seed execute_description w/ "core primitives + when to discover synthesis via search('perplexity')"; status resource; prompts-as-tools.
- **Version skew**: Base fastmcp 3.2.4 has CodeMode but extra for runtime. Mit: Clear docs + import guard.
- **Hyperfocus/overload**: Solves listing bloat but shifts to "discover bloat" if search bad. Mit: Strong BM25 + tags + seeded examples.

Overall risk low if opt-in + guarded (as plan said).

---

## 8. How This + Visibility + Middleware Addresses Issues

- **"Many tools not used"**: Visibility/profiles hide irrelevant (agent sees 4-6 instead of 16; can't misuse expensive if disabled). ToolSearch/CodeMode: lazy discovery (agent *searches* "content fetch" -> only then sees schema; focuses attention). Result: higher usage rate of *right* tools.
- **Agentic hyperfocus / early expensive / flat list confusion**: Middleware (expensive protection forces refine before perplexity; dynamic guidance post-search suggests "now batch_get or evaluate prompt" + injects into *every* result). CodeMode: no flat list to hyperfocus on; agent writes *goal-directed* script. Per-session: unlock advanced after core success (progressive disclosure). Sampling/Elicitation: server can probe ("clarify timeframe?") without full tool. Chaining hints + prompts/resources (as-tools) teach grammar explicitly. Telemetry (existing) + new compliance metrics close loop ("did follow search->fetch?").
- **Token/UX wins for rich servers**: Exactly as CodeMode doc: "hundreds of tools = tens of k tokens upfront". Here 16 w/ verbose params/schemas already hurts; will grow w/ composio/academic expansions.
- **Fits project**: Builds *on* existing investment (middleware, instructions, prompts, resources, agentic sub-tools) rather than replacing. External control preserved for default; CodeMode power-user escape hatch.

---

## 9. Next Steps / Acceptance (from plans + this)

1. Update pyproject + server.py (tags + transforms wiring + profile sketch + CodeMode guard).
2. Add KINDLY_TOOL_PROFILE + docs.
3. Enhance 2-3 tools w/ report_progress + recommended_next in returns.
4. Tests + live probe w/ Claude Code/Cursor (before/after list_tools size, execute example, guidance visible inside).
5. CHANGELOG + docs updates.
6. Measure (existing DuckDB views for tool calls + new "profile" / "codemode" / "followed_steering" fields).
7. If positive: expose search_status tool, more resources, consider dedicated codemode entrypoint.

**Proposed Unreleased CHANGELOG addition** (append to top of [Unreleased] Added):

```
- Deep research + code-ready plan for FastMCP advanced steering (CodeMode experimental, all transforms, Visibility/tags/per-session, middleware patterns, Sampling/Elicitation/Tasks) focused on tool overload in 16-tool search+fetch+synthesis+agentic servers. Fetched/validated official docs + source (fastmcp 3.2.4). Current usage: rich custom middleware (expensive protection + dynamic agent_guidance/suggested_next/prompts + rates; see middleware/), no transforms (mcp_compat unused), tags only on prompts, no visibility/CodeMode. Compared to FastMCP-client-steering-plan.md (Prompts/ResourcesAsTools, metadata, progress, CodeMode opt-in) + obs aggregated plans (P0 profiles via tags+Visibility + ToolSearch BM25). Critical applicability: CodeMode shines for coding agents (Cursor/Claude Code) on dynamic multi-step Python orchestrations over primitives (token/roundtrip savings, internal control flow); vs profiles (simpler, universal, no Python req, static subsets for cost). Recs: layer all (profiles default + ToolSearch + Prompts/Resources + *opt-in* CodeMode via KINDLY_ENABLE_CODE_MODE); add tags to tools; wire in server.py w/ sandbox tuned to our timeouts; middleware integrates for free. Risks mitigated (guard, flag, docs). Addresses overload/hyperfocus via lazy discovery + steering layers. See new plans/research/FastMCP-CodeMode-...-2026-06-02.md + diffs for server.py. (Subagent task.)
```

**Absolute paths referenced**:
- C:\Users\Jan\Documents\GitHub\1Agents1\.CLI\web-search-mcp\src\kindly_web_search_mcp_server\server.py (main diffs target)
- C:\Users\Jan\Documents\GitHub\1Agents1\.CLI\web-search-mcp\plans\Done\FastMCP-client-steering-plan.md
- C:\Users\Jan\Documents\GitHub\1Agents1\.CLI\web-search-mcp\plans\observability\aggregated-findings-recommendations-fastmcp-rerank-tool-strategy-2026-06-03.md
- C:\Users\Jan\Documents\GitHub\1Agents1\.CLI\web-search-mcp\src\kindly_web_search_mcp_server\middleware\*.py
- C:\Users\Jan\Documents\GitHub\1Agents1\.CLI\web-search-mcp\pyproject.toml
- C:\Users\Jan\Documents\GitHub\1Agents1\.CLI\web-search-mcp\.venv\Lib\site-packages\fastmcp\experimental\transforms\code_mode.py (source validation)
- https://gofastmcp.com/servers/transforms/code-mode ([web:2])
- CHANGELOG.md , docs/* , AGENTS.md

This completes the task. All recs are actionable, critical where warranted, and directly reference/ extend prior plans without broadening.

(End of report. Implement in phases starting w/ simpler transforms/profiles before experimental CodeMode.)
