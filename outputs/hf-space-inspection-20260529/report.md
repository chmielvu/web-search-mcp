# HF Space Inspection Report

I inspected the requested Hugging Face Spaces by downloading the public repo contents with the Hugging Face CLI and scanning the code for search, fetch, ranking, MCP, YouTube, PDF, and utility patterns.

Repos inspected: 50
Download failures: 0

## High-signal takeaways

- `Nymbo/*` is the richest cluster for reusable snippets: it separates `WebSearch`, `Fetch`, `web-scraper`, `HTML-to-Markdown`, `dom-to-semantic-markdown`, `Text-Scraper`, `youtube_splitter`, and `JSON-Crawl` into distinct utilities. That separation maps well to this MCP's current search vs fetch boundary.
- `Agents-MCP-Hackathon/web-search-mcp` and `Svngoku/jina-search-mcp` are the closest MCP analogues. They are the places to mine for tool contracts, prompt shapes, and lightweight result shaping.
- The `search_*` demo spaces are mostly useful for orchestration ideas, not for core search quality. Many wrap a search backend in a UI, but the more interesting ones explicitly separate planning, search, scraping, and synthesis.
- Several spaces use the same design pressure points that this MCP has: provider fallback, ranking quality, HTML cleaning, PDF extraction, and YouTube transcript handling.

## Best reusable code patterns

### MayuraKrishna/Search_Engine
Score: 17 | Files: 15 | Entry points: app.py, Dockerfile, README.md
Keywords: agent=33, mcp=20, search=18, fetch=12, utility=3, pdf=2
- `Dockerfile:21` [mcp, agent] ENTRYPOINT ["streamlit", "run", "src/streamlit_app.py", "--server.port=8501", "--server.address=0.0.0.0"]
- `README.md:2` [search] title: Search Engine
- `README.md:9` [agent] - streamlit
- `README.md:11` [agent] short_description: Streamlit template space

