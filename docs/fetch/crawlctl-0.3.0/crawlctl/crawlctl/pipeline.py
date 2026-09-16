"""discover -> acquire -> extract(arbitrate) -> clean -> score -> write clean .md docs.

Deep crawling is ALWAYS client-side BFS over ``result.links.internal``: Crawl4AI
>= 0.9 forbids deep_crawl_strategy over REST, and client BFS behaves uniformly
across all server versions. Batched POST /crawl requests (default 8 URLs/batch)
do the acquisition; per-batch progress is reported to the caller.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import urlsplit

from .chef import clean_with_backend
from .cleaner import CleaningOptions, count_links_images, extract_title, outline
from .client import Crawl4AIClient, ServerProfile, result_links
from .extract import build_candidates
from .knobs import build_browser_config, build_crawler_config, discover_urls, slugify_url, url_allowed
from .scorer import compare_candidates, score_markdown
from .storage import (BoilerplateDB, Manifest, estimate_tokens, sha256, utcnow_iso,
                      write_page, write_report)


@dataclass
class PipelineOptions:
    out_dir: Path = Path("./corpus")
    fit: bool = True
    cleaner: str = "auto"              # auto | native | markdownchef
    link_mode: str = "keep"            # keep | text | drop
    image_policy: str = "keep"         # keep | alt_only | drop
    citation_mode: str = "auto"        # auto | strip | keep
    extractor: str = "auto"            # auto | crawl4ai | trafilatura
    content_type: Optional[str] = None  # server-side preset hint (docs|wiki|blog|news|generic)
    min_score: int = 40                # reject below; publish band from scorer env
    batch_size: int = 8
    cache_mode: str = "bypass"
    incremental: bool = True
    keep_raw: bool = False
    css_selector: Optional[str] = None
    excluded_selector: Optional[str] = None
    target_elements: Optional[List[str]] = None
    wait_for: Optional[str] = None
    js_code: Optional[List[str]] = None
    page_timeout: int = 30000
    deep: bool = False
    max_depth: int = 2
    max_pages: int = 200
    include_external: bool = False
    url_include: List[str] = field(default_factory=list)
    url_exclude: List[str] = field(default_factory=list)
    two_pass: bool = False             # warm the boilerplate DB, then real run


def _crawler_config(profile: ServerProfile, opts: PipelineOptions):
    """Version-gated crawler config + warnings for this pipeline run."""
    return build_crawler_config(
        profile,
        cache_mode=opts.cache_mode,
        css_selector=opts.css_selector,
        excluded_selector=opts.excluded_selector,
        target_elements=opts.target_elements,
        wait_for=opts.wait_for,
        page_timeout=opts.page_timeout,
        js_code=opts.js_code,
        fit=opts.fit,
        content_type=opts.content_type,
    )


async def _crawl_flat(client: Crawl4AIClient, profile: ServerProfile, urls: List[str],
                      opts: PipelineOptions, progress: Callable[[str], None]) -> List[dict]:
    results: List[dict] = []
    browser_cfg, _ = build_browser_config(profile)
    for i in range(0, len(urls), opts.batch_size):
        batch = urls[i:i + opts.batch_size]
        crawler_cfg, warns = _crawler_config(profile, opts)
        for w in warns:
            progress(f"config: {w}")
        try:
            results.extend(await client.crawl(batch, browser_config=browser_cfg,
                                              crawler_config=crawler_cfg))
        except Exception as exc:
            progress(f"batch failed: {exc}")
            results.extend({"url": u, "success": False, "error_message": str(exc)}
                           for u in batch)
        progress(f"crawled {min(i + opts.batch_size, len(urls))}/{len(urls)}")
        await asyncio.sleep(0)
    return results


async def _crawl_deep(client: Crawl4AIClient, profile: ServerProfile, seeds: List[str],
                      opts: PipelineOptions, progress: Callable[[str], None]) -> List[dict]:
    """Client-side BFS over internal links (uniform across server versions)."""
    visited: set = set()
    results: List[dict] = []
    frontier = list(seeds)
    for depth in range(opts.max_depth + 1):
        if not frontier or len(visited) >= opts.max_pages:
            break
        batch = [u for u in frontier if u not in visited][: opts.max_pages - len(visited)]
        frontier = []
        progress(f"depth {depth}: {len(batch)} URLs")
        for r in await _crawl_flat(client, profile, batch, opts, progress):
            url = r.get("url") or ""
            visited.add(url)
            results.append(r)
            if depth < opts.max_depth:
                host = urlsplit(url).netloc
                for href in result_links(r).get("internal", []):
                    if href and href not in visited and url_allowed(
                            href, opts.url_include, opts.url_exclude, host,
                            opts.include_external):
                        frontier.append(href)
    return results


def _slug_with_dedupe(url: str, used: set) -> str:
    slug = slugify_url(url)
    if slug in used:
        suffix = sha256(url)[:6]
        slug = f"{slug}-{suffix}"
    used.add(slug)
    return slug


async def run_pipeline(client: Crawl4AIClient, sources: List[str], opts: PipelineOptions,
                       profile: Optional[ServerProfile] = None,
                       progress: Optional[Callable[[str], None]] = None,
                       progress_cb: Optional[Callable[[int, int, str], None]] = None) -> dict:
    log = progress or (lambda _m: None)
    profile = profile or await client.ensure_profile()

    out = Path(opts.out_dir)
    (out / "pages").mkdir(parents=True, exist_ok=True)
    (out / "quarantine").mkdir(parents=True, exist_ok=True)
    if opts.keep_raw:
        (out / "raw").mkdir(parents=True, exist_ok=True)
    manifest = Manifest.load(out) if opts.incremental else Manifest()
    bdb = BoilerplateDB(out / "state" / "boilerplate.json")
    domain_cache: dict = {}

    def boiler_for(url: str) -> dict:
        d = urlsplit(url).netloc
        if d not in domain_cache:
            domain_cache[d] = bdb.lookup(d)
        return domain_cache[d]

    urls = await discover_urls(client, sources, max_pages=opts.max_pages,
                               include=opts.url_include, exclude=opts.url_exclude)
    log(f"discovered {len(urls)} URLs")

    def report_progress(done: int, total: int) -> None:
        if progress_cb:
            try:
                progress_cb(done, total, f"processed {done}/{total} pages")
            except Exception:
                pass

    if opts.deep:
        results = await _crawl_deep(client, profile, urls, opts, log)
    else:
        results = await _crawl_flat(client, profile, urls, opts, log)
    log(f"acquired {len(results)} results")

    used_slugs: set = set()
    done_count = 0

    def process(r: dict, write: bool) -> dict:
        nonlocal done_count
        url = r.get("url") or ""
        domain = urlsplit(url).netloc
        if not r.get("success"):
            rec = {"status": "failed", "error": r.get("error_message", "unknown"),
                   "checked_at": utcnow_iso()}
            manifest.records[url] = rec
            done_count += 1
            report_progress(done_count, len(results))
            return {"url": url, **rec}
        cands = build_candidates(r, url, fit=opts.fit, extractor=opts.extractor)
        if not cands:
            manifest.records[url] = {"status": "failed", "error": "empty content",
                                     "checked_at": utcnow_iso()}
            done_count += 1
            report_progress(done_count, len(results))
            return {"url": url, "status": "failed", "error": "empty content"}

        # clean + score every candidate with its detected content type
        scored = []
        for engine, body in cands:
            cr = clean_with_backend(body, CleaningOptions(
                source_url=url, link_mode=opts.link_mode, image_policy=opts.image_policy,
                citation_mode=opts.citation_mode, site_boilerplate=boiler_for(url)),
                backend=opts.cleaner)
            sc = score_markdown(cr.text, cr.content_type, cr.edits)
            scored.append((engine, cr, sc))
        engine, _, sc = compare_candidates(
            [(e, c.text, c.edits, c.content_type) for e, c, _ in scored])
        cr = next(c for e, c, _ in scored if e == engine)

        content_hash = sha256(cr.text)
        if opts.incremental and manifest.unchanged(url, content_hash):
            manifest.records[url]["checked_at"] = utcnow_iso()
            done_count += 1
            report_progress(done_count, len(results))
            return {"url": url, "status": "unchanged", "score": sc["value"]}

        # observe in BOTH passes (warm-up + write): BoilerplateDB.observe is
        # idempotent per page, so this never double-counts.
        bdb.observe(domain, url, cr.block_hashes)
        domain_cache[domain] = bdb.lookup(domain)

        status = "published" if sc["value"] >= 65 else (
            "review" if sc["value"] >= opts.min_score else "quarantined")
        subdir = "pages" if status in ("published", "review") else "quarantine"
        slug = _slug_with_dedupe(url, used_slugs)
        file_rel = f"{subdir}/{slug}.md"

        meta = r.get("metadata") or {}
        fm = {
            "source_url": url,
            "title": meta.get("title") or extract_title(cr.text, url),
            "source_domain": domain,
            "content_type": cr.content_type,
            "type_confidence": round(cr.type_confidence, 2),
            "crawled_at": utcnow_iso(),
            "extractor": engine,
            "cleaner": opts.cleaner,
            "words": sc["signals"]["words"],
            "tokens_est": estimate_tokens(cr.text),
            "content_hash": content_hash,
            "score": {"value": sc["value"], "band": sc["band"], "notes": sc["notes"]},
            "headings": outline(cr.text),
            "links": count_links_images(cr.text),
            "structure": {k: v for k, v in cr.structure.items() if k != "protected_hashes"},
            "cleaning_edits": cr.edits,
            "notes": cr.warnings,
            "status": status,
        }
        if write:
            write_page(out / subdir, slug + ".md", fm, cr.text)
            if opts.keep_raw:
                raw_body = next((b for e, b in cands if e == "crawl4ai"),
                                cands[0][1])
                write_page(out / "raw", slug + ".md",
                           {"source_url": url, "captured_at": fm["crawled_at"]}, raw_body)
        manifest.records[url] = {"status": status, "file": file_rel,
                                 "score": sc["value"], "content_hash": content_hash,
                                 "extractor": engine, "checked_at": utcnow_iso()}
        done_count += 1
        report_progress(done_count, len(results))
        return {"url": url, "status": status, "score": sc["value"], "file": file_rel,
                "extractor": engine, "notes": sc["notes"]}

    # two-pass: warm the boilerplate DB without writing, then run for real.
    # observe() is idempotent per page, so double observation is harmless.
    if opts.two_pass:
        log("two-pass mode: warm-up pass (no writes)...")
        for r in results:
            process(r, write=False)
        bdb.save()
        domain_cache.clear()

    records = [process(r, write=True) for r in results]
    bdb.save()
    summary = {
        "discovered": len(urls), "crawled": len(results),
        "published": sum(1 for x in records if x["status"] == "published"),
        "review": sum(1 for x in records if x["status"] == "review"),
        "quarantined": sum(1 for x in records if x["status"] == "quarantined"),
        "unchanged": sum(1 for x in records if x["status"] == "unchanged"),
        "failed": sum(1 for x in records if x["status"] == "failed"),
    }
    report = {"finished_at": utcnow_iso(), "summary": summary,
              "options": {k: str(v) for k, v in vars(opts).items()}, "urls": records}
    manifest.stats["last_run"] = summary
    manifest.save(out)
    rp = write_report(out, report)
    log(f"done: {summary}")
    return {"success": True, "summary": summary, "report": str(rp),
            "out_dir": str(out), "urls": records[:100]}
