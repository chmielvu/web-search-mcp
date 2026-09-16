"""hsearch — typer-based CLI (thin wrapper over engine.py)."""
from __future__ import annotations

import asyncio
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hsearch import __version__
from hsearch.cache import ResultCache
from hsearch.config import (
    ALL_PROVIDERS,
    PROVIDER_ENV,
    cache_dir,
    cache_ttl,
    configured_providers,
    get_key,
    timeout_seconds,
)
from hsearch.engine import (
    search as engine_search,
    answer as engine_answer,
    ground as engine_ground,
    find_similar as engine_find_similar,
    research as engine_research,
    research_streaming as engine_research_streaming,
    agent as engine_agent,
    map_site as engine_map_site,
    crawl_site as engine_crawl_site,
    account_usage as engine_account_usage,
    AgentResponse,
    AnswerResponse,
    GroundingResponse,
    ResearchResponse,
    SearchResponse,
    TraversalResponse,
)
from hsearch.extract import EXTRACT_PROVIDERS, extract_many
from hsearch.filters import Filters
from hsearch.models import SearchResult
from hsearch.output import emit
from hsearch.providers import list_providers
from hsearch.router import ALL_MODES

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="hsearch — unified CLI over 6 commercial search APIs.",
    rich_markup_mode="rich",
)
cache_app = typer.Typer(help="Cache utilities", no_args_is_help=True)
app.add_typer(cache_app, name="cache")

console = Console()
err_console = Console(stderr=True)


# --- helpers -----------------------------------------------------------------


def _cli_option_present(long_name: str, short_name: str | None = None) -> bool:
    for arg in sys.argv[1:]:
        if arg == long_name or arg.startswith(f"{long_name}="):
            return True
        if short_name and (arg == short_name or arg.startswith(short_name)):
            return True
    return False


def _print_errors(errors: dict[str, str]) -> None:
    if not errors:
        return
    for name, msg in errors.items():
        err_console.print(f"[yellow]![/] [bold]{name}[/]: {msg}")


# --- commands ----------------------------------------------------------------


def _version_cb(value: bool) -> None:
    if value:
        typer.echo(f"hsearch {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None, "--version", "-V", callback=_version_cb, is_eager=True, help="Show version."
    ),
) -> None:
    """hsearch — unified search over Brave, Serper, Exa, Tavily, Firecrawl, Jina."""


