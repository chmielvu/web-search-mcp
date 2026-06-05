"""Direct MCP client test for kindly server."""
import asyncio
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> int:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-u", "-m", "kindly_web_search_mcp_server", "--transport", "stdio"],
    )
    print(f"Spawning: {params.command} {params.args}", file=sys.stderr, flush=True)
    try:
        async with stdio_client(params) as (read, write):
            print("stdio_client connected", file=sys.stderr, flush=True)
            async with ClientSession(read, write) as session:
                print("ClientSession ready, calling initialize", file=sys.stderr, flush=True)
                result = await asyncio.wait_for(session.initialize(), timeout=30)
                print(f"initialize OK protocol={result.protocolVersion}", file=sys.stderr, flush=True)
                tools = await asyncio.wait_for(session.list_tools(), timeout=30)
                print(f"tools/list returned {len(tools.tools)} tools", file=sys.stderr, flush=True)
                for t in tools.tools:
                    print(f"  - {t.name}", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
