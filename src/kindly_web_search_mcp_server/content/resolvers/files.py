"""Specialized document converter resolver for PDF, DOCX, PPTX, XLSX, EPUB, IPYNB, and CSV.

Converts multi-format documents into clean LLM-ready Markdown using:
- PyMuPDF (fitz) for PDF layout extraction
- Microsoft MarkItDown for Office and EPUB documents (.docx, .pptx, .xlsx, .epub)
- Native JSON parser for Jupyter Notebooks (.ipynb)
- CSV/TSV table generator
- Google Docs / Sheets URL rewriting to direct export formats
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import urllib.parse

from ..http_utils import SafeFetchError, safe_fetch_url
from ..machine_readable import render_columnar_markdown, render_mhtml_markdown, render_typed_content
from ..models import (
    AcquisitionError,
    Diagnostic,
    FetchContext,
    ParsedURL,
    RawDocument,
    ResolverTarget,
    TextDocument,
)

LOGGER = logging.getLogger(__name__)

_MAX_PDF_PAGES = int(os.environ.get("GENERIC_PDF_MAX_PAGES", "30").strip())


class DocumentConversionError(RuntimeError):
    """A recognized document could not be converted safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


DOC_EXTENSIONS: set[str] = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".doc",
    ".ppt",
    ".xls",
    ".mht",
    ".parquet",
    ".arrow",
    ".feather",
    ".mhtml",
    ".epub",
    ".ipynb",
    ".csv",
    ".tsv",
}

_GOOGLE_DOC_RE = re.compile(r"^https?://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)(?:/.*)?$")
_GOOGLE_SHEET_RE = re.compile(
    r"^https?://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)(?:/.*)?$"
)


def rewrite_document_url(url: str) -> str:
    """Rewrite Google Docs / Google Sheets URLs to direct export endpoints."""
    m_doc = _GOOGLE_DOC_RE.match(url)
    if m_doc:
        doc_id = m_doc.group(1)
        return f"https://docs.google.com/document/d/{doc_id}/export?format=txt"

    m_sheet = _GOOGLE_SHEET_RE.match(url)
    if m_sheet:
        sheet_id = m_sheet.group(1)
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"

    return url


def detect_doc_type(url: str, detected_type: str | None = None) -> str:
    """Derive canonical source_type from URL or detected document type."""
    if detected_type:
        return detected_type
    path = urllib.parse.urlparse(url).path.lower()
    for ext in DOC_EXTENSIONS:
        if path.endswith(ext):
            return ext.lstrip(".")
    if "docs.google.com/document" in url:
        return "docx"
    if "docs.google.com/spreadsheets" in url:
        return "csv"
    return "document"


# ------------------------------------------------------------------
# Document Converters
# ------------------------------------------------------------------


def convert_pdf_to_markdown(pdf_bytes: bytes, source_url: str) -> str:
    """Extract clean Markdown from PDF bytes using PyMuPDF."""
    try:
        import pymupdf as fitz  # PyMuPDF >= 1.24
    except ImportError:
        import fitz  # legacy alias on older installs

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_count = len(doc)
    pages_to_render = min(page_count, _MAX_PDF_PAGES)

    md_lines: list[str] = [
        "# PDF Document",
        f"Source: {source_url}",
        f"_Pages: {pages_to_render}/{page_count}_",
        "",
    ]

    for page_idx in range(pages_to_render):
        page = doc[page_idx]
        text = str(page.get_text("text")).strip()
        if text:
            md_lines.append(f"## Page {page_idx + 1}")
            md_lines.append(text)
            md_lines.append("")

    if pages_to_render < page_count:
        md_lines.append(f"_Note: Truncated after {pages_to_render} pages of {page_count}_")

    return "\n".join(md_lines).strip()


def convert_ipynb_to_markdown(ipynb_text: str, source_url: str) -> str:
    """Parse Jupyter Notebook JSON into clean structured Markdown."""
    try:
        nb_data = json.loads(ipynb_text)
    except Exception as exc:
        return f"# Jupyter Notebook\n\nFailed to parse JSON: {exc}\n\n```json\n{ipynb_text[:5000]}\n```"

    cells = nb_data.get("cells", [])
    metadata = nb_data.get("metadata", {})
    language_info = metadata.get("language_info", {})
    lang = language_info.get("name", "python")
    nb_title = metadata.get("title") or "Jupyter Notebook"

    lines: list[str] = [f"# {nb_title}", f"Source: {source_url}", ""]

    for cell in cells:
        cell_type = cell.get("cell_type", "code")
        source = cell.get("source", "")
        if isinstance(source, list):
            source = "".join(source)
        source = source.strip()

        if cell_type == "markdown":
            if source:
                lines.append(source)
                lines.append("")
        elif cell_type == "code":
            exec_count = cell.get("execution_count")
            exec_header = f"[{exec_count or ' '}]:" if exec_count is not None else ""
            lines.append(f"### In {exec_header}")
            lines.append(f"```{lang}\n{source}\n```")

            outputs = cell.get("outputs", [])
            for out in outputs:
                out_type = out.get("output_type")
                if out_type == "stream":
                    text = out.get("text", "")
                    if isinstance(text, list):
                        text = "".join(text)
                    if text.strip():
                        lines.append(f"**Output (stream):**\n```\n{text.strip()}\n```")
                elif out_type in ("execute_result", "display_data"):
                    data = out.get("data", {})
                    if "text/markdown" in data:
                        md_out = data["text/markdown"]
                        if isinstance(md_out, list):
                            md_out = "".join(md_out)
                        lines.append(md_out.strip())
                    elif "text/plain" in data:
                        plain_out = data["text/plain"]
                        if isinstance(plain_out, list):
                            plain_out = "".join(plain_out)
                        lines.append(f"**Output:**\n```\n{plain_out.strip()}\n```")
                elif out_type == "error":
                    ename = out.get("ename", "Error")
                    evalue = out.get("evalue", "")
                    lines.append(f"**Error:** `{ename}: {evalue}`")
            lines.append("")
        elif cell_type == "raw" and source:
            lines.append(f"```\n{source}\n```\n")

    return "\n".join(lines).strip()


