from __future__ import annotations

import contextlib
import io
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

import httpx

from ...utils.environment import get_int_env
from ..models import FetchContext, ParsedURL, RawDocument, ResolverTarget
from ._bridge import bridge_text_producer


class ArxivError(RuntimeError):
    pass


_ARXIV_ID_NEW_RE = re.compile(r"^(?P<id>\d{4}\.\d{4,5})(?P<ver>v\d+)?$", re.IGNORECASE)
_ARXIV_ID_LEGACY_RE = re.compile(r"^(?P<cat>[^/]+)/(?P<num>\d{7})(?P<ver>v\d+)?$", re.IGNORECASE)


@dataclass(frozen=True)
class ArxivMetadata:
    arxiv_id: str
    entry_id: str | None
    title: str | None
    authors: list[str]
    abstract: str | None
    published: str | None
    updated: str | None
    primary_category: str | None
    categories: list[str]
    abs_url: str
    pdf_url: str


@dataclass(frozen=True)
class PdfMarkdown:
    markdown: str
    page_count: int
    pages_rendered: int


@contextlib.contextmanager
def _suppress_third_party_output():
    """
    Suppress noisy third-party prints during PDF conversion.

    Some PDF/LLM helper libraries print advisories directly to stdout/stderr (e.g., recommending
    `pymupdf_layout`). In MCP stdio mode, *any* accidental output can corrupt the protocol stream.
    """
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        yield


def _normalize_whitespace(text: str) -> str:
    # Keep this conservative: preserve paragraphs where possible but avoid huge indentation.
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join([ln for ln in lines if ln]).strip()


def _normalize_title(text: str) -> str:
    # Titles should be a single line (avoid breaking Markdown list items).
    return " ".join(text.split()).strip()


def parse_arxiv_url(url: str) -> str:
    """
    Parse an arXiv URL and extract the arXiv identifier.

    Supported:
    - https://arxiv.org/abs/<id>
    - https://arxiv.org/pdf/<id>
    - https://arxiv.org/pdf/<id>.pdf
    """
    parsed = urlparse(url)
    host_raw = parsed.hostname or ""
    if not host_raw:
        raise ArxivError("URL has no hostname.")

    host = host_raw.lower()
    if not host.endswith("arxiv.org"):
        raise ArxivError(f"Unsupported arXiv host: {host}")

    path = parsed.path or ""
    if not path:
        raise ArxivError("URL has no path.")

    # Strip trailing slashes and decode any percent-encoding.
    path = unquote(path).rstrip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        raise ArxivError("URL is not a recognized arXiv paper URL.")

    prefix = parts[0].lower()
    if prefix not in {"abs", "pdf"}:
        raise ArxivError("URL is not a recognized arXiv abs/pdf URL.")

    arxiv_id = "/".join(parts[1:])
    if arxiv_id.lower().endswith(".pdf"):
        arxiv_id = arxiv_id[: -len(".pdf")]

    arxiv_id = arxiv_id.strip()
    if not arxiv_id:
        raise ArxivError("Empty arXiv identifier.")

    if _ARXIV_ID_NEW_RE.match(arxiv_id) or _ARXIV_ID_LEGACY_RE.match(arxiv_id):
        return arxiv_id

    raise ArxivError(f"Unrecognized arXiv identifier format: {arxiv_id}")


def _default_user_agent() -> str:
    return (
        os.environ.get("ARXIV_USER_AGENT", "").strip() or "web-search-mcp/0.0.1 (arXiv retriever)"
    )


