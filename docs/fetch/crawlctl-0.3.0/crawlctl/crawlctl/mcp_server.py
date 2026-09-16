"""FastMCP server: scrape -> clean -> RAG-ready Markdown for AI agents.

Package: PyPI ``fastmcp`` (standalone FastMCP 2.x/4.x, gofastmcp.com).
Import is ``from fastmcp import FastMCP`` — the official ``mcp`` SDK v1
bundled FastMCP at ``mcp.server.fastmcp`` but SDK v2 removed that path.

Transports: stdio (default, for Claude Desktop / Claude Code / Cursor) and
streamable HTTP (``--http``), plus a ``GET /health`` custom route for HTTP
deploys.

Tool surface (5 tools, agent-first: few tools, rich params, honest
annotations, structured dict returns -> structuredContent):

* capabilities()   — server discovery + capability matrix + tool guide
* scrape()         — 1..n URLs -> cleaned, scored markdown (+files)
* crawl_site()     — sitemap/llms.txt/URL -> corpus of front-mattered .md
* clean_text()     — clean + score markdown you already have
* corpus_status()  — manifest summary for a corpus
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator, List, Optional

try:
    from fastmcp import FastMCP, Context
    from fastmcp.exceptions import ToolError

    FASTMCP_FLAVOR = "standalone"
except ImportError:  # pragma: no cover - official SDK v1 fallback
    from mcp.server.fastmcp import FastMCP, Context  # type: ignore

    class ToolError(RuntimeError):  # type: ignore
        pass

    FASTMCP_FLAVOR = "mcp-sdk-v1"

try:
    from mcp.types import ToolAnnotations
except ImportError:  # pragma: no cover
    ToolAnnotations = dict  # type: ignore

from . import __version__
from .client import Crawl4AIClient, Crawl4AIError
from .service import (InputError, capabilities as _capabilities,
                      clean_text as _clean_text, corpus_status as _corpus_status,
                      crawl_site as _crawl_site, scrape as _scrape)


@asynccontextmanager
async def lifespan(server) -> AsyncIterator[dict]:
    client = Crawl4AIClient()
    profile = None
    try:
        profile = await client.detect()
    except Exception as exc:
        # stay up: tools surface a precise connection error per call
        print(f"[crawl4ai-rag-mcp] server detection failed: {exc}", file=sys.stderr)
    try:
        yield {"client": client, "profile": profile}
    finally:
        await client.aclose()


mcp = FastMCP(
    "crawl4ai-rag-mcp",
    instructions=(
        "Web scraping -> cleaned, quality-scored, RAG-ready Markdown documents "
        "via a self-hosted Crawl4AI server.\n\n"
        "TOOLS\n"
        "- capabilities(): call once to discover the server (version, endpoints, "
        "cleaner backends) and read the tool guide.\n"
        "- scrape(urls, ...): 1..100 URLs -> cleaned+scored markdown inline. "
        "mode='browser' (default) uses the tuned extraction config server-side; "
        "mode='fast' is the cheap /md path; mode='bm25' needs query=. Pass "
        "save_dir to also write full front-mattered .md files.\n"
        "- crawl_site(source, out_dir, ...): build a corpus from a page, "
        "sitemap.xml, llms.txt or URL-list file. Writes pages/*.md (publish), "
        "quarantine/*.md (low score), manifest.json (incremental), "
        "report-latest.json. two_pass=true warms the per-site boilerplate DB "
        "first (recommended for the first crawl of a large site).\n"
        "- clean_text(markdown, ...): clean + score content you already have.\n"
        "- corpus_status(corpus_dir): per-status counts + lowest-score sample.\n\n"
        "OUTPUT CONTRACT: every file and returned document carries front matter "
        "(title, source_url, content_type, score, content_hash, outline, links) "
        "so downstream chunkers/indexers can ingest without re-parsing. "
        "Chunking/indexing are intentionally out of scope.\n\n"
        "FAILURES: errors are actionable (auth hints, config-gate explanations, "
        "batch-size limits). Scores band into publish/review/quarantine."
    ),
    lifespan=lifespan,
)


def _ctx(ctx) -> tuple:
    lc = ctx.request_context.lifespan_context
    return lc.get("client"), lc.get("profile")


def _wrap(fn):
    async def inner(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except InputError as exc:
            raise ToolError(f"Invalid input: {exc}") from exc
        except Crawl4AIError as exc:
            raise ToolError(f"Crawl4AI server error: {exc}") from exc
        except Exception as exc:
            raise ToolError(
                f"{type(exc).__name__}: {exc}. If this persists, check the "
                "Crawl4AI server logs (docker logs <container>) and verify "
                "CRAWL4AI_BASE_URL."
            ) from exc
    return inner


_RO = ToolAnnotations(title=None, readOnlyHint=True, destructiveHint=False,
                      idempotentHint=True, openWorldHint=False) if ToolAnnotations is not dict else {}
_WEB_RO = ToolAnnotations(title=None, readOnlyHint=True, destructiveHint=False,
                          idempotentHint=False, openWorldHint=True) if ToolAnnotations is not dict else {}
_WEB_RW = ToolAnnotations(title=None, readOnlyHint=False, destructiveHint=False,
                          idempotentHint=False, openWorldHint=True) if ToolAnnotations is not dict else {}


@mcp.tool(annotations=_WEB_RO)
async def capabilities(ctx: Context) -> str:
    """Discover the Crawl4AI server: version, live endpoints, capability flags,
    available cleaner backends, and the tool usage guide. Call this first."""
    client, _ = _ctx(ctx)
    return _j(await _wrap(_capabilities)(client))


@mcp.tool(annotations=_WEB_RO)
async def scrape(ctx: Context, urls: List[str], mode: str = "auto",
                 query: Optional[str] = None, cleaner: str = "auto",
                 link_mode: str = "keep", image_policy: str = "keep",
                 citation_mode: str = "auto", content_type: str = "auto",
                 save_dir: Optional[str] = None, max_chars: int = 24000,
                 options: Optional[dict] = None, fit: bool = True) -> str:
    """Scrape 1..100 URLs and return CLEAN, scored, RAG-ready markdown per URL.

    mode: auto|browser|fast|bm25 (browser = tuned server-side extraction with
    PruningContentFilter; fast = cheap /md endpoint; bm25 needs query=).
    cleaner: auto|native|markdownchef. link_mode: keep|text|drop.
    image_policy: keep|alt_only|drop. content_type hint: docs|wiki|blog|news|generic.
    Inline markdown truncates at max_chars; pass save_dir to also write full
    front-mattered .md files (paths returned per URL). options keys:
    css_selector, excluded_selector, target_elements, wait_for, js_code,
    word_count_threshold, pruning, extra.
    """
    client, _ = _ctx(ctx)
    return _j(await _wrap(_scrape)(
        client, urls, mode=mode, query=query, cleaner=cleaner, link_mode=link_mode,
        image_policy=image_policy, citation_mode=citation_mode,
        content_type=content_type, save_dir=save_dir, max_chars=max_chars,
        options=options, fit=fit))


@mcp.tool(annotations=_WEB_RW)
async def crawl_site(ctx: Context, source: str, out_dir: str = "./corpus",
                     deep: bool = False, max_depth: int = 2, max_pages: int = 200,
                     include_external: bool = False,
                     url_include: Optional[List[str]] = None,
                     url_exclude: Optional[List[str]] = None,
                     cleaner: str = "auto", link_mode: str = "keep",
                     image_policy: str = "keep", citation_mode: str = "auto",
                     extractor: str = "auto", content_type: Optional[str] = None,
                     min_score: int = 40, keep_raw: bool = False,
                     two_pass: bool = False, force: bool = False,
                     fit: bool = True, options: Optional[dict] = None) -> str:
    """Build a corpus of CLEAN markdown documents from a page, sitemap.xml,
    llms.txt, or URL-list file.

    Writes <out_dir>/pages/*.md (front matter: title, source_url,
    content_type, score, content_hash, outline, links, cleaning edits),
    quarantine/ for low-score pages, raw/ mirror if keep_raw=true,
    state/boilerplate.json (per-site template DB), manifest.json (incremental;
    unchanged URLs skipped unless force=true), report-latest.json.
    deep=true follows internal links (client-side BFS, max_depth/max_pages caps).
    two_pass=true warms the boilerplate DB first (recommended for first crawl
    of a large site). Chunking/indexing happen downstream.
    """
    client, _ = _ctx(ctx)

    def on_progress(done: int, total: int, message: str):
        try:
            ctx.report_progress(done, total, message)
        except Exception:
            pass

    return _j(await _wrap(_crawl_site)(
        client, source, out_dir, deep=deep, max_depth=max_depth,
        max_pages=max_pages, include_external=include_external,
        url_include=url_include, url_exclude=url_exclude, cleaner=cleaner,
        link_mode=link_mode, image_policy=image_policy,
        citation_mode=citation_mode, extractor=extractor,
        content_type=content_type, min_score=min_score, keep_raw=keep_raw,
        two_pass=two_pass, force=force, fit=fit, options=options,
        progress=lambda m: print(f"[crawl_site] {m}", flush=True),
        progress_cb=on_progress,
    ))


@mcp.tool(annotations=_RO)
async def clean_text(ctx: Context, markdown: str, source_url: Optional[str] = None,
                     cleaner: str = "auto", link_mode: str = "keep",
                     image_policy: str = "keep", citation_mode: str = "auto",
                     content_type: str = "auto", save_path: Optional[str] = None,
                     max_chars: int = 24000) -> str:
    """Clean + score markdown you already have (no scraping). Returns cleaned
    text, content type, edit report and 0-100 publish/review/reject score.
    save_path writes a front-mattered .md file."""
    return _j(await _wrap(_clean_text)(
        markdown, source_url=source_url, cleaner=cleaner, link_mode=link_mode,
        image_policy=image_policy, citation_mode=citation_mode,
        content_type=content_type, save_path=save_path, max_chars=max_chars))


@mcp.tool(annotations=_RO)
async def corpus_status(ctx: Context, corpus_dir: str) -> str:
    """Manifest summary for a corpus built by crawl_site: per-status counts,
    last run stats, and the lowest-score pages for review."""
    return _j(await _wrap(_corpus_status)(corpus_dir))


def _j(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


@mcp.custom_route("/health", methods=["GET"])
async def _health(_request=None):
    from starlette.responses import JSONResponse

    return JSONResponse({"status": "ok", "server": "crawl4ai-rag-mcp",
                         "version": __version__})


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="crawl4ai-rag-mcp",
        description="FastMCP server: self-hosted Crawl4AI -> clean RAG-ready Markdown",
    )
    ap.add_argument("--http", action="store_true",
                    help="serve streamable HTTP instead of stdio (default endpoint /mcp)")
    ap.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT", "8051")))
    ap.add_argument("--base-url", default=None,
                    help="Crawl4AI server base URL (or env CRAWL4AI_BASE_URL)")
    ap.add_argument("--version", action="store_true", help="print version and exit")
    args = ap.parse_args()

    if args.version:
        print(f"crawl4ai-rag-mcp {__version__} (fastmcp flavor: {FASTMCP_FLAVOR})")
        return
    if args.base_url:
        os.environ["CRAWL4AI_BASE_URL"] = args.base_url

    if args.http:
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
