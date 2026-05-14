# Quick Research: 8 Repos - Novel Features and Code Patterns

## SUMMARY

Researched 8 repos in 3-4 min each via README + key source files. Three repos are highly relevant to the baseline (kindly-web-search-mcp). Two are production-grade. Key findings below.

---

## 1. web-search-plus (robbyczgw-cla) v3.0.3
**HIGHLY RELEVANT. Closest cousin to the baseline. 50K LOC Python/CLI.**

Novel features:
- RegEx-based QueryAnalyzer with weighted signals for shopping/research/discovery intent. Routes to provider WITHOUT LLM overhead. Includes German language patterns.
- Provider health/cooldown tracking in provider_health.json. Failing providers skipped for configurable duration.
- Separate extract pipeline (extract.py) with auto-fallback Firecrawl->Linkup->Tavily->Exa->You.com
- Exa auto-upgrade to deep/deep-reasoning based on query signals
- --explain-routing debug flag
- SSRF-safe SearXNG URL validation (DNS resolution, private IP blocking, cloud metadata endpoint blocks)

Code patterns to adopt:
- JSON-file cache with SHA256 keying (no external DB)
- Provider-agnostic normalize_result() function
- Config layering: config.json merges over DEFAULT_CONFIG
- Env file auto-load from skill root

## 2. NinjaSearchWithHumanGPT (thrivewithai) - 2023 PoC
**LOW RELEVANCE. Abandoned proof-of-concept.**

Novel features:
- Human-in-the-loop: agent asks user for clarification via LangChain human tool
- Bot detection bypass via Zenrows proxy
- On-the-fly FAISS RAG from scraped HTML (RecursiveCharacterTextSplitter->OpenAIEmbeddings->FAISS->RetrievalQA)

Pattern: Entire agent in ~80 lines of LangChain

## 3. open-extract (velocitybolt)
**MEDIUM RELEVANCE. Different domain (structured extraction).**

Novel features:
- Schema-driven extraction: user defines JSON schema of key-value pairs to extract from unstructured docs
- Multi-schema/multi-document in one call
- No vendor lock-in (any model provider)
- Built-in caching for repeat extractions

Pattern: Schema-as-configuration paradigm

## 4. Blog-writer-multi-agent (Abdulbasit110)
**LOW RELEVANCE. Different domain (blog writing).**

Novel features:
- CrewAI multi-agent: Planner->Writer->Editor
- Serper web search integration
- Full-stack: FastAPI + Next.js/React/Tailwind/Shadcn + Gemini 2.0-Flash

Pattern: Jupyter Notebook as FastAPI server (interesting for prototyping)

## 5. llm-agent-web-tools (ZubinGou) - ICLR'24 CRITIC Paper
**MEDIUM RELEVANCE. Academic, multi-engine search.**

Novel features:
- Deterministic reproduction cache: archives ALL greedy-decoding queries + outcomes for reproducibility
- Fuzzy snippet-to-page matching via fuzzysearch library
- end_year filtering on Google
- BaseSearch abstract class with ReturnType enum (FULL/TITLE/LINK/DESCRIPTION)
- Multi-engine: Bing, Baidu, Google Scholar, DuckDuckGo, GitHub, StackOverflow, YouTube

Pattern: ReturnType enum for selective field extraction

## 6. AgentWebSearch-MCP (insung8150) - MOST NOVEL
**HIGH RELEVANCE. Zero-API-key MCP server with real Chrome CDP.**

Novel features:
- Zero-API-key search via real Chrome CDP (3 actual browser windows, not headless)
- Parallel multi-portal: Naver(9222)/Google(9223)/Brave(9224) simultaneously
- Bot detection bypass by design (real browser fingerprint, persistent sessions, OAuth)
- Korean portal support (Naver requires real browser)
- 6 MCP tools: web_search, fetch_urls, smart_search, get_search_status, cancel_search, agentcpm
- Partial results + cancellation: get_search_status returns progress %, partial data mid-search
- AgentCPM-Explore 4B model integration via SGLang
- Depth control: simple(~35s), medium(~50s), deep(~170s)
- Multi-backend LLM adapters: SGLang, Ollama, LM Studio, OpenAI

Patterns: chrome_launcher.py lifecycle manager, PORTAL_CONFIG extensibility, BaseAdapter interface

## 7. web-search-agent (TheWhiteTower16)
**LOW RELEVANCE. Minimal project.**

Basically a DeepSeek wrapper with web UI. ~800 chars of README. No source accessible.

## 8. web-forager (CyranoB) - pip-installable MCP
**HIGH RELEVANCE. Production MCP server + Agent Skills.**

Novel features:
- Dual-mode: MCP server AND 5 standalone Agent Skills (no MCP required)
- LLM-friendly text output format option (numbered, readable text instead of JSON)
- DuckDuckGo news search with date-sorted results
- Direct HTTP + trafilatura extraction with Jina Reader fallback
- Multi-client MCP config docs (Claude Desktop, Codex, Cursor, OpenCode, Gemini CLI)
- uv/uvx packaging for zero-install MCP usage
- Docker support

Pattern: Centralized FastMCP singleton used by all tool modules, pyproject.toml entry points

---

## TOP ACTIONABLE TAKEAWAYS FOR THE BASELINE

### Tier 1 (Immediately Valuable)
1. RegEx-based intent router from web-search-plus: classify shopping/research/discovery without LLM
2. Partial results + cancellation from AgentWebSearch-MCP: get_search_status/cancel_search for long searches
3. LLM-friendly text output from web-forager: output_format="text" saves tokens
4. Provider health/cooldown tracking from web-search-plus: persistent health state

### Tier 2 (Worth Exploring)
5. CDP-based real browser fallback from AgentWebSearch-MCP
6. Deterministic reproduction cache from llm-agent-web-tools
7. Separate extract provider chain from web-search-plus

### Tier 3 (Nice to Have)
8. Agent Skills alongside MCP from web-forager
9. Schema-driven extraction from open-extract
10. CrewAI-style orchestration from Blog-writer-multi-agent

---

## QUALITY ASSESSMENT

web-search-plus: HIGH novelty, HIGH quality, VERY HIGH relevance - Production-ready
AgentWebSearch-MCP: VERY HIGH novelty, HIGH quality, HIGH relevance - Production-capable
web-forager: MEDIUM novelty, HIGH quality, HIGH relevance - Actively maintained
llm-agent-web-tools: HIGH novelty, HIGH quality, MEDIUM relevance - Research-grade
All others: MEDIUM/LOW novelty and relevance
