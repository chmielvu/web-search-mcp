# Web Search Agent Ecosystem — Repo Research Summary
Baseline: kindly-web-search-mcp (MCP server with SearXNG, DDG, Gemini, Tavily, Brave, Jina, Composio backends)

## 1. agentic_search_openai_langgraph (menonpg)
**URL**: https://github.com/menonpg/agentic_search_openai_langgraph
**Stars**: unknown | **License**: MIT | **Language**: Python

### Architecture
- Multi-agent supervisor pattern using LangGraph
- Supervisor → routes between 2 workers: Web_Searcher, Insight_Researcher
- Uses OpenAI GPT-4o exclusively (no local model support)
- State management via LangGraph `StateGraph`

### Search Backends
- Tavily API (paid, with API key)
- DuckDuckGo (free, no key needed)
- Switchable via `get_tools()` in tools.py

### UI Layer
- Streamlit app (`app_st.py`)
- Gradio app (`app_gradio.py` / `main.py`)

### Key Strengths vs Baseline
- + Multi-agent orchestration with supervisor-routing pattern
- + Built-in content processing (`process_content` tool)
- + Step-by-step insight generation from search results
- + Both Streamlit and Gradio UIs
- - **Proprietary LLM only** (GPT-4o); no local model path
- - Requires 3 paid API keys (OpenAI + Tavily + LangSmith)
- - No SearXNG support, no image search, no caching
- - Monolithic graph; not MCP-compatible

### Code Quality
- Clean separation: agents.py, graph.py, tools.py
- `AgentState` TypedDict for state
- `AgentExecutor` with LangChain tools
- ~500 lines total; well-structured but minimal

---

## 2. SurfAgent (Haseebasif7)
**URL**: https://github.com/Haseebasif7/SurfAgent
**Stars**: unknown | **Language**: Python

### Architecture
- CLI-based agent, built from scratch
- Single `WebAgent` class with integrated research pipeline
- Uses Selenium for browser-based page fetching (handles JS-heavy sites)
- Llama Vision Models for image analysis (GROQ or Ollama)

### Search Backends
- Brave Search API (paid, with API key)
- Wikipedia (LangChain integration)

### Unique Features
- **Dynamic memory system**: ResearchMemory tracks past interactions, query types, source reliability
- **Host tracker**: `HOSTS.txt` blacklists problematic domains
- **Content assessment pipeline**: `assess_content_relevance()` → `extract_key_information()` → `assess_question_complexity()`
- **Adaptive research depth**: Complexity-based `should_continue_research()` adjusts number of sources
- **Human feedback loop**: `record_human_feedback()` captures accuracy judgments
- **Domain-prioritized search**: For stock prices, targets marketwatch/finance.yahoo/bloomberg/reuters
- Image analysis via Llama Vision models

### Key Strengths vs Baseline
- + Smart adaptive research depth (not fixed N results)
- + Source reliability scoring over time
- + Selenium handles JS-rendered pages (baseline relies on text extraction)
- + Local LLM support (Ollama) + cloud (GROQ)
- + Image analysis capability
- - **Single Brave Search backend only** (no SearXNG, DDG fallback)
- - Heavy dependency: requires Selenium + headless browser
- - No MCP interface; standalone CLI only
- - Monolithic class (~500 lines in web_agent.py); harder to extend

---

## 3. AI-web_scraper (m92vyas)
**URL**: https://github.com/m92vyas/AI-web_scraper
**Stars**: unknown | **Language**: Python

### Architecture
- Lightweight scraping functions library
- Two main functions: `scrape_data_from_web()` (search + scrape), `extract_from_url()` (targeted extraction)
- Uses its own open-source **llm-reader** as webpage→LLM-ready text converter (alternative to Firecrawl/Jina Reader)
- Playwright for browser-based fetching

### Search Backends
- No built-in search engine; relies on LLM-driven web search + scraping
- LiteLLM for model abstraction (OpenAI, Gemini, etc.)

### Key Strengths vs Baseline
- + **Cost-effective**: uses open-source llm-reader instead of Jina/Firecrawl APIs
- + URL extraction is explicitly highlighted as a strength
- + Simple function-based API; easy to add to existing codebases
- + Structured output via natural language format specification
- + Model-agnostic via LiteLLM (OpenAI, Gemini, local models)
- - **No anti-blocking mechanism** (acknowledged limitation)
- - No built-in search engine (must pair with external search)
- - Very minimal (incomplete README, no tests visible)
- - Not an agent; just utility functions

---

## 4. open-extract (velocitybolt)
**URL**: https://github.com/velocitybolt/open-extract
**Stars**: unknown | **Language**: Python

### Architecture
- Platform for structured data extraction from unstructured documents/websites
- Designed for AI Agent frameworks: LangGraph, AG2, CrewAI
- Schema-based extraction: define key-value pairs describing what to extract
- Single API call abstraction

### Key Features
- **Multi-schema/multi-document support**: extract based on multiple schemas simultaneously
- **Built-in caching**: previously extracted schemas retrieved instantly, no reprocessing
- **No vendor lock-in**: any model provider (open-source or closed-source)
- Returns JSON or Markdown

### Key Strengths vs Baseline
- + Schema-based structured extraction (baseline returns markdown text only)
- + Built-in caching for repeated extractions
- + Multi-document batching
- + Framework integration (LangGraph, AG2, CrewAI)
- - **Not a search agent** — it's a document extraction platform
- - Focused on financial/legal/research document parsing, not web search
- - Requires running its own platform (`./start-oe.sh`); not lightweight
- - No search backend at all

---