@app.command()
def search(
    query: str = typer.Argument(..., help="Query string."),
    provider: list[str] = typer.Option(
        None, "--provider", "-p",
        help=f"Provider(s) to query; repeat for multiple ({'|'.join(ALL_PROVIDERS)}).",
    ),
    mode: Optional[str] = typer.Option(
        None, "--mode", "-m", help=f"Routing mode ({'|'.join(ALL_MODES)})."
    ),
    all_providers: bool = typer.Option(
        False, "--all", help="Query every configured provider in parallel + merge."
    ),
    top: int = typer.Option(10, "--top", "-n", help="Max results per provider."),
    fmt: Optional[str] = typer.Option(
        None, "--format", "-f", help="Output: table | json | jsonl | markdown | urls (auto: json when piped, table in terminal)"
    ),
    no_cache: bool = typer.Option(False, "--no-cache", help="Disable result cache."),
    cache_ttl_opt: Optional[int] = typer.Option(
        None, "--cache-ttl", help="Override cache TTL in seconds for this call."
    ),
    time: Optional[str] = typer.Option(
        None, "--time", "-t", help="day|week|month|year or YYYY-MM-DD..YYYY-MM-DD"
    ),
    lang: Optional[str] = typer.Option(None, "--lang", "-l", help="ISO 639-1 (en, zh, ja, ...)"),
    region: Optional[str] = typer.Option(None, "--region", "-r", help="ISO 3166 (US, CN, ...)"),
    location: Optional[str] = typer.Option(
        None, "--location", help="Provider location hint (e.g. San Francisco,CA,US)."
    ),
    site: list[str] = typer.Option(None, "--site", help="Restrict to site(s); repeatable."),
    exclude: list[str] = typer.Option(None, "--exclude", help="Exclude site(s); repeatable."),
    extract_top: int = typer.Option(
        0, "--extract-top", help="Extract content of top-N merged results."
    ),
    extract_provider: str = typer.Option(
        "jina",
        "--extract-provider",
        help="Provider for --extract-top content fetch: jina | firecrawl.",
    ),
    agent: bool = typer.Option(
        False,
        "--agent",
        help="Agent-friendly preset: --format json --top 5 unless explicitly overridden.",
    ),
    answer: bool = typer.Option(
        False, "--answer", "-a",
        help="Ask Tavily for a synthesized answer (printed at top).",
    ),
    summary: bool = typer.Option(
        False, "--summary",
        help="Ask Exa/Firecrawl for per-result LLM summaries.",
    ),
    sources: Optional[str] = typer.Option(
        None, "--sources",
        help="Firecrawl multi-source (comma-sep): web,news,images.",
    ),
    goggles: list[str] = typer.Option(
        None, "--goggles",
        help="Brave: Goggle URL or inline definition; repeatable.",
    ),
    serper_type: Optional[str] = typer.Option(
        None, "--serper-type",
        help="Serper endpoint: search | news | images | videos | shopping | places | scholar | patents.",
    ),
    page: Optional[int] = typer.Option(
        None, "--page",
        help="Serper page number for paginated endpoints.",
    ),
    autocorrect: Optional[bool] = typer.Option(
        None, "--autocorrect/--no-autocorrect",
        help="Serper: enable or disable query autocorrection.",
    ),
    livecrawl: Optional[str] = typer.Option(
        None, "--livecrawl",
        help="Legacy Exa freshness alias: always|fallback|never (mapped to maxAgeHours).",
    ),
    auto: bool = typer.Option(
        False, "--auto",
        help="Tavily auto_parameters=True (let Tavily pick depth/topic).",
    ),
    raw: bool = typer.Option(
        False, "--raw",
        help="Tavily include_raw_content='markdown' — fills SearchResult.content.",
    ),
    retries: int = typer.Option(
        2, "--retries",
        help="Per-request retries on 429/5xx (exponential backoff).",
    ),
    fanout_timeout: Optional[float] = typer.Option(
        None, "--fanout-timeout",
        help="Max seconds to wait for all providers; returns partial results on timeout.",
    ),
    days: Optional[int] = typer.Option(
        None, "--days",
        help="Tavily news mode: results from past N days.",
    ),
    chunks_per_source: Optional[int] = typer.Option(
        None, "--chunks-per-source",
        help="Tavily chunks per source for advanced/fast depth (1-3).",
    ),
    additional_query: list[str] = typer.Option(
        None, "--additional-query",
        help="Exa extra query variation for deep-search modes; repeatable.",
    ),
    max_age_hours: Optional[int] = typer.Option(
        None, "--max-age-hours",
        help="Exa contents.maxAgeHours: 0 live, -1 cache-only, omit for default fallback.",
    ),
    highlights: bool = typer.Option(
        False, "--highlights",
        help="Exa contents.highlights=True for relevant excerpts.",
    ),
    context_threshold: Optional[str] = typer.Option(
        None, "--context-threshold",
        help="Brave LLM Context threshold: strict | balanced | lenient | disabled.",
    ),
    exact: bool = typer.Option(
        False, "--exact",
        help="Tavily exact_match=True — quoted phrases must appear verbatim (no synonyms).",
    ),
    depth: Optional[str] = typer.Option(
        None, "--depth",
        help="Tavily search_depth: basic | advanced | fast | ultra-fast.",
    ),
    exa_type: Optional[str] = typer.Option(
        None, "--exa-type",
        help="Exa type: auto | fast | instant | neural | deep-lite | deep | deep-reasoning.",
    ),
    category: Optional[str] = typer.Option(
        None, "--category",
        help="Exa category: company | publication | news | people | personal site | "
             "financial report. Retired names ('research paper', 'linkedin profile') "
             "are auto-mapped to their successors.",
    ),
    context: bool = typer.Option(
        False, "--context",
        help="Exa contents.context — return ONE pre-assembled LLM-ready context "
             "string across all results (surfaced as meta.context). Same as --mode rag.",
    ),
    context_max_characters: Optional[int] = typer.Option(
        None, "--context-max-chars",
        help="Cap the --context string (default 12000). Unbounded can exceed 160K chars.",
    ),
    include_favicon: bool = typer.Option(
        False, "--include-favicon",
        help="Tavily: return favicon URL per result.",
    ),
    include_usage: bool = typer.Option(
        False, "--include-usage",
        help="Tavily: include credit usage info in response meta.",
    ),
    include_images: bool = typer.Option(
        False, "--include-images",
        help="Tavily: include query/result images.",
    ),
    include_image_descriptions: bool = typer.Option(
        False, "--include-image-descriptions",
        help="Tavily: include image descriptions with --include-images.",
    ),
    answer_depth: Optional[str] = typer.Option(
        None, "--answer-depth",
        help="Tavily answer detail level: basic | advanced (requires --answer).",
    ),
    moderation: bool = typer.Option(
        False, "--moderation",
        help="Exa: enable content moderation to filter unsafe results.",
    ),
    livecrawl_timeout: Optional[int] = typer.Option(
        None, "--livecrawl-timeout",
        help="Exa: livecrawl timeout in milliseconds (default 10000).",
    ),
    ignore_invalid_urls: bool = typer.Option(
        False, "--ignore-invalid-urls",
        help="Firecrawl: exclude URLs that are invalid for follow-on scrape endpoints.",
    ),
    firecrawl_scrape_timeout: Optional[int] = typer.Option(
        None, "--firecrawl-scrape-timeout",
        help="Firecrawl scrapeOptions.timeout in milliseconds.",
    ),
    firecrawl_wait_for: Optional[int] = typer.Option(
        None, "--firecrawl-wait-for",
        help="Firecrawl scrapeOptions.waitFor in milliseconds.",
    ),
    firecrawl_parsers: Optional[str] = typer.Option(
        None, "--firecrawl-parsers",
        help="Firecrawl scrapeOptions.parsers, comma-separated (e.g. 'pdf').",
    ),
    firecrawl_redact_pii: bool = typer.Option(
        False, "--firecrawl-redact-pii",
        help="Firecrawl scrapeOptions.redactPII — redact personal info in scraped content.",
    ),
    jina_engine: Optional[str] = typer.Option(
        None, "--jina-engine",
        help="Jina X-Engine header for Reader/Search.",
    ),
    jina_respond_with: Optional[str] = typer.Option(
        None, "--jina-respond-with",
        help="Jina X-Respond-With header, e.g. no-content, markdown, readerlm-v2.",
    ),
    jina_target_selector: Optional[str] = typer.Option(
        None, "--jina-target-selector",
        help="Jina X-Target-Selector CSS selector.",
    ),
    jina_wait_for: Optional[str] = typer.Option(
        None, "--jina-wait-for",
        help="Jina X-Wait-For-Selector CSS selector.",
    ),
    jina_remove_selector: Optional[str] = typer.Option(
        None, "--jina-remove-selector",
        help="Jina X-Remove-Selector CSS selector.",
    ),
    jina_generated_alt: bool = typer.Option(
        False, "--jina-generated-alt",
        help="Jina: caption images with generated alt text.",
    ),
    safe_search: bool = typer.Option(
        False, "--safe-search",
        help="Tavily: filter adult/unsafe content (Enterprise only).",
    ),
    project_id: Optional[str] = typer.Option(
        None, "--project-id",
        help="Tavily: X-Project-ID header for per-project usage tracking.",
    ),
    firecrawl_only_clean_content: bool = typer.Option(
        False, "--firecrawl-clean-content",
        help="Firecrawl: LLM-based cleanup of residual boilerplate (beta).",
    ),
    firecrawl_max_age: Optional[int] = typer.Option(
        None, "--firecrawl-max-age",
        help="Firecrawl scrapeOptions.maxAge in ms (cache freshness threshold).",
    ),
    firecrawl_min_age: Optional[int] = typer.Option(
        None, "--firecrawl-min-age",
        help="Firecrawl scrapeOptions.minAge in ms (cache-only mode, set 1 for any cached).",
    ),
    firecrawl_block_ads: Optional[bool] = typer.Option(
        None, "--firecrawl-block-ads/--firecrawl-no-block-ads",
        help="Firecrawl: enable/disable ad and cookie popup blocking.",
    ),
    firecrawl_proxy: Optional[str] = typer.Option(
        None, "--firecrawl-proxy",
        help="Firecrawl proxy tier: basic | enhanced | auto.",
    ),
    firecrawl_question: Optional[str] = typer.Option(
        None, "--firecrawl-question",
        help="Firecrawl: ask a question about each scraped page (returns answer).",
    ),
    highlights_query: Optional[str] = typer.Option(
        None, "--highlights-query",
        help="Firecrawl/Exa: query string for highlights relevance.",
    ),
    firecrawl_store_in_cache: Optional[bool] = typer.Option(
        None, "--firecrawl-store-in-cache/--firecrawl-no-store-in-cache",
        help="Firecrawl scrapeOptions.storeInCache — cache scraped pages for reuse.",
    ),
    firecrawl_lockdown: Optional[bool] = typer.Option(
        None, "--firecrawl-lockdown/--firecrawl-no-lockdown",
        help="Firecrawl scrapeOptions.lockdown — restricted/hardened scrape mode.",
    ),
    firecrawl_zero_data_retention: Optional[bool] = typer.Option(
        None, "--firecrawl-zdr/--firecrawl-no-zdr",
        help="Firecrawl scrapeOptions.zeroDataRetention — do not persist scraped data.",
    ),
    firecrawl_skip_tls_verification: Optional[bool] = typer.Option(
        None, "--firecrawl-skip-tls/--firecrawl-no-skip-tls",
        help="Firecrawl scrapeOptions.skipTlsVerification — ignore TLS cert errors.",
    ),
) -> None:
    """Run a search across one, many, or all providers."""
    if agent:
        if fmt is None:
            fmt = "json"
        if not _cli_option_present("--top", "-n"):
            top = 5

    if extract_top and extract_top > 0 and extract_provider not in EXTRACT_PROVIDERS:
        err_console.print(
            f"[red]Invalid --extract-provider:[/] {extract_provider} "
            f"(choose {'|'.join(EXTRACT_PROVIDERS)})"
        )
        raise typer.Exit(2)

    try:
        resp: SearchResponse = asyncio.run(
            engine_search(
                query,
                providers=list(provider) if provider else None,
                mode=mode,
                all_providers=all_providers,
                top=top,
                no_cache=no_cache,
                cache_ttl=cache_ttl_opt,
                time=time,
                lang=lang,
                region=region,
                location=location,
                sites=site,
                exclude=exclude,
                extract_top=extract_top,
                extract_provider=extract_provider,
                fanout_timeout=fanout_timeout,
                answer=answer,
                summary=summary,
                sources=sources,
                goggles=list(goggles) if goggles else None,
                serper_type=serper_type,
                page=page,
                autocorrect=autocorrect,
                livecrawl=livecrawl,
                auto=auto,
                raw=raw,
                retries=retries,
                days=days,
                chunks_per_source=chunks_per_source,
                additional_queries=list(additional_query) if additional_query else None,
                max_age_hours=max_age_hours,
                highlights=highlights,
                context_threshold=context_threshold,
                exact=exact,
                depth=depth,
                exa_type=exa_type,
                category=category,
                context=context,
                context_max_characters=context_max_characters,
                include_favicon=include_favicon,
                include_usage=include_usage,
                include_images=include_images,
                include_image_descriptions=include_image_descriptions,
                answer_depth=answer_depth,
                moderation=moderation,
                livecrawl_timeout=livecrawl_timeout,
                ignore_invalid_urls=ignore_invalid_urls,
                firecrawl_scrape_timeout=firecrawl_scrape_timeout,
                firecrawl_wait_for=firecrawl_wait_for,
                firecrawl_parsers=firecrawl_parsers,
                firecrawl_redact_pii=firecrawl_redact_pii if firecrawl_redact_pii else None,
                jina_engine=jina_engine,
                jina_respond_with=jina_respond_with,
                jina_target_selector=jina_target_selector,
                jina_wait_for=jina_wait_for,
                jina_remove_selector=jina_remove_selector,
                jina_generated_alt=jina_generated_alt,
                safe_search=safe_search,
                project_id=project_id,
                firecrawl_only_clean_content=firecrawl_only_clean_content,
                firecrawl_max_age=firecrawl_max_age,
                firecrawl_min_age=firecrawl_min_age,
                firecrawl_block_ads=firecrawl_block_ads,
                firecrawl_proxy=firecrawl_proxy,
                firecrawl_question=firecrawl_question,
                highlights_query=highlights_query,
                firecrawl_store_in_cache=firecrawl_store_in_cache,
                firecrawl_lockdown=firecrawl_lockdown,
                firecrawl_zero_data_retention=firecrawl_zero_data_retention,
                firecrawl_skip_tls_verification=firecrawl_skip_tls_verification,
            )
        )
    except ValueError as e:
        err_console.print(f"[red]Invalid filter:[/] {e}")
        raise typer.Exit(2)

    merged = resp.results
    mode_key = (mode or "").lower() or None

    if fmt is None:
        fmt = "table" if sys.stdout.isatty() else "json"

    meta = resp.meta
    if agent:
        meta["agent_preset"] = True

    if (answer or mode_key == "answer") and resp.answer and fmt in ("table", "markdown", "md"):
        if fmt == "table":
            console.print(
                Panel(
                    resp.answer,
                    title="[bold green]Tavily Answer[/]",
                    border_style="green",
                    expand=True,
                )
            )
        else:
            sys.stdout.write(f"## Answer\n\n{resp.answer}\n\n")

    emit(merged, fmt, console=console, meta=meta, errors=resp.errors)
    if fmt != "json":
        _print_errors(resp.errors)
    if not merged and resp.errors:
        raise typer.Exit(1)


