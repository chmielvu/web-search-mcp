"""Specialized resolver for Academic DOIs and Paywalled Papers using Unpaywall and Crossref.

Discovers Open Access full-text PDFs and bibliographic metadata for DOIs (10.xxxx/...).
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

from ..http_utils import SafeFetchError, safe_fetch_url
from ..models import AcquisitionError, FetchContext, RawDocument, ResolverTarget, ParsedURL
from ._bridge import _text_document
from .files import convert_pdf_to_markdown

LOGGER = logging.getLogger(__name__)

_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)")
_DOI_ORG_RE = re.compile(r"^https?://(?:dx\.)?doi\.org/(10\.\d{4,9}/.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class DOITarget:
    doi: str


def parse_doi_url(url: str) -> DOITarget | None:
    """Extract a DOI from a doi.org URL or publisher URL."""
    try:
        m_doi_org = _DOI_ORG_RE.match(url)
        if m_doi_org:
            doi = m_doi_org.group(1).strip()
            # Remove trailing slashes or URL fragments
            doi = doi.split("#")[0].split("?")[0].rstrip("/")
            return DOITarget(doi=doi)

        # Look for DOI pattern in path or query
        parsed = urllib.parse.urlparse(url)
        full_target = f"{parsed.path}?{parsed.query}"
        m = _DOI_RE.search(full_target)
        if m:
            doi = m.group(1).strip().rstrip("/")
            return DOITarget(doi=doi)

        return None
    except Exception:
        return None


def render_unpaywall_metadata_markdown(data: dict[str, Any], doi: str, url: str) -> str:
    """Render bibliographic metadata to clean Markdown."""
    title = data.get("title") or "Academic Paper"
    year = data.get("year") or ""
    journal_name = data.get("journal_name") or ""
    publisher = data.get("publisher") or ""
    is_oa = data.get("is_oa", False)
    oa_status = data.get("oa_status") or ""
    best_oa = data.get("best_oa_location") or {}
    pdf_url = best_oa.get("url_for_pdf")
    landing_url = best_oa.get("url_for_landing_page") or f"https://doi.org/{doi}"
    oa_license = best_oa.get("license") or ""

    # Authors
    z_authors = data.get("z_authors") or []
    author_names = []
    for a in z_authors:
        family = a.get("family", "")
        given = a.get("given", "")
        if family and given:
            author_names.append(f"{given} {family}")
        elif family:
            author_names.append(family)
        elif a.get("name"):
            author_names.append(a["name"])

    lines: list[str] = [
        f"# {title}",
        f"**DOI:** [{doi}](https://doi.org/{doi}) | **Source:** {url}",
    ]

    if author_names:
        lines.append(f"**Authors:** {', '.join(author_names[:10])}")
        if len(author_names) > 10:
            lines.append(f"_... and {len(author_names) - 10} more authors_")

    meta_parts: list[str] = []
    if journal_name:
        meta_parts.append(f"**Journal:** {journal_name}")
    if year:
        meta_parts.append(f"**Year:** {year}")
    if publisher:
        meta_parts.append(f"**Publisher:** {publisher}")
    if is_oa:
        oa_label = f"Open Access ({oa_status})" if oa_status else "Open Access"
        meta_parts.append(f"**Access:** `{oa_label}`")
    else:
        meta_parts.append("**Access:** `Paywalled / Closed`")

    if meta_parts:
        lines.append(" | ".join(meta_parts))

    links: list[str] = [f"[Publisher / Landing Page]({landing_url})"]
    if pdf_url:
        links.append(f"[Open Access PDF]({pdf_url})")
    if oa_license:
        links.append(f"**License:** `{oa_license}`")
    lines.append("\n**Links:** " + " • ".join(links))

    return "\n".join(lines).strip() + "\n"


async def fetch_doi_raw(target: ResolverTarget, ctx: FetchContext) -> RawDocument:
    """Acquire a DOI paper via Unpaywall/OA PDF or metadata fallback."""

    url = target.values.get("url") or target.url
    parsed = parse_doi_url(url)
    if parsed is None:
        raise AcquisitionError(code="invalid_doi", message="Could not parse DOI from URL.")
    api_url = f"https://api.unpaywall.org/v2/{parsed.doi}?email=academic_researcher@kindly.ai"
    try:
        response = await ctx.http_client.get(
            api_url,
            headers={"User-Agent": "kindly-web-search-mcp/1.0 (academic-resolver)"},
            timeout=ctx.timeout(25.0),
        )
    except Exception as exc:
        raise AcquisitionError(code="unpaywall_transport", message=str(exc)[:300]) from exc
    if response.status_code != 200:
        raise AcquisitionError(
            code="unpaywall_not_found",
            message=f"Unpaywall returned HTTP {response.status_code}.",
            http_status=response.status_code,
        )
    try:
        data = response.json()
    except Exception as exc:
        raise AcquisitionError(code="unpaywall_shape", message=str(exc)[:300]) from exc
    if not isinstance(data, dict):
        raise AcquisitionError(
            code="unpaywall_shape", message="Unpaywall payload was not an object."
        )
    meta_md = render_unpaywall_metadata_markdown(data, parsed.doi, url)
    pdf_url = (data.get("best_oa_location") or {}).get("url_for_pdf")
    if pdf_url:
        try:
            fetched_pdf = await safe_fetch_url(pdf_url, max_response_bytes=ctx.max_response_bytes)
        except SafeFetchError as exc:
            raise AcquisitionError(code=exc.code, message=str(exc)) from exc
        if fetched_pdf.is_pdf:
            pdf_md = convert_pdf_to_markdown(fetched_pdf.body, pdf_url)
            return _text_document(
                url,
                "doi",
                f"{meta_md}\n\n---\n\n## Full Text (Open Access PDF)\n\n{pdf_md}",
                fetched_url=fetched_pdf.fetched_url or pdf_url,
                scope="full",
                complete=True,
            )
    return _text_document(url, "doi", meta_md, scope="excerpt", complete=None)


def match_doi(parsed: ParsedURL) -> ResolverTarget | None:
    path = parsed.parts.path or ""
    if re.search(r"10\.\d{4,9}/", path):
        return ResolverTarget(url=parsed.url, kind="doi", values={"url": parsed.url})
    return None
