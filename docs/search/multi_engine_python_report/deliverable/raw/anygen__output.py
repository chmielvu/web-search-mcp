"""Output formatters: table, json, markdown, urls."""
from __future__ import annotations

import json
import sys
from typing import Any, Iterable

from rich.console import Console
from rich.table import Table

from hsearch.models import SearchResult


def _truncate(s: str, n: int) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def render_table(results: Iterable[SearchResult], console: Console | None = None) -> None:
    console = console or Console()
    table = Table(show_lines=False, header_style="bold cyan", expand=True)
    table.add_column("#", justify="right", style="dim", no_wrap=True, width=3)
    table.add_column("Provider(s)", style="magenta", no_wrap=True)
    table.add_column("Title", style="bold")
    table.add_column("Snippet", overflow="fold")
    table.add_column("URL", style="blue", overflow="fold")

    for i, r in enumerate(results, 1):
        srcs = ",".join(r.sources or [r.provider])
        # Prefer summary when present (it's the curated short version).
        snippet_src = r.summary or r.snippet
        table.add_row(
            str(i),
            srcs,
            _truncate(r.title, 80),
            _truncate(snippet_src, 200),
            r.url,
        )
    console.print(table)


def render_json(
    results: Iterable[SearchResult],
    meta: dict[str, Any] | None = None,
    errors: dict[str, str] | None = None,
) -> str:
    payload: dict[str, Any] = {}
    if meta is not None:
        payload["meta"] = meta
    payload["results"] = [r.to_dict() for r in results]
    if errors:
        payload["errors"] = errors
    return json.dumps(payload, ensure_ascii=False, indent=2)


def render_jsonl(results: Iterable[SearchResult]) -> str:
    return "\n".join(
        json.dumps(r.to_dict(), ensure_ascii=False) for r in results
    )


# Markdown render limits — keep terminal-friendly by default, but `--raw`
# bumps content limit so users get the full page they explicitly asked for.
MD_SNIPPET_MAX = 500
MD_CONTENT_MAX = 50000  # generous limit for --raw/extract — user explicitly asked for full content


def render_markdown(results: Iterable[SearchResult]) -> str:
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        srcs = ", ".join(r.sources or [r.provider])
        title = r.title or r.url
        lines.append(f"### {i}. [{title}]({r.url})")
        lines.append(f"_sources: {srcs}_")
        if r.published:
            lines.append(f"_published: {r.published}_")
        if r.summary:
            lines.append("")
            lines.append("**Summary:** " + _truncate(r.summary, MD_SNIPPET_MAX))
        if r.snippet:
            lines.append("")
            lines.append(_truncate(r.snippet, MD_SNIPPET_MAX))
        if r.content:
            lines.append("")
            # Quote-block, but allow much more than snippet — this is the
            # full-page content the user opted into via --raw / --extract-top.
            content = _truncate(r.content, MD_CONTENT_MAX)
            lines.append("> " + content.replace("\n", "\n> "))
        lines.append("")
    return "\n".join(lines)


def render_urls(results: Iterable[SearchResult]) -> str:
    return "\n".join(r.url for r in results if r.url)


def emit(
    results: list[SearchResult],
    fmt: str,
    console: Console | None = None,
    meta: dict[str, Any] | None = None,
    errors: dict[str, str] | None = None,
) -> None:
    fmt = fmt.lower()
    if fmt == "table":
        render_table(results, console=console)
    elif fmt == "json":
        sys.stdout.write(render_json(results, meta=meta, errors=errors) + "\n")
    elif fmt == "jsonl":
        sys.stdout.write(render_jsonl(results) + "\n")
    elif fmt in ("md", "markdown"):
        sys.stdout.write(render_markdown(results) + "\n")
    elif fmt == "urls":
        sys.stdout.write(render_urls(results) + "\n")
    else:
        raise ValueError(f"unknown format: {fmt}")
