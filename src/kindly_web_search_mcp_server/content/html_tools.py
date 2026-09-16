"""HTML tooling: BeautifulSoup access, markdownify conversion, metadata/links.

markdownify is the sole HTML-to-Markdown implementation; there is no
Trafilatura rung and no regex fallback. No classification, sanitizing
passes, scoring, or artifact construction here.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover
    BeautifulSoup = None
try:
    from markdownify import markdownify as _markdownify
except Exception:  # pragma: no cover
    _markdownify = None


def soup_from_html(html: str):
    """Return a BeautifulSoup tree or None when bs4 is unavailable."""
    if BeautifulSoup is None:
        return None
    return BeautifulSoup(html or "", "html.parser")


def url_hostname(url: str) -> str | None:
    """Return the lowercased hostname of a URL or None when absent."""
    parsed = urlparse(url)
    return parsed.netloc.lower() or None


def html_to_markdown(html: str, *, url: str | None = None) -> str:
    """Convert HTML to Markdown with markdownify; raise when unavailable."""
    _ = url
    if _markdownify is None:
        raise RuntimeError("markdownify is required for local HTML conversion")
    soup = soup_from_html(html)
    if soup is not None:
        for element in soup(["script", "style"]):
            element.decompose()
        return _markdownify(str(soup), heading_style="ATX").strip()
    return _markdownify(html, heading_style="ATX").strip()


def extract_metadata(html: str, *, page_url: str, fetched_url: str | None = None) -> dict[str, str]:
    """Extract title/description/site/canonical/language from HTML."""
    soup = soup_from_html(html)
    metadata: dict[str, str] = {
        "fetched_url": fetched_url or page_url,
        "domain": url_hostname(fetched_url or page_url) or "",
    }
    if soup is None:
        return {key: value for key, value in metadata.items() if value}

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    if title:
        metadata["title"] = title

    def _meta(*, name: str | None = None, property: str | None = None) -> str:
        attrs: dict[str, str] = {}
        if name:
            attrs["name"] = name
        if property:
            attrs["property"] = property
        tag = soup.find("meta", attrs=attrs)
        content = tag.get("content") if tag else None
        return content.strip() if isinstance(content, str) and content.strip() else ""

    for key, value in (
        ("description", _meta(name="description") or _meta(property="og:description")),
        ("site_name", _meta(property="og:site_name") or _meta(name="application-name")),
    ):
        if value:
            metadata[key] = value

    canonical = ""
    link = soup.find("link", attrs={"rel": lambda value: value and "canonical" in value})
    if link:
        href = link.get("href")
        canonical = href.strip() if isinstance(href, str) and href.strip() else ""
    if canonical:
        metadata["canonical_url"] = canonical

    html_tag = soup.find("html")
    if html_tag:
        lang = html_tag.get("lang")
        if isinstance(lang, str) and lang.strip():
            metadata["language"] = lang.strip()

    return metadata


def extract_links(
    html: str,
    *,
    base_url: str,
    max_links: int = 25,
    include_external: bool = True,
    same_domain_only: bool = False,
) -> list[dict[str, str | bool]]:
    """Extract outbound links from HTML with SSRF-safe scheme filtering."""
    soup = soup_from_html(html)
    if soup is None:
        return []

    base_domain = url_hostname(base_url)
    links: list[dict[str, str | bool]] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        absolute_url = urljoin(base_url, href)
        parsed = urlparse(absolute_url)
        if parsed.scheme not in {"http", "https"}:
            continue

        domain = parsed.netloc.lower() or ""
        internal = bool(base_domain and domain == base_domain)
        if (same_domain_only or not include_external) and not internal:
            continue

        normalized_url = parsed._replace(fragment="").geturl()
        if normalized_url in seen:
            continue
        seen.add(normalized_url)

        text = anchor.get_text(" ", strip=True) or normalized_url
        links.append(
            {
                "url": normalized_url,
                "text": text,
                "domain": domain,
                "internal": internal,
            }
        )
        if len(links) >= max_links:
            break

    return links


__all__ = [
    "extract_links",
    "extract_metadata",
    "html_to_markdown",
    "soup_from_html",
    "url_hostname",
]