@app.command()
def extract(
    urls: list[str] = typer.Argument(..., help="One or more URLs to extract."),
    provider: str = typer.Option(
        "jina", "--provider", "-p", help="jina | firecrawl | tavily"
    ),
    fmt: Optional[str] = typer.Option(
        None, "--format", "-f", help="markdown | json (auto: json when piped, markdown in terminal)"
    ),
    concurrency: int = typer.Option(4, "--concurrency", "-c", help="Parallel requests."),
    query: Optional[str] = typer.Option(
        None, "--query", help="Tavily: rerank extracted chunks by relevance to this intent."
    ),
    extract_depth: Optional[str] = typer.Option(
        None, "--extract-depth",
        help="Tavily: basic | advanced (advanced retrieves tables/embedded content).",
    ),
    extract_format: Optional[str] = typer.Option(
        None, "--extract-format",
        help="Tavily: content format — markdown | text.",
    ),
) -> None:
    """Fetch one or more URLs and return clean markdown/text.

    The ``tavily`` provider additionally supports ``--query`` (relevance
    reranking), ``--extract-depth``, and ``--extract-format``.
    """
    if fmt is None:
        fmt = "markdown" if sys.stdout.isatty() else "json"
    options: dict = {}
    if provider == "tavily":
        if query:
            options["query"] = query
        if extract_depth:
            options["extract_depth"] = extract_depth
        if extract_format:
            options["format"] = extract_format
    outcomes = asyncio.run(
        extract_many(urls, provider=provider, concurrency=concurrency, **options)
    )
    if fmt == "json":
        import json

        results_ok = [{"url": u, "content": c} for u, c, e in outcomes if not e]
        errs = {u: e for u, _c, e in outcomes if e}
        meta = {
            "provider": provider,
            "urls_requested": len(urls),
            "urls_succeeded": len(results_ok),
        }
        out: dict = {"meta": meta, "results": results_ok}
        if errs:
            out["errors"] = errs
        sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
        return
    for url, content, err in outcomes:
        console.rule(f"[bold]{url}[/]")
        if err:
            err_console.print(f"[red]error:[/] {err}")
            continue
        sys.stdout.write((content or "") + "\n\n")


