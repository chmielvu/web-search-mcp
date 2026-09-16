"""Tool logic layer — framework-free functions behind the MCP tools.

Kept separate from ``mcp_server.py`` so every tool is unit-testable without
the MCP protocol. MCP wrappers add: context plumbing, ToolError mapping,
progress reporting, annotations.

Design (agent-first, per RESEARCH.md): 5 orthogonal tools, rich params with
sane RAG-tuned defaults, structured dict returns (FastMCP converts them to
structuredContent), progressive disclosure via params instead of extra tools.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import urlsplit

from .chef import backend_report, clean_with_backend
from .cleaner import CleaningOptions, extract_title, outline, count_links_images
from .client import Crawl4AIClient, Crawl4AIError, ServerProfile, extract_markdown
from .knobs import build_browser_config, build_crawler_config
from .pipeline import PipelineOptions, run_pipeline
from .scorer import score_markdown
from .siteprof import detect_content_type
from .storage import Manifest

VALID_CONTENT_TYPES = ("auto", "docs", "wiki", "blog", "news", "generic")
VALID_LINK_MODES = ("keep", "text", "drop")
VALID_IMAGE_POLICIES = ("keep", "alt_only", "drop")
VALID_CITATION_MODES = ("auto", "strip", "keep")
VALID_EXTRACTORS = ("auto", "crawl4ai", "trafilatura")
VALID_CLEANERS = ("auto", "native", "markdownchef")


class InputError(ValueError):
    """Invalid tool input -> 400-style tool error for the agent."""


def _check(name: str, value: str, allowed: tuple) -> str:
    if value not in allowed:
        raise InputError(
            f"{name}='{value}' is invalid. Allowed: {', '.join(allowed)}."
        )
    return value


# ---------------------------------------------------------------- capabilities
async def capabilities(client: Crawl4AIClient) -> dict:
    """Server discovery + capability matrix + tool usage guidance."""
    profile = await client.ensure_profile()
    out = {
        "server": profile.to_dict(),
        "cleaner_backends": backend_report(),
        "notes": {
            "deep_crawl": (
                "crawl_site uses client-side BFS on all server versions "
                "(Crawl4AI >= 0.9 forbids server-side deep-crawl strategies over REST)"
            ),
            "auth": (
                "bearer token attached when CRAWL4AI_API_TOKEN is set; "
                f"server reports auth_required={profile.auth_required}"
            ),
        },
        "tool_guide": {
            "scrape": "1..n URLs -> cleaned, scored markdown inline (+files with save_dir). "
                      "Default mode='browser' runs the tuned extraction config server-side.",
            "crawl_site": "sitemap.xml | llms.txt | URL | URL-list file -> corpus directory of "
                          "front-mattered clean .md files (incremental via manifest).",
            "clean_text": "clean + score markdown you already have; returns edits + score.",
            "corpus_status": "manifest summary for a corpus built by crawl_site.",
        },
    }
    if profile.schema_ok:
        out["server"]["schema_crawler_fields"] = sorted(
            ((profile.schema or {}).get("crawler") or {}).keys()
        )
    return out


# ------------------------------------------------------------------- scrape
def _browser_options(options: Optional[dict]) -> dict:
    """Validate/normalize the server-side browser options dict."""
    allowed = {"css_selector", "excluded_selector", "target_elements", "wait_for",
               "js_code", "word_count_threshold", "pruning", "extra"}
    unknown = set(options or {}) - allowed
    if unknown:
        raise InputError(
            f"options: unknown keys {sorted(unknown)}. Allowed: {sorted(allowed)}."
        )
    return dict(options or {})


async def scrape(
    client: Crawl4AIClient,
    urls: List[str],
    *,
    mode: str = "auto",
    query: Optional[str] = None,
    cleaner: str = "auto",
    link_mode: str = "keep",
    image_policy: str = "keep",
    citation_mode: str = "auto",
    content_type: str = "auto",
    save_dir: Optional[str] = None,
    max_chars: int = 24000,
    options: Optional[dict] = None,
    fit: bool = True,
) -> dict:
    """Scrape 1..n URLs -> cleaned, scored, RAG-ready markdown per URL."""
    if not urls:
        raise InputError("urls: provide at least one http(s) URL.")
    if len(urls) > 100:
        raise InputError("urls: max 100 per call (server /crawl limit); batch larger jobs.")
    for u in urls:
        if not u.startswith(("http://", "https://")):
            raise InputError(f"urls: '{u}' is not an http(s) URL.")
    mode = _check("mode", mode, ("auto", "browser", "fast", "bm25"))
    cleaner = _check("cleaner", cleaner, VALID_CLEANERS)
    link_mode = _check("link_mode", link_mode, VALID_LINK_MODES)
    image_policy = _check("image_policy", image_policy, VALID_IMAGE_POLICIES)
    citation_mode = _check("citation_mode", citation_mode, VALID_CITATION_MODES)
    content_type = _check("content_type", content_type, VALID_CONTENT_TYPES)
    opts = _browser_options(options)
    if mode == "bm25" and not query:
        raise InputError("mode='bm25' requires query=<search terms>.")
    if max_chars < 500:
        raise InputError("max_chars must be >= 500 (raise it, or use save_dir for full files).")

    profile = await client.ensure_profile()
    browser_cfg, _ = build_browser_config(profile)
    o = _browser_options(opts)

    per_url: dict = {}
    files: List[str] = []

    def clean_body(body: str, url: str) -> dict:
        res = clean_with_backend(body, CleaningOptions(
            source_url=url, link_mode=link_mode, image_policy=image_policy,
            citation_mode=citation_mode,
            content_type=content_type if content_type != "auto" else "auto",
        ), backend=cleaner)
        sc = score_markdown(res.text, res.content_type, res.edits)
        return {"markdown": res.text, "content_type": res.content_type,
                "type_confidence": res.type_confidence, "edits": res.edits,
                "score": sc, "structure": res.structure}

    if mode in ("browser", "auto"):
        crawler_cfg, warns = build_crawler_config(
            profile, fit=fit, query=query if mode == "browser" and query else None,
            use_bm25=False, content_type=None if content_type == "auto" else content_type,
            css_selector=o.get("css_selector"),
            excluded_selector=o.get("excluded_selector"),
            target_elements=o.get("target_elements"),
            wait_for=o.get("wait_for"),
            js_code=o.get("js_code"),
            word_count_threshold=o.get("word_count_threshold"),
            pruning=o.get("pruning"),
            extra=o.get("extra"),
        )
        for w in warns:
            per_url.setdefault("_config_warnings", []).append(w)
        try:
            results = await client.crawl(urls, browser_config=browser_cfg,
                                         crawler_config=crawler_cfg)
        except Crawl4AIError:
            raise
        for r in results:
            url = r.get("url") or "?"
            entry: dict = {"success": r.get("success"),
                           "error": r.get("error_message"),
                           "title": (r.get("metadata") or {}).get("title"),
                           "extractor": "crawl4ai", "mode": "browser"}
            raw, fitmd = extract_markdown(r)
            body = (fitmd or raw) if fit else raw
            if body and r.get("success"):
                cleaned = clean_body(body, url)
                entry.update(cleaned)
            per_url[url] = entry
    else:  # fast | bm25 -> POST /md per URL
        f = "bm25" if mode == "bm25" else ("raw" if fit is False else "fit")
        for u in urls:
            try:
                data = await client.md(u, f=f, q=query)
                entry = {"success": True, "extractor": f"md:{data.get('filter', f)}",
                         "mode": mode}
                body = data.get("markdown") or ""
                if body:
                    entry.update(clean_body(body, u))
                per_url[u] = entry
            except Crawl4AIError as exc:
                per_url[u] = {"success": False, "error": str(exc), "mode": mode}

    # optional file output (full, never truncated)
    if save_dir:
        sd = Path(save_dir)
        for url, entry in per_url.items():
            if url.startswith("_") or not entry.get("markdown"):
                continue
            slug = _file_slug(url)
            fm = {
                "source_url": url,
                "title": entry.get("title") or extract_title(entry["markdown"], url),
                "content_type": entry.get("content_type", "generic"),
                "crawled_at": _now(),
                "extractor": entry.get("extractor"),
                "cleaner": cleaner,
                "words": (entry.get("score", {}).get("signals", {}) or {}).get("words"),
                "content_hash": _hash(entry["markdown"]),
                "score": {"value": entry["score"]["value"], "band": entry["score"]["band"]},
                "headings": outline(entry["markdown"]),
                "links": count_links_images(entry["markdown"]),
            }
            p = sd / f"{slug}.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            from .storage import write_page

            write_page(sd, f"{slug}.md", fm, entry["markdown"])
            entry["file"] = str(p)
            files.append(str(p))

    # inline truncation (files are never truncated)
    for url, entry in per_url.items():
        if url.startswith("_"):
            continue
        md = entry.get("markdown")
        if md and len(md) > max_chars:
            entry["markdown"] = md[:max_chars]
            entry["truncated"] = True
            entry["full_markdown_in"] = ("file" if save_dir else
                                         "re-call with larger max_chars or save_dir")
        else:
            entry["truncated"] = False

    published = sum(1 for u, e in per_url.items()
                    if not u.startswith("_") and e.get("score", {}).get("band") == "publish")
    return {"success": True, "requested": len(urls), "publish_band": published,
            "files_written": files, "results": per_url}


# ---------------------------------------------------------------- crawl_site
async def crawl_site(
    client: Crawl4AIClient,
    source: str,
    out_dir: str = "./corpus",
    *,
    deep: bool = False,
    max_depth: int = 2,
    max_pages: int = 200,
    include_external: bool = False,
    url_include: Optional[List[str]] = None,
    url_exclude: Optional[List[str]] = None,
    cleaner: str = "auto",
    link_mode: str = "keep",
    image_policy: str = "keep",
    citation_mode: str = "auto",
    extractor: str = "auto",
    content_type: Optional[str] = None,
    min_score: int = 40,
    keep_raw: bool = False,
    two_pass: bool = False,
    force: bool = False,
    fit: bool = True,
    options: Optional[dict] = None,
    progress: Optional[Callable[[str], None]] = None,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """Build a corpus of clean .md documents from a page/sitemap/llms.txt/URL file."""
    cleaner = _check("cleaner", cleaner, VALID_CLEANERS)
    link_mode = _check("link_mode", link_mode, VALID_LINK_MODES)
    image_policy = _check("image_policy", image_policy, VALID_IMAGE_POLICIES)
    citation_mode = _check("citation_mode", citation_mode, VALID_CITATION_MODES)
    extractor = _check("extractor", extractor, VALID_EXTRACTORS)
    if content_type is not None and content_type not in VALID_CONTENT_TYPES:
        raise InputError(f"content_type='{content_type}' invalid; allowed: {VALID_CONTENT_TYPES}")
    o = _browser_options(options)

    opts = PipelineOptions(
        out_dir=Path(out_dir), fit=fit, cleaner=cleaner, link_mode=link_mode,
        image_policy=image_policy, citation_mode=citation_mode, extractor=extractor,
        content_type=content_type, min_score=min_score,
        incremental=not force, keep_raw=keep_raw, two_pass=two_pass,
        deep=deep, max_depth=max_depth, max_pages=max_pages,
        include_external=include_external,
        url_include=list(url_include or []), url_exclude=list(url_exclude or []),
        css_selector=o.get("css_selector"), excluded_selector=o.get("excluded_selector"),
        target_elements=o.get("target_elements"), wait_for=o.get("wait_for"),
        js_code=o.get("js_code"),
    )
    return await run_pipeline(client, [source], opts, progress=progress,
                              progress_cb=progress_cb)


# ---------------------------------------------------------------- clean_text
async def clean_text(
    markdown: str,
    *,
    source_url: Optional[str] = None,
    cleaner: str = "auto",
    link_mode: str = "keep",
    image_policy: str = "keep",
    citation_mode: str = "auto",
    content_type: str = "auto",
    save_path: Optional[str] = None,
    max_chars: int = 24000,
) -> dict:
    """Clean + score markdown you already have. No scraping."""
    if not markdown or not markdown.strip():
        raise InputError("markdown: empty input.")
    cleaner = _check("cleaner", cleaner, VALID_CLEANERS)
    link_mode = _check("link_mode", link_mode, VALID_LINK_MODES)
    image_policy = _check("image_policy", image_policy, VALID_IMAGE_POLICIES)
    citation_mode = _check("citation_mode", citation_mode, VALID_CITATION_MODES)
    content_type = _check("content_type", content_type, VALID_CONTENT_TYPES)

    res = clean_with_backend(markdown, CleaningOptions(
        source_url=source_url, link_mode=link_mode, image_policy=image_policy,
        citation_mode=citation_mode,
        content_type=content_type,
    ), backend=cleaner)
    sc = score_markdown(res.text, res.content_type, res.edits)
    out = {
        "markdown": res.text[:max_chars],
        "truncated": len(res.text) > max_chars,
        "content_type": res.content_type,
        "type_confidence": res.type_confidence,
        "edits": res.edits,
        "warnings": res.warnings,
        "structure": res.structure,
        "score": sc,
    }
    if save_path:
        from .storage import write_page

        p = Path(save_path)
        fm = {
            "source_url": source_url,
            "title": extract_title(res.text, ""),
            "content_type": res.content_type,
            "cleaned_at": _now(),
            "cleaner": cleaner,
            "content_hash": _hash(res.text),
            "score": {"value": sc["value"], "band": sc["band"]},
            "headings": outline(res.text),
        }
        write_page(p.parent, p.name, fm, res.text)
        out["file"] = str(p)
    return out


# ------------------------------------------------------------- corpus_status
def corpus_status(corpus_dir: str) -> dict:
    """Manifest summary for a corpus built by crawl_site."""
    p = Path(corpus_dir)
    if not (p / "manifest.json").exists():
        raise InputError(
            f"corpus_dir '{corpus_dir}' has no manifest.json — was it created by crawl_site?"
        )
    m = Manifest.load(p)
    counts: dict = {}
    low_score = []
    for url, rec in m.records.items():
        counts[rec.get("status", "?")] = counts.get(rec.get("status", "?"), 0) + 1
        score = rec.get("score")
        if isinstance(score, (int, float)) and score < 65 and rec.get("status") != "unchanged":
            low_score.append({"url": url, "score": score, "file": rec.get("file")})
    low_score.sort(key=lambda x: x["score"])
    return {"corpus": str(p), "counts": counts,
            "last_run": m.stats.get("last_run"), "updated_at": m.stats.get("updated_at"),
            "low_score_sample": low_score[:20]}


# -------------------------------------------------------------------- helpers
def _file_slug(url: str) -> str:
    from .knobs import slugify_url

    return slugify_url(url)


def _now() -> str:
    from .storage import utcnow_iso

    return utcnow_iso()


def _hash(text: str) -> str:
    from .storage import sha256

    return sha256(text)
