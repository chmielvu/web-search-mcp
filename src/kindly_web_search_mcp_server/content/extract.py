"""Content extraction using BS4 + markdownify.

Primary: BS4 + markdownify (fast, no heavy deps).
Fallback: regex-based HTML→Markdown when BS4 unavailable.
"""

from __future__ import annotations

import html as _html
import logging
import re

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore

try:
    from markdownify import markdownify as md  # type: ignore
except Exception:  # pragma: no cover
    md = None  # type: ignore

LOGGER = logging.getLogger(__name__)

_MIN_OUTPUT_CHARS = 200


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
    """BS4 + markdownify extraction."""
    if BeautifulSoup is not None and md is not None:
        soup = BeautifulSoup(html, "html.parser")
        for element in soup(["script", "style", "header", "footer", "nav", "aside"]):
            element.decompose()
        return md(str(soup))
    return _simple_html_to_markdown(html)


def extract_content_as_markdown(html: str, *, url: str | None = None) -> str:
    """Extract content from HTML using BS4 + markdownify.

    Falls back to regex-based extraction if BS4/markdownify unavailable.
    """
    result = _bs4_markdownify_fallback(html)
    if result and len(result) >= _MIN_OUTPUT_CHARS:
        LOGGER.info("Extracted via BS4+markdownify: %d chars", len(result))
        return result

    LOGGER.info(
        "BS4+markdownify output short (%s chars), using regex fallback",
        len(result) if result else 0,
    )
    fallback = _simple_html_to_markdown(html)
    LOGGER.info("Regex fallback extraction: %d chars", len(fallback))
    return fallback