@app.command("providers")
def providers_cmd() -> None:
    """List all providers and key status."""
    table = Table(title="hsearch providers", header_style="bold cyan")
    table.add_column("Provider", style="bold")
    table.add_column("Env var", style="dim")
    table.add_column("Status")
    for name in list_providers():
        env = PROVIDER_ENV[name]
        ok = bool(get_key(name))
        status = "[green]configured[/]" if ok else "[red]missing[/]"
        table.add_row(name, env, status)
    console.print(table)


@app.command()
def config() -> None:
    """Show current configuration."""
    cache = ResultCache()
    s = cache.stats()
    cache.close()
    table = Table(title="hsearch config", header_style="bold cyan")
    table.add_column("Setting", style="bold")
    table.add_column("Value")
    table.add_row("version", __version__)
    table.add_row("timeout (s)", str(timeout_seconds()))
    table.add_row("cache_ttl (s)", str(cache_ttl()))
    table.add_row("cache_dir", str(s.get("path") or cache_dir()))
    table.add_row("cache_entries", str(s["entries"]))
    table.add_row("cache_size_bytes", str(s["size_bytes"]))
    table.add_row("configured_providers", ", ".join(configured_providers()) or "(none)")
    console.print(table)


@app.command("schema")
def schema_cmd() -> None:
    """Output tool schema as JSON for LLM self-discovery.

    LLM agents can run ``hsearch schema`` to learn how to call this CLI.
    """
    from hsearch.schema import render_schema

    sys.stdout.write(render_schema() + "\n")