## 5. Blog-writer-multi-agent (Abdulbasit110)
**URL**: https://github.com/Abdulbasit110/Blog-writer-multi-agent
**Stars**: unknown | **Language**: Python + TypeScript

### Architecture
- CrewAI multi-agent system: Planner → Writer → Editor
- Gemini 2.0-Flash-EXP as LLM
- Serper Web Search for real-time data
- FastAPI backend + Next.js frontend (full-stack)
- Jupyter Notebook (`crewai.ipynb`) for AI logic

### Search Backends
- **Serper Web Search** (Google Search API wrapper)

### Key Strengths vs Baseline
- + End-to-end pipeline: web search → content generation → editing
- + Full-stack application (Next.js + Shadcn UI + Tailwind)
- + CrewAI orchestration (more structured than LangGraph for sequential tasks)
- + References automatically included in output
- - **Single purpose**: blog writing only; not a general search agent
- - Requires Gemini API key + Serper API key
- - No local LLM option
- - Jupyter Notebook as server; not production-grade

---

## 6. local-llm-searxng-agent (Dev-TechT)
**URL**: https://github.com/Dev-TechT/local-llm-searxng-agent
**Stars**: unknown | **License**: MIT | **Language**: Python

### Architecture
- CLI-based, single-file agent (`agent.py` ~250 lines)
- Podman container for SearXNG
- Keyword-triggered search detection (SEARCH_TRIGGER_KEYWORDS, IMAGE_SEARCH_TRIGGER_KEYWORDS)
- Animated waiting indicator (threading-based)

### agent.py Deep Dive
```
main() loop:
  1. get_search_type(prompt) → NONE, TEXT, or IMAGE
  2. If IMAGE: perform_searxng_search → display URLs, skip LLM
  3. If TEXT: perform_searxng_search → query_local_lm with search context
  4. query_local_lm builds: System Prompt + History + (search_context + prompt)
  5. Removes <think> tags from DeepSeek/other reasoning models
  6. Maintains conversation_history list
```

### Config (config.py)
- `LOCAL_LM_URL`: http://127.0.0.1:1234/v1/chat/completions (LM Studio default)
- `SEARXNG_URL`: http://127.0.0.1:8080
- `SEARCH_TRIGGER_KEYWORDS`: ["latest", "current", "today", "recent", "news", "price of", "stock", "weather", ...]
- `IMAGE_SEARCH_TRIGGER_KEYWORDS`: ["image of", "picture of", ...]
- `SEARXNG_PARAMS`: format=json, engines=google,bing,duckduckgo, safesearch=0
- `MAX_SEARCH_RESULTS`: 5
- `REQUEST_TIMEOUT`: 15s
- `SYSTEM_PROMPT`: "You are a helpful assistant that can use web search results..."

### Key Strengths vs Baseline
- + **Fully local**: LLM + SearXNG both run locally; zero API costs
- + OpenAI-compatible API; works with Ollama, LM Studio, Jan, etc.
- + Dedicated image search mode
- + Conversation history maintained across turns
- + Cleans reasoning model artifacts (remove_think_tags)
- + Clean, minimal codebase (~250 lines)
- + Podman setup script included
- - **Keyword-based trigger detection** (crude; misses implicit search needs)
- - No content extraction from fetched pages; uses snippets only
- - No adaptive research depth
- - CLI only; no MCP/tool interface
- - No source reliability tracking
- - Hard-coded search parameters

---

## Comparison Matrix (vs kindly-web-search-mcp baseline)

| Feature | baseline | agentic-search | SurfAgent | AI-web-scraper | open-extract | blog-writer | local-searxng |
|---|---|---|---|---|---|---|---|
| Search backends | 7+ | 2 | 1 | 0 (LLM-driven) | 0 | 1 | 1 |
| SearXNG | Yes | No | No | No | No | No | Yes |
| Local LLM | No | No | Yes (Ollama) | Via LiteLLM | Any | No | Yes |
| Multi-agent orchestration | No | Yes (Supervisor) | No | No | No | Yes (CrewAI) | No |
| Image search | No | No | Yes (Vision) | No | No | No | Yes |
| Content extraction | Basic | process_content | Selenium+fetch | llm-reader | Schema-based | Serper | Snippets only |
| Caching | No | No | Memory-based | No | Yes | No | No |
| MCP interface | Yes | No | No | No | No | No | No |
| Adaptive depth | No | No | Yes | No | No | No | No |
| Source reliability | No | No | Yes | No | No | No | No |

## Key Insights for kindly-web-search-mcp Improvement

1. **Adaptive Research Depth** (from SurfAgent): Instead of fixed `num_results`, adjust search depth
   based on query complexity and source quality thresholds.

2. **Schema-Based Extraction** (from open-extract): Allow callers to specify extraction schemas
   as MCP parameters; return structured JSON instead of only markdown.

3. **Source Reliability Scoring** (from SurfAgent): Track domain success rates over time;
   use to re-rank results across sessions.

4. **Multi-Agent Mode** (from agentic-search): Offer a supervisor-routing mode where
   the MCP server can orchestrate search → extract → synthesize pipelines.

5. **Local LLM Path** (from local-llm-searxng-agent): Already have SearXNG; adding
   an OpenAI-compatible local LLM endpoint for summarization would complete the stack.

6. **Image Search** (from SurfAgent + local-searxng): Already possible with SearXNG's
   image categories. Expose as a first-class MCP tool.

7. **Caching** (from open-extract): Cache previously fetched URLs with TTL to reduce
   redundant network calls.

8. **Content Relevance Assessment** (from SurfAgent): Use LLM to score each result's
   relevance to the query; filter before returning to caller.
