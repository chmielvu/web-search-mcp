"""Specialized resolver for Academic DOIs and Paywalled Papers using Unpaywall and Crossref.

Discovers Open Access full-text PDFs and bibliographic metadata for DOIs (10.xxxx/...).
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

from ..artifact import ContentArtifact, ContentError
from ..options import FetchOptions
from ..safe_fetch import SafeFetchError, safe_fetch_url
from ..sanitize import sanitize_markdown
from ..status_classifier import classify_markdown
from ...telemetry import record_content_error, record_content_resolution
from ...utils.url_canonicalize import canonicalize_url
from .document import _convert_pdf_to_markdown

LOGGER = logging.getLogger(__name__)

# Standard DOI regex pattern: 10.xxxx/xxxx
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


async def fetch_doi_paper_markdown(
    url: str,
    *,
    fetch_options: FetchOptions | None = None,
) -> ContentArtifact:
    """Fetch Open Access academic paper full text or metadata by DOI."""
    options = fetch_options or FetchOptions()
    target = parse_doi_url(url)
    if not target:
        return ContentArtifact(
            input_url=url,
            normalized_url=canonicalize_url(url),
            fetched_url=url,
            status="error",
            source_type="doi",
            fetch_backend="unpaywall_api",
            content_type=None,
            markdown="",
            word_count=0,
            quality_score=0.0,
            error=ContentError(
                code="invalid_doi", message="Could not parse DOI from URL", retryable=False
            ),
        )

    email = "academic_researcher@kindly.ai"
    unpaywall_api_url = f"https://api.unpaywall.org/v2/{target.doi}?email={email}"

    try:
        timeout_sec = options.stage_timeout_seconds or 25.0
        async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
            headers = {"User-Agent": "kindly-web-search-mcp/1.0 (academic-resolver)"}
            resp = await client.get(unpaywall_api_url, headers=headers)
            if resp.status_code != 200:
                # If Unpaywall has no record, return error so generic cascade can try publisher page
                return ContentArtifact(
                    input_url=url,
                    normalized_url=canonicalize_url(url),
                    fetched_url=url,
                    status="error",
                    source_type="doi",
                    fetch_backend="unpaywall_api",
                    content_type=None,
                    markdown="",
                    word_count=0,
                    quality_score=0.0,
                    error=ContentError(
                        code="unpaywall_not_found",
                        message=f"Unpaywall returned HTTP {resp.status_code} for DOI {target.doi}",
                        retryable=False,
                    ),
                )

            data = resp.json()
            meta_md = render_unpaywall_metadata_markdown(data, target.doi, url)
            best_oa = data.get("best_oa_location") or {}
            pdf_url = best_oa.get("url_for_pdf")

            # Try to fetch and extract the full-text PDF if OA PDF exists
            if pdf_url:
                try:
                    fetched_pdf = await safe_fetch_url(
                        pdf_url,
                        timeout_seconds=timeout_sec,
                        max_response_bytes=options.max_response_bytes,
                    )
                    if fetched_pdf.is_pdf:
                        pdf_md = _convert_pdf_to_markdown(fetched_pdf.body, pdf_url)
                        full_content = (
                            f"{meta_md}\n\n---\n\n## Full Text (Open Access PDF)\n\n{pdf_md}"
                        )
                        clean_text = sanitize_markdown(full_content)
                        cls = classify_markdown(clean_text)
                        word_count = len(clean_text.split())

                        record_content_resolution(
                            stage="unpaywall_pdf",
                            url=url,
                            success=True,
                            size_bytes=len(clean_text.encode("utf-8")),
                            word_count=word_count,
                            extraction_method="unpaywall_pdf_extract",
                        )

                        return ContentArtifact(
                            input_url=url,
                            normalized_url=canonicalize_url(url),
                            fetched_url=pdf_url,
                            status=cls.status,
                            source_type="academic_doi",
                            fetch_backend="unpaywall_pdf_extract",
                            content_type="text/markdown",
                            markdown=clean_text,
                            word_count=word_count,
                            quality_score=1.0 if cls.status == "success" else 0.7,
                            error=None,
                        )
                except Exception as pdf_exc:
                    LOGGER.debug("Failed fetching OA PDF %s: %s", pdf_url, pdf_exc)

            # Fallback to rich bibliographic metadata
            clean_meta = sanitize_markdown(meta_md)
            cls = classify_markdown(clean_meta)
            word_count = len(clean_meta.split())

            return ContentArtifact(
                input_url=url,
                normalized_url=canonicalize_url(url),
                fetched_url=url,
                status="success" if word_count >= 15 else cls.status,
                source_type="academic_doi",
                fetch_backend="unpaywall_metadata",
                content_type="text/markdown",
                markdown=clean_meta,
                word_count=word_count,
                quality_score=0.8,
                error=None,
            )
    except SafeFetchError as exc:
        record_content_error(stage="unpaywall", url=url, error_type=exc.code)
        return ContentArtifact(
            input_url=url,
            normalized_url=canonicalize_url(url),
            fetched_url=url,
            status="error",
            source_type="academic_doi",
            fetch_backend="unpaywall_api",
            content_type=None,
            markdown="",
            word_count=0,
            quality_score=0.0,
            error=ContentError(code=exc.code, message=str(exc), retryable=False),
        )
    except Exception as exc:
        record_content_error(stage="unpaywall", url=url, error_type=type(exc).__name__)
        return ContentArtifact(
            input_url=url,
            normalized_url=canonicalize_url(url),
            fetched_url=url,
            status="error",
            source_type="academic_doi",
            fetch_backend="unpaywall_api",
            content_type=None,
            markdown="",
            word_count=0,
            quality_score=0.0,
            error=ContentError(code=type(exc).__name__, message=str(exc)[:500], retryable=True),
        )