@app.command("mcp")
def mcp_cmd() -> None:
    """Start hsearch as a stdio MCP server."""
    try:
        from hsearch.mcp_server import run_stdio

        run_stdio()
    except RuntimeError as e:
        err_console.print(f"[red]MCP support is not installed.[/] {e}")
        raise typer.Exit(1)
    except ImportError:
        err_console.print(
            '[red]MCP support is not installed.[/] Install MCP support with: pip install -e ".[mcp]"'
        )
        raise typer.Exit(1)


def _render_traversal(resp: TraversalResponse, fmt: Optional[str], show_content: bool) -> None:
    """Shared renderer for `hsearch map` / `hsearch crawl`."""
    if resp.error:
        err_console.print(f"[red]error:[/] {resp.error}")
        raise typer.Exit(1)
    if fmt is None:
        fmt = "table" if sys.stdout.isatty() else "json"
    if fmt == "json":
        import json

        sys.stdout.write(json.dumps(resp.to_dict(), ensure_ascii=False, indent=2) + "\n")
    elif fmt == "urls":
        for u in resp.urls:
            sys.stdout.write(u + "\n")
    elif fmt == "markdown":
        sys.stdout.write(f"# {resp.kind}: {resp.base_url}\n\n")
        for p in resp.pages:
            sys.stdout.write(f"## {p['url']}\n\n")
            if show_content and p.get("content"):
                sys.stdout.write(p["content"] + "\n\n")
    else:
        table = Table(
            title=f"hsearch {resp.kind} — {resp.base_url} ({len(resp.pages)} pages, "
            f"{resp.response_time}s)",
            header_style="bold cyan",
        )
        table.add_column("#", style="dim", width=4)
        table.add_column("URL")
        if any(p.get("content") for p in resp.pages):
            table.add_column("Chars", justify="right", width=8)
        for i, p in enumerate(resp.pages, 1):
            row = [str(i), p["url"]]
            if any(x.get("content") for x in resp.pages):
                row.append(str(len(p.get("content") or "")))
            table.add_row(*row)
        console.print(table)


@app.command("map")
def map_cmd(
    url: str = typer.Argument(..., help="Root URL to map."),
    max_depth: Optional[int] = typer.Option(
        None, "--max-depth", help="Link-hops from the root (default 1)."
    ),
    max_breadth: Optional[int] = typer.Option(
        None, "--max-breadth", help="Max links followed per page."
    ),
    limit: Optional[int] = typer.Option(None, "--limit", help="Max total pages to discover."),
    instructions: Optional[str] = typer.Option(
        None, "--instructions", help="Natural-language steering, e.g. 'only pricing pages'."
    ),
    select_paths: Optional[list[str]] = typer.Option(
        None, "--select-path", help="Regex path allowlist; repeatable."
    ),
    exclude_paths: Optional[list[str]] = typer.Option(
        None, "--exclude-path", help="Regex path blocklist; repeatable."
    ),
    allow_external: bool = typer.Option(
        False, "--allow-external", help="Follow links off the root domain."
    ),
    categories: Optional[list[str]] = typer.Option(
        None, "--category", help="Page category filter (e.g. Documentation, Pricing); repeatable."
    ),
    fmt: Optional[str] = typer.Option(
        None, "--format", "-f", help="table | json | urls | markdown (auto: json when piped)."
    ),
) -> None:
    """Map a site's URL inventory via Tavily /map (fast, no content extraction).

    The right first move when you need a COMPLETE page enumeration (marketplace
    listings, docs trees, connector catalogs) — beats fighting client-side
    React pagination, and works when sitemap.xml is missing or stale.
    """
    resp = asyncio.run(
        engine_map_site(
            url,
            max_depth=max_depth,
            max_breadth=max_breadth,
            limit=limit,
            instructions=instructions,
            select_paths=select_paths or None,
            exclude_paths=exclude_paths or None,
            allow_external=allow_external or None,
            categories=categories or None,
        )
    )
    _render_traversal(resp, fmt, show_content=False)


