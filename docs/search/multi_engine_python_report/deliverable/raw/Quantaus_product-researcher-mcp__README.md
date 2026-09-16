# Product Researcher MCP Server

An MCP server that gives Claude Code web search and product research capabilities. Never leave your terminal to research a product again.

## What It Does

5 tools, one server:

| Tool | What It Does |
|------|-------------|
| `research_product` | Full product brief — features, pricing, tech, API |
| `compare_products` | Side-by-side comparison of 2-5 products |
| `lookup_pricing` | Focused pricing/plans lookup |
| `find_alternatives` | Find competitors and alternatives |
| `search_web` | Raw web search for anything else |

## Quick Setup

### 1. Install dependencies

```bash
cd product-researcher-mcp
pip install -r requirements.txt
```

### 2. Pick a search provider and get an API key

| Provider | Free Tier | Best For | Sign Up |
|----------|-----------|----------|---------|
| **Tavily** | 1,000/month | AI agents (returns summaries) | [tavily.com](https://tavily.com) |
| **Brave** | 2,000/month | Privacy, good free tier | [brave.com/search/api](https://brave.com/search/api/) |
| **Serper** | 2,500 total | Google-quality results | [serper.dev](https://serper.dev) |

### 3. Set your environment variables

```bash
cp .env.example .env
# Edit .env — set SEARCH_PROVIDER and the matching API key
```

### 4. Add to Claude Code

Add this to your Claude Code MCP config (`~/.claude/claude_desktop_config.json` or project-level `.mcp.json`):

```json
{
  "mcpServers": {
    "product-researcher": {
      "command": "python",
      "args": ["/full/path/to/product-researcher-mcp/server.py"],
      "env": {
        "SEARCH_PROVIDER": "tavily",
        "TAVILY_API_KEY": "tvly-your-key-here"
      }
    }
  }
}
```

Or if using Brave:

```json
{
  "mcpServers": {
    "product-researcher": {
      "command": "python",
      "args": ["/full/path/to/product-researcher-mcp/server.py"],
      "env": {
        "SEARCH_PROVIDER": "brave",
        "BRAVE_API_KEY": "BSA-your-key-here"
      }
    }
  }
}
```

### 5. Test it

In Claude Code, just ask:

```
research Kling AI
```
```
compare Supabase vs Firebase vs PlanetScale
```
```
what's the pricing for Vercel
```
```
find alternatives to Midjourney for AI image generation
```

## How It Works

```
You ask Claude Code
    → Claude calls research_product tool
        → MCP server builds smart search queries
            → Hits your search API (Tavily/Brave/Serper)
                → Deduplicates & formats results
                    → Returns structured markdown brief
                        → Claude synthesizes the answer for you
```

The server is search-API-agnostic. Swap providers any time by changing one env var.

## File Structure

```
product-researcher-mcp/
├── server.py          # The MCP server (all tools + search adapters)
├── requirements.txt   # Python dependencies
├── .env.example       # Config template
└── README.md          # You're here
```

## Extending

Want to add a new search provider? Add a function following the pattern:

```python
async def _search_newprovider(query: str, max_results: int) -> List[Dict[str, Any]]:
    # Hit the API, return list of {"title": ..., "content": ..., "url": ...}
    pass
```

Then add it to the `providers` dict in the `search()` function. Done.

## License

MIT

## Author

Built by [Quantaus](https://github.com/Quantaus)
