from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore


def _soup(html: str):
    if BeautifulSoup is None:
        return None
    return BeautifulSoup(html or "", "html.parser")


def _safe_domain(url: str) -> str | None:
    parsed = urlparse(url)
    return parsed.netloc.lower() or None


def strip_html_selectors(html: str, selectors: str | None) -> str:
    if not selectors:
        return html
    soup = _soup(html)
    if soup is None:
        return html
    for selector in [part.strip() for part in selectors.split(",") if part.strip()]:
        for element in soup.select(selector):
            element.decompose()
    return str(soup)


def extract_html_metadata(
    html: str, *, page_url: str, fetched_url: str | None = None
) -> dict[str, str]:
    soup = _soup(html)
    metadata: dict[str, str] = {
        "fetched_url": fetched_url or page_url,
        "domain": _safe_domain(fetched_url or page_url) or "",
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
    link = soup.find(
        "link", attrs={"rel": lambda value: value and "canonical" in value}
    )
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


def extract_html_links(
    html: str,
    *,
    base_url: str,
    max_links: int = 25,
    include_external: bool = True,
    same_domain_only: bool = False,
) -> list[dict[str, str | bool]]:
    soup = _soup(html)
    if soup is None:
        return []

    base_domain = _safe_domain(base_url)
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
        if same_domain_only or not include_external:
            if not internal:
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


def extract_sitemap_links(
    xml_text: str,
    *,
    base_url: str,
    max_links: int = 100,
    include_external: bool = True,
    same_domain_only: bool = False,
) -> list[dict[str, str | bool]]:
    base_domain = _safe_domain(base_url)
    links: list[dict[str, str | bool]] = []
    seen: set[str] = set()
    for raw_url in re.findall(
        r"<loc>\s*(.*?)\s*</loc>", xml_text or "", flags=re.I | re.S
    ):
        candidate = raw_url.strip()
        if not candidate:
            continue
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"}:
            continue
        domain = parsed.netloc.lower() or ""
        internal = bool(base_domain and domain == base_domain)
        if same_domain_only or not include_external:
            if not internal:
                continue
        normalized_url = parsed._replace(fragment="").geturl()
        if normalized_url in seen:
            continue
        seen.add(normalized_url)
        links.append(
            {
                "url": normalized_url,
                "text": normalized_url,
                "domain": domain,
                "internal": internal,
            }
        )
        if len(links) >= max_links:
            break
    return links