@app.command("crawl")
def crawl_cmd(
    url: str = typer.Argument(..., help="Root URL to crawl."),
    max_depth: Optional[int] = typer.Option(
        None, "--max-depth", help="Link-hops from the root (default 1)."
    ),
    max_breadth: Optional[int] = typer.Option(
        None, "--max-breadth", help="Max links followed per page."
    ),
    limit: Optional[int] = typer.Option(None, "--limit", help="Max total pages to crawl."),
    instructions: Optional[str] = typer.Option(
        None, "--instructions", help="Natural-language steering, e.g. 'API reference pages only'."
    ),
    extract_depth: Optional[str] = typer.Option(
        None, "--extract-depth", help="basic | advanced (advanced gets tables/embedded content)."
    ),
    out_format: Optional[str] = typer.Option(
        None, "--content-format", help="markdown | text — extracted content format."
    ),
    select_paths: Optional[list[str]] = typer.Option(
        None, "--select-path", help="Regex path allowlist; repeatable."
    ),
    exclude_paths: Optional[list[str]] = typer.Option(
        None, "--exclude-path", help="Regex path blocklist; repeatable."
    ),
    allow_external: bool = typer.Option(
        False, "--allow-external", help="Follow links off the root domain."
    ),
    categories: Optional[list[str]] = typer.Option(
        None, "--category", help="Page category filter; repeatable."
    ),
    fmt: Optional[str] = typer.Option(
        None, "--format", "-f", help="table | json | urls | markdown (auto: json when piped)."
    ),
) -> None:
    """Crawl a site and extract each page's content via Tavily /crawl.

    Use `hsearch map` first to size the job, then crawl with --limit. The
    --instructions flag is real agentic steering, not a keyword filter: it
    prunes the frontier during traversal.
    """
    resp = asyncio.run(
        engine_crawl_site(
            url,
            max_depth=max_depth,
            max_breadth=max_breadth,
            limit=limit,
            instructions=instructions,
            extract_depth=extract_depth,
            format=out_format,
            select_paths=select_paths or None,
            exclude_paths=exclude_paths or None,
            allow_external=allow_external or None,
            categories=categories or None,
        )
    )
    _render_traversal(resp, fmt, show_content=True)


@app.command("usage")
def usage_cmd(
    fmt: Optional[str] = typer.Option(
        None, "--format", "-f", help="table | json (auto: json when piped)."
    ),
) -> None:
    """Show remaining quota / usage for providers that expose it.

    Only Tavily and Firecrawl publish a per-key usage endpoint. Brave, Serper,
    Exa and Jina have none — Exa reports per-call cost in each search response
    instead (see `--include-usage`).
    """
    data = asyncio.run(engine_account_usage())
    if fmt is None:
        fmt = "table" if sys.stdout.isatty() else "json"
    if fmt == "json":
        import json

        sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return
    table = Table(title="hsearch usage", header_style="bold cyan")
    table.add_column("Provider", style="bold")
    table.add_column("Quota / usage")
    table.add_column("Detail", style="dim")
    for prov in sorted(data):
        d = data[prov] or {}
        if d.get("error"):
            table.add_row(prov, "[red]unavailable[/]", str(d["error"])[:60])
            continue
        if prov == "tavily":
            quota = f"{d.get('plan_usage')}/{d.get('plan_limit')}"
            caps = d.get("by_capability") or {}
            detail = f"plan={d.get('plan')} " + " ".join(
                f"{k}={v}" for k, v in caps.items() if v
            )
        elif prov == "firecrawl":
            quota = f"{d.get('remaining_credits')} left / {d.get('plan_credits')}"
            detail = f"period ends {str(d.get('period_end'))[:10]}"
        else:
            quota = "-"
            detail = str(d)[:60]
        table.add_row(prov, quota, detail)
    console.print(table)
    console.print(
        "[dim]brave / serper / exa / jina: no public usage endpoint. "
        "Exa returns per-call costDollars — use `--include-usage`.[/]"
    )


@app.command("answer")
def answer_cmd(
    query: str = typer.Argument(..., help="Question to answer."),
    text: bool = typer.Option(False, "--text", help="Include full text in citations."),
    fmt: Optional[str] = typer.Option(
        None, "--format", "-f", help="json | markdown (auto: json when piped)."
    ),
) -> None:
    """Get an LLM-generated answer with citations from Exa."""
    resp: AnswerResponse = asyncio.run(engine_answer(query, text=text))
    if resp.error:
        err_console.print(f"[red]error:[/] {resp.error}")
        raise typer.Exit(1)
    if fmt is None:
        fmt = "markdown" if sys.stdout.isatty() else "json"
    if fmt == "json":
        import json

        sys.stdout.write(json.dumps(resp.to_dict(), ensure_ascii=False, indent=2) + "\n")
    else:
        if resp.answer:
            console.print(Panel(resp.answer, title="[bold green]Answer[/]", border_style="green"))
        if resp.citations:
            table = Table(title="Citations", header_style="bold cyan")
            table.add_column("#", style="dim", width=3)
            table.add_column("Title")
            table.add_column("URL", style="dim")
            for i, c in enumerate(resp.citations, 1):
                table.add_row(str(i), c.title, c.url)
            console.print(table)