def _parse_arxiv_atom_xml(xml_text: str, *, arxiv_id: str) -> ArxivMetadata:
    # Atom feed uses namespaces; arXiv-specific fields are under arxiv ns.
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    root = ET.fromstring(xml_text)
    entry = root.find("atom:entry", ns)
    if entry is None:
        raise ArxivError("arXiv API response contained no entry.")

    entry_id = (entry.findtext("atom:id", default="", namespaces=ns) or "").strip() or None
    title_raw = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
    summary_raw = (entry.findtext("atom:summary", default="", namespaces=ns) or "").strip()
    published = (entry.findtext("atom:published", default="", namespaces=ns) or "").strip() or None
    updated = (entry.findtext("atom:updated", default="", namespaces=ns) or "").strip() or None

    title = _normalize_title(title_raw) if title_raw else None
    abstract = _normalize_whitespace(summary_raw) if summary_raw else None

    authors: list[str] = []
    for a in entry.findall("atom:author", ns):
        name = (a.findtext("atom:name", default="", namespaces=ns) or "").strip()
        if name:
            authors.append(name)

    categories: list[str] = []
    for c in entry.findall("atom:category", ns):
        term = (c.attrib.get("term") or "").strip()
        if term:
            categories.append(term)

    primary_category = None
    pc = entry.find("arxiv:primary_category", ns)
    if pc is not None:
        primary_category = (pc.attrib.get("term") or "").strip() or None

    # Find PDF link. arXiv commonly uses link[@title="pdf"].
    pdf_url = None
    for link in entry.findall("atom:link", ns):
        href = (link.attrib.get("href") or "").strip()
        if not href:
            continue
        if (link.attrib.get("title") or "").lower() == "pdf":
            pdf_url = href
            break
        if (link.attrib.get("type") or "").lower() == "application/pdf":
            pdf_url = href
            break

    # Canonical abs/pdf URLs (ensure we always have them even if API omits).
    abs_url = f"https://arxiv.org/abs/{arxiv_id}"
    if pdf_url is None:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

    return ArxivMetadata(
        arxiv_id=arxiv_id,
        entry_id=entry_id,
        title=title,
        authors=authors,
        abstract=abstract,
        published=published,
        updated=updated,
        primary_category=primary_category,
        categories=categories,
        abs_url=abs_url,
        pdf_url=pdf_url,
    )


async def _fetch_arxiv_metadata(
    arxiv_id: str,
    *,
    http_client: httpx.AsyncClient,
) -> ArxivMetadata:
    url = "https://export.arxiv.org/api/query"
    headers = {"User-Agent": _default_user_agent()}
    params = {"id_list": arxiv_id, "start": 0, "max_results": 1}
    resp = await http_client.get(url, headers=headers, params=params)
    resp.raise_for_status()
    return _parse_arxiv_atom_xml(resp.text, arxiv_id=arxiv_id)


async def _download_pdf_bytes(
    pdf_url: str,
    *,
    http_client: httpx.AsyncClient,
) -> bytes:
    headers = {"User-Agent": _default_user_agent()}
    resp = await http_client.get(pdf_url, headers=headers)
    resp.raise_for_status()
    content = resp.content

    ctype = (resp.headers.get("content-type") or "").lower()
    is_pdf_type = "pdf" in ctype
    is_pdf_sig = content[:5] == b"%PDF-"
    if not (is_pdf_type or is_pdf_sig):
        raise ArxivError(f"Downloaded content does not look like a PDF (content-type={ctype!r}).")

    if not is_pdf_sig:
        raise ArxivError("Downloaded content did not have a %PDF- signature.")

    return content


def _pdf_bytes_to_markdown_best_effort(
    pdf_bytes: bytes,
    *,
    max_pages: int,
) -> PdfMarkdown:
    """
    Convert PDF bytes to Markdown in-memory.

    Prefers `pymupdf4llm` if installed; otherwise falls back to plain text extraction.
    """
    # Prefer the modern `pymupdf` import (PyMuPDF >= 1.24), but fall back to `fitz`
    # for older installs.
    try:
        import pymupdf  # type: ignore
    except Exception:
        pymupdf = None  # type: ignore

    if pymupdf is None:
        try:
            import fitz as pymupdf  # type: ignore
        except Exception as e:  # pragma: no cover
            raise ArxivError("PyMuPDF is required for PDF processing but is not installed.") from e

    # Import layout helpers if available. This can improve downstream layout extraction.
    try:  # pragma: no cover
        import pymupdf.layout  # type: ignore
    except Exception:
        # Some installs may provide this as a standalone distribution.
        with contextlib.suppress(Exception):
            import pymupdf_layout  # type: ignore  # noqa: F401

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        page_count = int(doc.page_count)
        # 0 means unlimited - render all pages
        effective_max = page_count if max_pages <= 0 else max_pages
        pages_rendered = min(effective_max, page_count)
        with _suppress_third_party_output():
            try:
                import pymupdf4llm  # type: ignore
            except Exception:
                pymupdf4llm = None  # type: ignore

        if pymupdf4llm is not None and hasattr(pymupdf4llm, "to_markdown"):
            # Prefer using `pymupdf4llm` only if we can limit pages; otherwise fall back to
            # our own page-by-page extraction to enforce bounds deterministically.
            import inspect

            pages = list(range(pages_rendered))
            try:
                sig = inspect.signature(pymupdf4llm.to_markdown)
                params = sig.parameters
                if "pages" in params:
                    with _suppress_third_party_output():
                        md = str(pymupdf4llm.to_markdown(doc, pages=pages))
                    return PdfMarkdown(
                        markdown=md,
                        page_count=page_count,
                        pages_rendered=pages_rendered,
                    )
                if "page_numbers" in params:
                    with _suppress_third_party_output():
                        md = str(pymupdf4llm.to_markdown(doc, page_numbers=pages))
                    return PdfMarkdown(
                        markdown=md,
                        page_count=page_count,
                        pages_rendered=pages_rendered,
                    )
            except Exception:
                # Fall back below.
                pass

        # Fallback: plain-text per page (still valid Markdown, just minimal structure).
        parts: list[str] = []
        for i in range(pages_rendered):
            page = doc.load_page(i)
            try:
                text = str(page.get_text("markdown") or "").strip()
            except Exception:
                text = ""
            if not text:
                text = str(page.get_text("text") or "").strip()
            if not text:
                continue
            parts.append(f"### Page {i + 1}\n\n{text}\n")
        md = "\n".join(parts).strip()
        return PdfMarkdown(markdown=md, page_count=page_count, pages_rendered=pages_rendered)
    finally:
        doc.close()


