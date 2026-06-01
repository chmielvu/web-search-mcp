import asyncio
import sys

sys.path.insert(0, "src")

from kindly_web_search_mcp_server.search.academic_s2 import search_semanticscholar


async def test():
    print("Testing S2 search (60s timeout)...")
    try:
        results = await asyncio.wait_for(
            search_semanticscholar("reinforcement learning", limit=3),
            timeout=60,
        )
        print(f"OK: {len(results)} results")
        for r in results:
            print(f"  - {r.title[:80]}")
    except asyncio.TimeoutError:
        print("TIMEOUT after 60s")
    except Exception as e:
        print(f"Error: {type(e).__name__}: {e}")


asyncio.run(test())