@app.command("research")
def research_cmd(
    input_text: str = typer.Argument(..., help="Research question / instruction."),
    model: str = typer.Option(
        "mini", "--model",
        help="Tavily research model: mini (fast, narrow) | pro (deep) | auto.",
    ),
    citation_format: str = typer.Option(
        "numbered", "--citation-format",
        help="Citation style: numbered | mla | apa | chicago.",
    ),
    include_domains: list[str] = typer.Option(
        None, "--site", help="Restrict research to domain(s); repeatable."
    ),
    exclude_domains: list[str] = typer.Option(
        None, "--exclude", help="Exclude domain(s); repeatable."
    ),
    timeout: float = typer.Option(
        600.0, "--timeout", help="Overall deadline in seconds (research is async server-side)."
    ),
    poll_interval: float = typer.Option(
        5.0, "--poll-interval", help="Seconds between status polls."
    ),
    stream: bool = typer.Option(
        False, "--stream",
        help="Stream the report to stdout as it is written (SSE) instead of "
             "waiting for the whole thing. Ignores --format.",
    ),
    fmt: Optional[str] = typer.Option(
        None, "--format", "-f", help="json | markdown (auto: json when piped)."
    ),
) -> None:
    """Run a Tavily deep-research task (async agent) and print the final report.

    Mini-model tasks usually complete in 10-60s; pro can take several minutes.
    Pass --stream to see text as it is generated.
    """
    if stream:
        async def _run_stream() -> int:
            n = 0
            try:
                async for piece in engine_research_streaming(
                    input_text,
                    model=model,
                    citation_format=citation_format,
                    include_domains=list(include_domains) if include_domains else None,
                    exclude_domains=list(exclude_domains) if exclude_domains else None,
                    timeout=timeout,
                ):
                    sys.stdout.write(piece)
                    sys.stdout.flush()
                    n += len(piece)
            except Exception as e:  # noqa: BLE001 — surface any stream failure cleanly
                err_console.print(f"\n[red]stream error:[/] {type(e).__name__}: {e}")
                return 1
            sys.stdout.write("\n")
            if n == 0:
                err_console.print(
                    "[yellow]warning:[/] stream produced no content — "
                    "retry without --stream to get the buffered report."
                )
                return 1
            return 0

        raise typer.Exit(asyncio.run(_run_stream()))

    resp: ResearchResponse = asyncio.run(
        engine_research(
            input_text,
            model=model,
            citation_format=citation_format,
            include_domains=list(include_domains) if include_domains else None,
            exclude_domains=list(exclude_domains) if exclude_domains else None,
            poll_interval=poll_interval,
            timeout=timeout,
        )
    )
    if resp.error:
        err_console.print(f"[red]error:[/] {resp.error}")
        if "timeout" in resp.error.lower():
            err_console.print(
                "[yellow]hint:[/] research is async server-side — raise --timeout, "
                "or use --model mini for faster narrow questions."
            )
        raise typer.Exit(1)
    if fmt is None:
        fmt = "markdown" if sys.stdout.isatty() else "json"
    if fmt == "json":
        import json

        sys.stdout.write(json.dumps(resp.to_dict(), ensure_ascii=False, indent=2) + "\n")
    else:
        if resp.content:
            console.print(Panel(resp.content, title="[bold green]Research Report[/]", border_style="green"))
        if resp.sources:
            table = Table(title="Sources", header_style="bold cyan")
            table.add_column("#", style="dim", width=3)
            table.add_column("Title")
            table.add_column("URL", style="dim")
            for i, s in enumerate(resp.sources, 1):
                if isinstance(s, dict):
                    table.add_row(str(i), s.get("title") or "", s.get("url") or "")
            console.print(table)


@app.command("agent")
def agent_cmd(
    query: str = typer.Argument(..., help="Research / list-building / enrichment instruction."),
    effort: str = typer.Option(
        "auto", "--effort",
        help="Cost/reasoning tier: minimal | low | medium | high | xhigh | auto.",
    ),
    schema_file: Optional[str] = typer.Option(
        None, "--schema-file",
        help="Path to a JSON Schema file → schema-validated structured output.",
    ),
    previous_run_id: Optional[str] = typer.Option(
        None, "--previous-run-id",
        help="Continue from a completed run (e.g. 'find 10 more results').",
    ),
    timeout: float = typer.Option(
        600.0, "--timeout", help="Overall deadline in seconds (Agent runs are async server-side)."
    ),
    poll_interval: float = typer.Option(
        3.0, "--poll-interval", help="Seconds between status polls."
    ),
    fmt: Optional[str] = typer.Option(
        None, "--format", "-f", help="json | markdown (auto: json when piped)."
    ),
) -> None:
    """Run an Exa Agent task (async high-compute research / list-building / enrichment).

    The Agent handles multi-hop workflows that need many structured fields and
    complex reasoning — e.g. "find companies that raised a Series A, then find
    their decision makers". minimal/low effort answer narrow questions in
    seconds; high/xhigh deep tasks can take minutes.
    """
    output_schema = None
    if schema_file:
        import json as _json

        try:
            with open(schema_file, encoding="utf-8") as fh:
                output_schema = _json.load(fh)
        except (OSError, ValueError) as e:
            err_console.print(f"[red]Cannot read --schema-file:[/] {e}")
            raise typer.Exit(2)

    resp: AgentResponse = asyncio.run(
        engine_agent(
            query,
            effort=effort,
            output_schema=output_schema,
            previous_run_id=previous_run_id,
            poll_interval=poll_interval,
            timeout=timeout,
        )
    )
    if resp.error:
        err_console.print(f"[red]error:[/] {resp.error}")
        if "timeout" in resp.error.lower():
            err_console.print(
                "[yellow]hint:[/] Agent runs are async server-side — raise --timeout, "
                "or lower --effort (minimal/low) for faster narrow tasks."
            )
        raise typer.Exit(1)
    if fmt is None:
        fmt = "markdown" if sys.stdout.isatty() else "json"
    if fmt == "json":
        import json

        sys.stdout.write(json.dumps(resp.to_dict(), ensure_ascii=False, indent=2) + "\n")
    else:
        if resp.structured is not None:
            import json as _json

            console.print(
                Panel(
                    _json.dumps(resp.structured, ensure_ascii=False, indent=2),
                    title="[bold green]Structured Output[/]",
                    border_style="green",
                )
            )
        elif resp.text:
            console.print(
                Panel(resp.text, title="[bold green]Agent Report[/]", border_style="green")
            )
        if resp.cost:
            console.print(f"[dim]cost: ${resp.cost.get('total', 0)} | run_id: {resp.run_id}[/]")


