"""HTML extraction ladder: Trafilatura -> BS4+markdownify -> regex fallback.

Moved verbatim from ``content/sanitize.py`` (conversion belongs with the
Jina/crawl4ai rungs; this module is the rare local fallback). Each rung
returns ``sanitize_markdown(result)`` only — ``sanitize_markdown`` already
strips boilerplate, so the old double pass was redundant.
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
    import trafilatura  # type: ignore[import-not-found,import-untyped]
except Exception:  # pragma: no cover
    trafilatura = None  # type: ignore

try:
    from markdownify import markdownify as md  # type: ignore
except Exception:  # pragma: no cover
    md = None  # type: ignore

from ..utils.text_clean import sanitize_markdown

LOGGER = logging.getLogger(__name__)

_MIN_OUTPUT_CHARS = 200


def _strip_tags_keep_text(raw_html: str) -> str:
    """Remove script/style tags and convert block-level tags to newlines."""
    cleaned = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw_html or "")
    cleaned = re.sub(r"(?i)<br\s*/?>", "\n", cleaned)
    cleaned = re.sub(r"(?i)</p\s*>", "\n\n", cleaned)
    cleaned = re.sub(r"(?i)</div\s*>", "\n\n", cleaned)
    cleaned = re.sub(r"(?i)</li\s*>", "\n\n", cleaned)
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
    h = re.sub(
        r'(?is)<a[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>',
        r"[\2](\1)",
        h,
    )
    h = re.sub(r"(?is)<code[^>]*>(.*?)</code>", r"`\1`", h)
    h = re.sub(
        r"(?is)<pre[^>]*>(.*?)</pre>",
        lambda m: f"```\n{_strip_tags_keep_text(m.group(1))}\n```\n",
        h,
    )
    h = re.sub(
        r"(?is)<blockquote[^>]*>(.*?)</blockquote>",
        lambda m: f"> {_strip_tags_keep_text(m.group(1)).replace(chr(10), chr(10) + '> ')}\n\n",
        h,
    )
    h = re.sub(
        r'(?is)<img[^>]*src=["\']([^"\']*)["\'][^>]*alt=["\']([^"\']*)["\'][^>]*>',
        r"![\2](\1)",
        h,
    )
    h = re.sub(r'(?is)<img[^>]*src=["\']([^"\']*)["\'][^>]*>', r"![](\1)", h)
    return _strip_tags_keep_text(h)


def _trafilatura_extract(html: str, *, url: str | None = None) -> str | None:
    """High-precision article and main text extraction via Trafilatura."""
    if trafilatura is not None:
        try:
            extracted = trafilatura.extract(
                html,
                url=url,
                output_format="markdown",
                include_links=True,
                include_images=True,
                include_tables=True,
                include_comments=False,
            )
            if extracted and len(extracted.strip()) >= _MIN_OUTPUT_CHARS:
                return extracted.strip()
        except Exception as exc:
            LOGGER.debug("Trafilatura extraction failed: %s", exc)
    return None


def _bs4_markdownify_fallback(html: str) -> str:
    """BS4 + markdownify extraction with conservative structural chrome pruning."""
    if BeautifulSoup is not None and md is not None:
        soup = BeautifulSoup(html, "html.parser")
        for element in soup(["script", "style", "header", "footer", "nav", "aside"]):
            element.decompose()
        _prune_chrome_attributes(soup)
        return md(str(soup))
    return _simple_html_to_markdown(html)


_CHROME_ATTR_TOKEN_RE = re.compile(
    r"(?i)(?:^|\W)(?:nav(?:bar)?|sidebar|breadcrumb|pagination|cookie|consent|promo|sponsor"
    r"|social|share|advert|advertisement|complementary)(?:\W|$)"
)
_CHROME_PROTECTED_TAGS = frozenset(
    {"article", "main", "code", "pre", "table", "tbody", "tr", "td", "th"}
)


def _prune_chrome_attributes(soup) -> None:  # type: ignore[no-untyped-def]
    """Drop elements whose class/id/role tokens mark structural chrome.

    Conservative: content containers (article/main) and code/table nodes are
    never removed; word-boundary token matching avoids ``id="loader"`` style
    false hits that naive substring matching would cause.
    """
    for element in soup.find_all(attrs={"aria-hidden": "true"}):
        element.decompose()
    for element in soup.find_all(True):
        if element.name in _CHROME_PROTECTED_TAGS or getattr(element, "decomposed", False):
            continue
        attrs = " ".join(
            part
            for key in ("class", "id", "role")
            for part in (
                [element.get(key)]
                if isinstance(element.get(key), str)
                else (element.get(key) or [])
            )
            if part
        )
        if _CHROME_ATTR_TOKEN_RE.search(attrs):
            element.decompose()


def extract_html_as_markdown(html: str, *, url: str | None = None) -> str:
    """Extract content from HTML using BS4+markdownify -> regex fallback.
    The returned markdown is stripped of common boilerplate and sanitized.
    """
    # 1. Primary: Trafilatura (main content extraction)
    traf_result = _trafilatura_extract(html, url=url)
    if traf_result:
        LOGGER.info("Extracted via Trafilatura: %d chars", len(traf_result))
        return sanitize_markdown(traf_result)

    # 2. Secondary: BS4 + markdownify
    result = _bs4_markdownify_fallback(html)
    if result and len(result) >= _MIN_OUTPUT_CHARS:
        LOGGER.info("Extracted via BS4+markdownify: %d chars", len(result))
        return sanitize_markdown(result)

    # 3. Fallback: simple regex
    LOGGER.info(
        "BS4+markdownify output short (%s chars), using regex fallback",
        len(result) if result else 0,
    )
    fallback = _simple_html_to_markdown(html)
    LOGGER.info("Regex fallback extraction: %d chars", len(fallback))
    return sanitize_markdown(fallback)
