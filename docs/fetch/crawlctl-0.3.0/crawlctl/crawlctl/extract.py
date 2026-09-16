"""Extraction candidate generation: Crawl4AI primary + trafilatura arbitration.

trafilatura 2.x is the benchmark leader for main-content extraction from HTML
(official 2026-08 eval: F1 0.924 standard / 0.925 favor_precision). We run it
on the server's ``cleaned_html`` as a second opinion; the scorer arbitrates
after cleaning both candidates with type calibration.
"""

from __future__ import annotations

from typing import Optional


def trafilatura_candidate(cleaned_html: str, source_url: str,
                          favor_precision: bool = False) -> str:
    """HTML -> markdown via trafilatura. Empty string when unavailable/failed."""
    if not cleaned_html:
        return ""
    try:
        import trafilatura
    except ImportError:
        return ""
    try:
        return trafilatura.extract(
            cleaned_html,
            url=source_url,
            output_format="markdown",
            include_tables=True,
            include_links=True,
            include_formatting=True,
            favor_precision=favor_precision,
            favor_recall=not favor_precision,
        ) or ""
    except Exception:
        return ""


def build_candidates(result: dict, url: str, *, fit: bool = True,
                     extractor: str = "auto") -> list:
    """Return [(engine, markdown)] extraction candidates for one result.

    engine: 'crawl4ai' (fit or raw from the server) and optionally
    'trafilatura' (from cleaned_html) when installed and extractor allows.
    """
    from .client import extract_markdown

    raw, fitmd = extract_markdown(result)
    primary = (fitmd or raw) if fit else raw
    cands = []
    if extractor in ("auto", "crawl4ai") and primary:
        cands.append(("crawl4ai", primary))
    if extractor in ("auto", "trafilatura") and result.get("cleaned_html"):
        alt = trafilatura_candidate(result["cleaned_html"], url)
        if alt:
            cands.append(("trafilatura", alt))
    if not cands and raw:
        cands.append(("crawl4ai", raw))
    return cands