@app.command("ground")
def ground_cmd(
    statement: str = typer.Argument(..., help="Statement to fact-check."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass Jina cache."),
    fmt: Optional[str] = typer.Option(
        None, "--format", "-f", help="json | markdown (auto: json when piped)."
    ),
) -> None:
    """Fact-check a statement using Jina's Grounding API (g.jina.ai)."""
    resp: GroundingResponse = asyncio.run(engine_ground(statement, no_cache=no_cache))
    if resp.error:
        # Jina's g.jina.ai often returns Cloudflare 524 (origin > 120s) when
        # under heavy load. Give the user actionable guidance instead of a
        # raw HTTP error blob.
        err_msg = str(resp.error)
        if "524" in err_msg or "timeout" in err_msg.lower():
            err_console.print(
                f"[red]Jina Grounding API timed out.[/] "
                "g.jina.ai had to scrape multiple reference pages and didn't "
                "finish within its 120s Cloudflare origin window. This is a "
                "Jina-side load/availability issue, not an hsearch bug.\n"
                "[yellow]Try:[/] shorter statement, retry in a few minutes, "
                "or fall back to `hsearch search ... --mode answer` for a "
                "synthesized answer with citations from Tavily/Brave.\n"
                f"[dim]raw: {err_msg[:200]}[/]"
            )
        else:
            err_console.print(f"[red]error:[/] {err_msg}")
        raise typer.Exit(1)
    if fmt is None:
        fmt = "markdown" if sys.stdout.isatty() else "json"
    if fmt == "json":
        import json

        sys.stdout.write(json.dumps(resp.to_dict(), ensure_ascii=False, indent=2) + "\n")
    else:
        score = resp.factuality
        result = resp.result
        color = "green" if result else "red"
        verdict = "TRUE" if result else "FALSE"
        console.print(f"[bold {color}]{verdict}[/] (factuality: {score:.2f})" if score is not None else f"[bold {color}]{verdict}[/]")
        if resp.reasoning:
            console.print(Panel(resp.reasoning, title="Reasoning", border_style="blue"))
        if resp.references:
            table = Table(title="References", header_style="bold cyan")
            table.add_column("#", style="dim", width=3)
            table.add_column("Supports", width=8)
            table.add_column("URL", style="dim")
            table.add_column("Quote")
            for i, ref in enumerate(resp.references[:10], 1):
                supports = "[green]Yes[/]" if ref.get("isSupportive") else "[red]No[/]"
                table.add_row(str(i), supports, str(ref.get("url", "")), str(ref.get("keyQuote", ""))[:80])
            console.print(table)


@app.command("similar")
def similar_cmd(
    url: str = typer.Argument(..., help="URL to find similar pages for."),
    top: int = typer.Option(10, "--top", "-n", help="Max results."),
    text: bool = typer.Option(False, "--text", help="Include full text content."),
    highlights: bool = typer.Option(False, "--highlights", help="Include highlight excerpts."),
    summary: bool = typer.Option(False, "--summary", help="Include LLM summaries."),
    fmt: Optional[str] = typer.Option(
        None, "--format", "-f", help="Output format: table | json | markdown | urls."
    ),
) -> None:
    """Find semantically similar pages to a URL using Exa."""
    resp: SearchResponse = asyncio.run(
        engine_find_similar(url, top=top, text=text, highlights=highlights, summary=summary)
    )
    if not resp.results and resp.errors:
        for name, msg in resp.errors.items():
            err_console.print(f"[red]{name}:[/] {msg}")
        raise typer.Exit(1)
    if fmt is None:
        fmt = "table" if sys.stdout.isatty() else "json"
    emit(resp.results, fmt, console=console, meta=resp.meta, errors=resp.errors)
    if fmt != "json":
        _print_errors(resp.errors)


@cache_app.command("clear")
def cache_clear() -> None:
    """Clear all cached search results."""
    c = ResultCache()
    path = c.stats().get("path") or str(cache_dir())
    n = c.clear()
    c.close()
    console.print(f"[green]Cleared[/] {n} cache entries from {path}")


@cache_app.command("stats")
def cache_stats() -> None:
    """Show cache statistics."""
    c = ResultCache()
    s = c.stats()
    c.close()
    console.print(s)


if __name__ == "__main__":
    app()
