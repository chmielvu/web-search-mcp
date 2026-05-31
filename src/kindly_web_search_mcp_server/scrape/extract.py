"""Content extraction using trafilatura with two-pass strategy.

Pass 1: precision-focused (clean output, deduplicated).
Pass 2: recall-focused (fallback for weak/noisy pages).

References:
  https://trafilatura.readthedocs.io/en/latest/usage-python.html#extraction-settings
  https://trafilatura.readthedocs.io/en/latest/deduplication.html
"""

from __future__ import annotations

import html as _html
import logging
import re

from trafilatura import extract as _trafilatura_extract  # type: ignore

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore

try:
    from markdownify import markdownify as md  # type: ignore
except Exception:  # pragma: no cover
    md = None  # type: ignore

LOGGER = logging.getLogger(__name__)

# Thresholds for two-pass strategy
_MIN_OUTPUT_CHARS = 200  # Below this, switch to recall pass


def _strip_tags_keep_text(raw_html: str) -> str:
    """Remove script/style tags and convert block-level tags to newlines."""
    cleaned = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw_html or "")
    cleaned = re.sub(r"(?i)<br\s*/?>", "\n", cleaned)
    cleaned = re.sub(r"(?i)</p\s*>", "\n\n", cleaned)
    cleaned = re.sub(r"(?i)</div\s*>", "\n\n", cleaned)
    cleaned = re.sub(r"(?i)</li\s*>", "\n", cleaned)
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    cleaned = _html.unescape(cleaned)
    cleaned = re.sub(r"[ \t\r\f\v]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _simple_html_to_markdown(raw_html: str) -> str:
    """Very small HTML→Markdown fallback when BS4/markdownify unavailable."""
    h = raw_html or ""
    for level in range(1, 7):
        pattern = rf"(?is)<h{level}[^>]*>(.*?)</h{level}>"
        def repl(m: re.Match[str], lvl: int = level) -> str:
            return "\n" + ("#" * lvl) + " " + _strip_tags_keep_text(m.group(1)) + "\n\n"

        h = re.sub(pattern, repl, h)
    h = re.sub(
        r"(?is)<li[^>]*>(.*?)</li>",
        lambda m: f"- {_strip_tags_keep_text(m.group(1))}\n",
        h,
    )
    h = re.sub(
        r"(?is)<p[^>]*>(.*?)</p>",
        lambda m: f"{_strip_tags_keep_text(m.group(1))}\n\n",
        h,
    )
    return _strip_tags_keep_text(h)


def _bs4_markdownify_fallback(html: str) -> str:
    """BS4 + markdownify extraction, used when trafilatura is unavailable."""
    if BeautifulSoup is not None and md is not None:
        soup = BeautifulSoup(html, "html.parser")
        for element in soup(["script", "style", "header", "footer", "nav", "aside"]):
            element.decompose()
        main_content = soup.find("main") or soup.find("article") or soup.find("body")
        if main_content:
            return md(str(main_content), heading_style="ATX", strip=["a", "assets"])
        return "Could not extract main content."
    return _simple_html_to_markdown(html)


def _trafilatura_extract_pass(
    html: str,
    url: str | None = None,
    *,
    precision: bool = False,
    recall: bool = False,
) -> str | None:
    """Single trafilatura pass with configurable precision/recall.

    Args:
        html: Raw HTML content.
        url: Page URL for absolute link resolution.
        precision: If True, favor precision (cleaner output, less noise).
        recall: If True, favor recall (more content, less strict filtering).
    """
    try:
        text = _trafilatura_extract(
            html,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            include_links=True,
            deduplicate=True,
            url=url,
            favor_precision=precision,
            favor_recall=recall,
        )
        if text:
            return text
    except Exception as exc:
        LOGGER.warning(
            "Trafilatura %s extraction failed: %s",
            "precision" if precision else "recall" if recall else "default",
            exc,
        )
    return None


def extract_content_as_markdown(html: str, *, url: str | None = None) -> str:
    """Extract content from HTML using two-pass trafilatura strategy.

    Pass 1: precision-focused with deduplication.
    Pass 2: recall-focused if output is too short or noisy.

    Falls back to BS4/markdownify if trafilatura produces nothing.
    """
    # Pass 1: precision (clean, deduplicated output)
    result = _trafilatura_extract_pass(html, url=url, precision=True)
    if result is not None and len(result) >= _MIN_OUTPUT_CHARS:
        LOGGER.info("Extracted via trafilatura precision pass: %d chars", len(result))
        return result

    # Pass 2: recall (higher coverage, fallback for weak pages)
    LOGGER.info(
        "Trafilatura precision weak (%s chars), retrying with recall",
        len(result) if result else 0,
    )
    result = _trafilatura_extract_pass(html, url=url, recall=True)
    if result is not None:
        LOGGER.info("Extracted via trafilatura recall pass: %d chars", len(result))
        return result

    # Fallback: BS4/markdownify
    fallback = _bs4_markdownify_fallback(html)
    LOGGER.info("Trafilatura failed, BS4 fallback: %d chars", len(fallback))
    return fallback
