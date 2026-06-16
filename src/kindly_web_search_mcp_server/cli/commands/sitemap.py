from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..output import emit_json
from ..services.sitemap import fetch_semantic_sitemap_payload


sitemap_app = typer.Typer(no_args_is_help=True)


@sitemap_app.command("generate")
def generate_cmd(
    url: Annotated[str, typer.Option("--url", help="Website URL to crawl.")],
    max_pages: Annotated[int, typer.Option("--max-pages")] = 100,
    max_depth: Annotated[int, typer.Option("--max-depth")] = 3,
    heading_preview_chars: Annotated[int, typer.Option("--heading-preview-chars")] = 200,
    generate_llms_txt: Annotated[
        bool,
        typer.Option("--generate-llms-txt/--no-generate-llms-txt"),
    ] = False,
) -> None:
    """Crawl a website and extract a structured heading hierarchy per page.

    Uses Crawl4AI for sitemap discovery and browser-based crawling.
    Returns page URLs, titles, heading-based sections with text previews,
    and crawl statistics.

    Set --generate-llms-txt to also produce llms.txt-formatted markdown.
    """
    try:
        payload = asyncio.run(
            fetch_semantic_sitemap_payload(
                url,
                max_pages=max_pages,
                max_depth=max_depth,
                heading_preview_chars=heading_preview_chars,
                generate_llms_txt=generate_llms_txt,
            )
        )
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=str(exc),
            hint="Check the URL and Crawl4AI dependencies. Run `web-search-cli doctor` to verify readiness.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={
                "command": "sitemap generate",
                "url": url,
                "exception_type": type(exc).__name__,
            },
        ) from exc
    emit_json(payload, command="sitemap generate")


def register(app: typer.Typer) -> None:
    app.add_typer(sitemap_app, name="sitemap")