def convert_office_with_markitdown(body: bytes, filename: str) -> str:
    """Convert Office / EPUB files using the explicit MarkItDown extras."""
    suffix = os.path.splitext(filename)[1].lower()
    if suffix in {".docx", ".pptx", ".xlsx", ".epub"} and not body.startswith(b"PK"):
        raise DocumentConversionError(
            "invalid_office_container",
            f"{filename} is not a valid ZIP-based Office container",
        )
    if suffix in {".doc", ".ppt", ".xls"} and not body.startswith(
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    ):
        raise DocumentConversionError(
            "invalid_office_container",
            f"{filename} is not a valid legacy Office container",
        )
    try:
        from markitdown import MarkItDown
    except ImportError as exc:  # pragma: no cover - clean-install dependency gate
        raise DocumentConversionError(
            "office_dependency_missing",
            "markitdown[docx,pptx,xlsx,xls] is required for Office conversion",
        ) from exc

    try:
        md_engine = MarkItDown()
        with io.BytesIO(body) as stream:
            result = md_engine.convert_stream(
                stream,
                file_extension=os.path.splitext(filename)[1],
            )
    except Exception as exc:
        LOGGER.debug("MarkItDown conversion failed for %s: %s", filename, exc)
        raise DocumentConversionError(
            "office_conversion_failed",
            f"MarkItDown failed for {filename}: {exc}",
        ) from exc
    if not result or not result.text_content or not result.text_content.strip():
        raise DocumentConversionError(
            "office_empty_output",
            f"MarkItDown returned no text for {filename}",
        )
    return result.text_content.strip()


def match_document(parsed: ParsedURL) -> ResolverTarget | None:
    """Match PDF / Office / notebook / Google-Docs URLs."""

    host = (parsed.parts.hostname or "").lower()
    path = (parsed.parts.path or "").lower()
    if "docs.google.com" in host and ("/document/" in path or "/spreadsheets/" in path):
        return ResolverTarget(url=parsed.url, kind="document", values={"url": parsed.url})
    if path.endswith(tuple(DOC_EXTENSIONS)):
        return ResolverTarget(url=parsed.url, kind="document", values={"url": parsed.url})
    return None


async def fetch_document_raw(target: ResolverTarget, ctx: FetchContext) -> RawDocument:
    """Convert a matched document URL into a RawDocument candidate."""
    url = target.values.get("url") or target.url
    effective_url = rewrite_document_url(url)
    try:
        fetched = await safe_fetch_url(
            effective_url,
            timeout_seconds=ctx.timeout(30.0),
            max_response_bytes=ctx.max_response_bytes,
        )
    except SafeFetchError as exc:
        raise AcquisitionError(code=exc.code, message=str(exc)) from exc
    doc_type = fetched.doc_type or detect_doc_type(effective_url)
    diagnostics: list[Diagnostic] = []
    links: tuple[dict[str, object], ...] = ()
    metadata: dict[str, object] = {}
    markdown = ""
    try:
        if doc_type == "pdf":
            markdown = convert_pdf_to_markdown(fetched.body, url)
        elif doc_type == "ipynb":
            text_content = fetched.text or fetched.body.decode("utf-8", errors="replace")
            markdown = convert_ipynb_to_markdown(text_content, url)
        elif doc_type in ("csv", "tsv"):
            text_content = fetched.text or fetched.body.decode("utf-8", errors="replace")
            markdown, meta, typed_links = render_typed_content(doc_type, text_content, url)
            metadata.update(meta)
            links = tuple(typed_links)
        elif doc_type == "mhtml":
            markdown, meta = render_mhtml_markdown(fetched.body, url)
            metadata.update(meta)
        elif doc_type in {"parquet", "arrow", "feather"}:
            markdown, meta = render_columnar_markdown(fetched.body, url, doc_type)
            metadata.update(meta)
            diagnostics.append(
                Diagnostic(
                    code="bounded_sample",
                    message="Columnar source rendered as schema plus bounded sample.",
                    source="document",
                    phase="acquire",
                )
            )
        elif doc_type in ("docx", "pptx", "xlsx", "doc", "ppt", "xls", "epub"):
            filename = (
                os.path.basename(urllib.parse.urlparse(effective_url).path) or f"file.{doc_type}"
            )
            md_text = convert_office_with_markitdown(fetched.body, filename)
            markdown = f"# Document ({doc_type.upper()})\nSource: {url}\n\n{md_text}"
        else:
            markdown = fetched.text or fetched.body.decode("utf-8", errors="replace")
    except DocumentConversionError as exc:
        raise AcquisitionError(code=exc.code, message=str(exc)) from exc
    if not markdown.strip():
        raise AcquisitionError(
            code="empty_document", message="Document conversion returned no text."
        )
    return RawDocument(
        input_url=url,
        fetched_url=fetched.fetched_url or url,
        source_type=doc_type,
        fetch_backend=f"doc_converter_{doc_type}",
        body=TextDocument(text=markdown, format="markdown"),
        content_type=fetched.content_type,
        title=None,
        metadata=dict(metadata) or {},
        links=links,
        diagnostics=tuple(diagnostics),
        http_status=fetched.status_code,
        response_headers=dict(fetched.response_headers or {}),
        complete=True,
        scope="full",
        bytes_downloaded=len(fetched.body),
        redirect_count=None,
    )
