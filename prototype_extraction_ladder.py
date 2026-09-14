"""Prototype extraction ladder: selection -> conversion split.

Throwaway prototype for benchmarking (not wired into fetch pipeline).
Compares: current ladder vs rs-trafilatura (+precision/recall) vs
rs-cleanHTML + lxml/markdownify P2 converter vs raw html_to_markdown.

Usage:
    uv run python prototype_extraction_ladder.py <url>...
"""

from __future__ import annotations

import re
import sys

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as _md

try:
    import rs_trafilatura  # type: ignore[import-not-found]
except Exception:
    rs_trafilatura = None  # type: ignore

sys.path.insert(0, "src")
from kindly_web_search_mcp_server.content.html_extract import extract_html_as_markdown  # noqa: E402

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124 Safari/537.36",
    "Accept": "text/html,*/*;q=0.8",
}

_MD_HEAD = re.compile(r"\s{0,3}#{1,6}\s+\S")
_MD_LIST = re.compile(r"\s*(?:[-+*]|\d+[.)])\s+\S")


def p2_convert(html: str) -> str:
    """P2 converter: lxml + extended denylist + ATX/table_header opts."""
    soup = BeautifulSoup(html or "", "lxml")
    for el in soup(["script", "style", "nav", "header", "footer", "aside", "form", "iframe", "noscript"]):
        el.decompose()
    return _md(
        str(soup),
        heading_style="ATX",
        bullets="-",
        strip=["script", "style", "nav", "header", "footer", "aside", "form", "iframe", "noscript"],
    )


def struct(md_text: str) -> dict:
    lines = [line for line in md_text.splitlines() if line.strip()]
    return {
        "chars": len(md_text),
        "words": len(md_text.split()),
        "lines": len(lines),
        "fences": md_text.count("```"),
        "pipes": sum(1 for line in lines if "|" in line),
        "heads": sum(1 for line in lines if _MD_HEAD.match(line)),
        "lists": sum(1 for line in lines if _MD_LIST.match(line)),
    }


def code_fidelity(source_html: str, markdown: str) -> dict:
    """Byte-fidelity spot check: are source <pre> blocks present verbatim-ish."""
    pres = re.findall(r"<pre[^>]*>(.*?)</pre>", source_html, re.I | re.S)
    if not pres:
        return {"n_source_pre": 0}
    def _clean(s: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()[:120]
    hits = sum(1 for p in pres if _clean(p)[:40] and _clean(p)[:40] in re.sub(r"\s+", " ", markdown))
    return {"n_source_pre": len(pres), "hits": hits}


async def fetch(url: str) -> str:
    async with httpx.AsyncClient(headers=HEADERS, timeout=20.0, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.text[:500000]


async def run(url: str) -> None:
    html = await fetch(url)
    print(f"\n===== {url} html={len(html)} =====")
    cur = extract_html_as_markdown(html, url=url)
    print("current      ", struct(cur), code_fidelity(html, cur))
    print("  head:", cur[:300].replace("\n", " | "))
    if rs_trafilatura is None:
        print("rs-trafilatura not installed")
        return
    for name, kw in [("rs-def", {}), ("rs-prec", {"favor_precision": True}), ("rs-rec", {"favor_recall": True})]:
        x = rs_trafilatura.extract(html, url=url, output_markdown=True, include_links=True, include_tables=True, **kw)
        m = x.content_markdown or x.main_content or ""
        print(f"{name:12s} type={x.page_type} q={float(x.extraction_quality or 0):.2f}", struct(m), code_fidelity(html, m))
    ch = rs_trafilatura.clean_html(html)
    print("clean_html len=", len(ch), struct(p2_convert(ch)))


if __name__ == "__main__":
    import asyncio

    urls = sys.argv[1:] or ["https://docs.python.org/3/library/asyncio-task.html"]
    for u in urls:
        asyncio.run(run(u))
