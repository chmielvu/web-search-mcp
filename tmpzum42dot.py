
import asyncio, time, sys, uuid, httpx
from fastmcp import FastMCP
from kindly_web_search_mcp_server.search.contracts import SearchRun, WebSearchRequest
from kindly_web_search_mcp_server.utils.http_client import get_http_client
from kindly_web_search_mcp_server.search.planning import plan_search

mcp = FastMCP('test')

@mcp.tool
async def test_plan_search(query: str, research_goal: str) -> str:
    http_client = await get_http_client()
    request = WebSearchRequest(query=query, research_goal=research_goal, rewrite=True)
    run = SearchRun(request=request, http_client=http_client, run_key=str(uuid.uuid4()))
    print(f'[test] plan_search (no span) starting...', file=sys.stderr, flush=True)
    t0 = time.monotonic()
    try:
        plan = await asyncio.wait_for(plan_search(run), timeout=30)
        print(f'[test] plan_search done in {time.monotonic()-t0:.1f}s', file=sys.stderr, flush=True)
        return f'ok: {len(plan.branches)} branches'
    except asyncio.TimeoutError:
        print(f'[test] TIMEOUT after {time.monotonic()-t0:.1f}s', file=sys.stderr, flush=True)
        raise
    except Exception as e:
        print(f'[test] FAILED: {type(e).__name__}: {e}', file=sys.stderr, flush=True)
        raise

if __name__ == '__main__':
    mcp.run(transport='stdio')