async def fetch_arxiv_paper_raw(
    url: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, object]:
    """Fetch an arXiv paper's metadata and rendered PDF Markdown parts."""
    arxiv_id = parse_arxiv_url(url)

    max_pages = get_int_env("ARXIV_MAX_PAGES", 0)
    if max_pages < 0:
        max_pages = 0

    async def _run(client: httpx.AsyncClient) -> dict[str, object]:
        meta = await _fetch_arxiv_metadata(arxiv_id, http_client=client)
        pdf_bytes = await _download_pdf_bytes(meta.pdf_url, http_client=client)
        pdf_md = _pdf_bytes_to_markdown_best_effort(pdf_bytes, max_pages=max_pages)
        full_text = pdf_md.markdown

        truncated = pdf_md.page_count > pdf_md.pages_rendered
        trunc_reason = f"page cap ({pdf_md.pages_rendered})" if truncated else None
        coverage = {
            "page_count": pdf_md.page_count,
            "pages_rendered": pdf_md.pages_rendered,
        }
        # Drop large intermediate buffers as soon as possible (best-effort).
        pdf_bytes = b""
        pdf_md = PdfMarkdown(markdown="", page_count=0, pages_rendered=0)

        metadata_lines = ["# arXiv Paper", "", "## Metadata"]
        if meta.title:
            metadata_lines.append(f"- Title: {meta.title}")
        if meta.authors:
            metadata_lines.append(f"- Authors: {', '.join(meta.authors)}")
        metadata_lines.append(f"- arXiv ID: {meta.arxiv_id}")
        metadata_lines.append(f"- URL (abs): {meta.abs_url}")
        metadata_lines.append(f"- URL (pdf): {meta.pdf_url}")
        if meta.primary_category:
            metadata_lines.append(f"- Primary category: {meta.primary_category}")
        if meta.categories:
            metadata_lines.append(f"- Categories: {', '.join(meta.categories)}")
        if meta.published:
            metadata_lines.append(f"- Published: {meta.published}")
        if meta.updated:
            metadata_lines.append(f"- Updated: {meta.updated}")

        body_lines = [
            *metadata_lines,
            "",
            "## Abstract",
            "",
            meta.abstract or "_No abstract available._",
            "",
            "## Full Text (PDF)",
            "",
            full_text or "_No PDF text extracted._",
        ]
        if truncated:
            reason = trunc_reason or "output limits"
            body_lines.extend(["", f"_Truncated due to {reason}._", "", f"Source: {url}", ""])

        return {
            "title": meta.title or f"arXiv {meta.arxiv_id}",
            "markdown": "\n".join(body_lines).strip() + "\n",
            "complete": not truncated,
            "coverage": dict(coverage),
        }

    if http_client is None:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            return await _run(client)
    return await _run(http_client)


async def fetch_arxiv_raw(target: ResolverTarget, ctx: FetchContext) -> RawDocument:
    """Acquire an arXiv paper via the existing converter."""
    return await bridge_text_producer(
        target,
        ctx,
        "arxiv",
        lambda url: fetch_arxiv_paper_raw(url, http_client=ctx.http_client),
    )


def match_arxiv(parsed: ParsedURL) -> ResolverTarget | None:
    host = (parsed.parts.hostname or "").lower()
    if host in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
        return ResolverTarget(url=parsed.url, kind="arxiv", values={"url": parsed.url})
    return None
