"""Entry point for FastMCP CLI (fastmcp run server.py).

This file has NO relative imports - it imports from the installed package.
FastMCP run imports this file directly and finds the `mcp` object.
"""

from kindly_web_search_mcp_server.server import mcp

__all__ = ["mcp"]