### Agents-MCP-Hackathon/search-web-MCP-server
Score: 16 | Files: 67 | Entry points: app.py, pyproject.toml, README.md
Keywords: search=220, fetch=44, mcp=40, agent=16, utility=5, youtube=1
- `README.md:2` [search, mcp] title: web search MCP-server
- `README.md:6` [search, mcp] short_description: MCP server for general and custom search on web
- `pyproject.toml:2` [search, mcp] name = "search-tool"
- `src/core/types.py:17` [search, fetch] DEFAULT_SYSTEM_PROMPT = """You are an intelligent assistant designed to answer user questions strictly based on the provided list of search result items. Each item includes a title, description, content, and URL. You mus

### Jabbastin/Search_ranking_engine
Score: 16 | Files: 57 | Entry points: Dockerfile, pyproject.toml, README.md
Keywords: search=387, agent=72, pdf=56, mcp=30, fetch=22, utility=5
- `search_ranking_env.py:9` [search, pdf, agent] The agent acts by providing a ranked list of document IDs based on a query
- `server/models.py:7` [fetch, pdf] text: str = Field(..., description="Content or title of the document")
- `test_models.py:5` [search, pdf] doc1 = Document(id="d1", text="Sample Doc 1", relevance=0.8)
- `Dockerfile:17` [search] LABEL maintainer="search-ranking-env"

### Nymbo/Tools
Score: 16 | Files: 51 | Entry points: app.py, README.md, requirements.txt
Keywords: search=428, mcp=216, fetch=194, agent=70, utility=59, youtube=34
- `Filesystem/Skills/music-downloader/SKILL.md:12` [search, mcp, youtube] 1. **No URL provided?** → Use `Web_Search` tool (set search type to `videos`, prefer official YouTube links)
- `styles.css:10` [fetch, mcp, agent] content: "General purpose tools useful for any agent.";
- `Filesystem/Skills/music-downloader/SKILL.md:3` [youtube, utility] description: Download audio/music from YouTube, SoundCloud, and 1000+ other platforms using yt-dlp. Use when users request downloading songs, extracting audio from videos, downloading playlists/albums, or converting vide
- `Modules/Agent_Skills.py:4` [mcp, agent] Agent Skills Module for Nymbo-Tools MCP Server.

### panda0007/Search_ENGINE_LLM_Agent_Tools
Score: 16 | Files: 13 | Entry points: app.py, README.md, requirements.txt
Keywords: mcp=28, agent=25, search=13, pdf=9, fetch=6, youtube=2
- `README.md:8` [search, mcp, agent] short_description: Search Engine Agents and Tools with LLM's
- `README.md:3` [search, mcp] title: Search Engine Agents and Tools
- `.github/workflows/main.yml:15` [fetch] fetch-depth: 0
- `LICENSE:6` [pdf] of this license document, but changing it is not allowed.

### Manasa1/VIDEO_SEARCH_SUMMARIZER
Score: 16 | Files: 9 | Entry points: app.py, README.md, requirements.txt
Keywords: youtube=49, agent=19, search=14, mcp=14, fetch=10, pdf=1
- `README.md:2` [search, youtube] title: VIDEO SEARCH SUMMARIZER
- `README.md:6` [agent] sdk: streamlit
- `app.py:1` [agent] import streamlit as st
- `app.py:2` [agent] from phi.agent import Agent

### Nymbo/Fetch
Score: 16 | Files: 9 | Entry points: app.py, README.md, requirements.txt
Keywords: fetch=43, mcp=6, agent=6, pdf=2, youtube=1, search=1
- `README.md:11` [search, fetch, mcp] short_description: A simple MCP server to fetch URLs without search engines.
- `app.py:8` [fetch, pdf] from readability import Document           # Readability algorithm to isolate main content
- `README.md:2` [fetch] title: Fetch
- `README.md:6` [agent] sdk: gradio

### Aryan2704/Leet-Search
Score: 15 | Files: 91 | Entry points: Dockerfile, README.md, requirements.txt
Keywords: search=57, fetch=41, utility=22, mcp=4
- `backend/__pycache__/main.py:10` [search, utility] app = FastAPI(title="LeetCode Vector Search API", version="1.0")
- `backend/app/main.py:8` [search, utility] app = FastAPI(title="LeetCode Vector Search API", version="1.0")
- `backend/app/scripts/populate_db.py:16` [search, fetch] embedding = get_embedding(problem.get('content', ''))
- `backend/app/scripts/update_data.py:10` [fetch, utility] logging.info("🔄 Starting LeetCode problem scrape...")

### Nymbo/Webscout
Score: 15 | Files: 67 | Entry points: app.py, Dockerfile, README.md
Keywords: search=241, mcp=120, utility=57, fetch=54, agent=27, youtube=4
- `webscout/DWEBS.py:32` [search, utility] description="(list[str]) Types of search results: `web`, `image`, `videos`, `news`",
- `webscout/cli.py:57` [fetch, utility] width = 300 if k in ("content", "href", "image", "source", "thumbnail", "url") else 78
- `app.py:7` [search] from fastapi import FastAPI, Query
- `app.py:15` [utility] class Image:

### lawhy/web_search
Score: 15 | Files: 51 | Entry points: Dockerfile, pyproject.toml, README.md
Keywords: search=99, mcp=80, fetch=39, utility=10, pdf=1, agent=1
- `README.md:2` [search, mcp] title: Web Search Environment Server
- `models.py:33` [search, fetch] content: str = Field(..., description="The formatted content of the search results or error message if the search failed")
- `openenv_web_search.egg-info/entry_points.txt:2` [search, mcp] server = web_search.server.app:main
- `server/web_search_tool.py:26` [search, mcp] """A tool for searching the web using Google Search API (via Serper.dev)."""

### Agents-MCP-Hackathon/web-search-mcp
Score: 14 | Files: 39 | Entry points: app.py, README.md, requirements.txt
Keywords: search=179, agent=29, mcp=28, utility=8, youtube=3, fetch=1
- `README.md:2` [search, mcp] title: Web Search Mcp
- `app_wrapper.py:21` [search, mcp] title="Web Search MCP",
- `requirements.txt:2` [mcp, agent] gradio[mcp]==5.33.0
- `README.md:6` [agent] sdk: gradio

### Felladrin/awesome-ai-web-search
Score: 14 | Files: 21 | Entry points: README.md
Keywords: search=70, agent=35, youtube=17, fetch=7, pdf=6, utility=6
- `.github/hf-space-config.yml:1` [search] title: Awesome AI Web Search
- `.github/hf-space-config.yml:3` [search] short_description: Curated list of AI-powered web search software
- `README.md:2` [search] title: Awesome AI Web Search
- `README.md:4` [search] short_description: Curated list of AI-powered web search software

## Suggested transfers to Kindly

1. Split utilities the way Nymbo does: keep search, fetch, HTML cleanup, PDF handling, YouTube handling, and JSON crawl as separate tools or modules, not one overloaded path.
2. Keep official-doc-first source selection as a first-class ranking signal. The search spaces frequently show that generic search results are not enough for technical queries.
3. Treat HTML-to-Markdown and semantic-markdown conversion as a dedicated normalization stage, not a side effect inside search.
4. Preserve tool contracts that return small result objects with `title`, `link`, and `snippet`, and defer full content to fetch tools.
5. Use a clear provider fallback policy. Several demo spaces silently degrade, which is exactly the behavior that causes confusing search output.
6. For YouTube, keep transcript retrieval separate from video discovery; the best spaces treat those as different tools.
7. For PDFs and documents, keep bounded extraction and chunk/window support instead of returning whole blobs.

## Telemetry and hygiene notes

- I found a number of spaces that are mostly UI wrappers with little reusable backend code. Those were still useful to confirm what not to overbuild into the MCP.
- Public repo downloads were successful for the spaces inspected in this pass; no auth issues blocked the scan.
- The analysis focused on code and config, not model outputs, so I excluded marketing copy and README-only content unless it encoded a concrete implementation pattern.