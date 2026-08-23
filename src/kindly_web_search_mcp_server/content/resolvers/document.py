"""Specialized document converter resolver for PDF, DOCX, PPTX, XLSX, EPUB, IPYNB, and CSV.

Converts multi-format documents into clean LLM-ready Markdown using:
- PyMuPDF (fitz) for PDF layout extraction
- Microsoft MarkItDown for Office and EPUB documents (.docx, .pptx, .xlsx, .epub)
- Native JSON parser for Jupyter Notebooks (.ipynb)
- CSV/TSV table generator
- Google Docs / Sheets URL rewriting to direct export formats
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import urllib.parse

from ..artifact import ContentArtifact, ContentError
from ..format_renderers import render_columnar_markdown, render_mhtml_markdown
from ..options import FetchOptions
from ..safe_fetch import SafeFetchError, safe_fetch_url
from ..sanitize import sanitize_markdown
from ..status_classifier import classify_markdown
from ...telemetry import record_content_error, record_content_resolution
from ...utils.url_canonicalize import canonicalize_url

LOGGER = logging.getLogger(__name__)


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


def is_document_url(url: str) -> bool:
    """Return True if the URL targets a known document or exportable format."""
    try:
        if _GOOGLE_DOC_RE.match(url) or _GOOGLE_SHEET_RE.match(url):
            return True
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.lower()
        for ext in DOC_EXTENSIONS:
            if path.endswith(ext):
                return True
        return False
    except Exception:
        return False


def get_doc_source_type(url: str, detected_type: str | None = None) -> str:
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


def _convert_pdf_to_markdown(pdf_bytes: bytes, source_url: str) -> str:
    """Extract clean Markdown from PDF bytes using PyMuPDF (fitz)."""
    import fitz  # PyMuPDF

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_count = len(doc)
    max_pages = int(os.environ.get("GENERIC_PDF_MAX_PAGES", "30").strip())
    pages_to_render = min(page_count, max_pages)

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


def _convert_ipynb_to_markdown(ipynb_text: str, source_url: str) -> str:
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
        elif cell_type == "raw":
            if source:
                lines.append(f"```\n{source}\n```\n")

    return "\n".join(lines).strip()


def _convert_csv_to_markdown(csv_text: str, source_url: str, delimiter: str = ",") -> str:
    """Format CSV or TSV text as a clean GitHub-Flavored Markdown table."""
    reader = csv.reader(io.StringIO(csv_text, newline=""), delimiter=delimiter)
    rows: list[list[str]] = []
    max_rows = 500

    for i, row in enumerate(reader):
        if i >= max_rows:
            break
        cleaned_row = [cell.strip().replace("\n", " ").replace("|", "\\|") for cell in row]
        if any(cleaned_row):
            rows.append(cleaned_row)

    if not rows:
        return f"# Data Table\n\nSource: {source_url}\n\n_Empty table_"

    col_count = max(len(r) for r in rows)
    normalized_rows = [r + [""] * (col_count - len(r)) for r in rows]

    header = normalized_rows[0]
    separator = ["---"] * col_count

    md_lines: list[str] = [
        f"# Data Table\nSource: {source_url}\n",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]

    for row in normalized_rows[1:]:
        md_lines.append("| " + " | ".join(row) + " |")

    if len(rows) >= max_rows:
        md_lines.append(f"\n_Note: Table truncated to first {max_rows} rows_")

    return "\n".join(md_lines).strip()


def _convert_office_with_markitdown(body: bytes, filename: str) -> str:
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


def _office_error_artifact(
    input_url: str,
    fetched_url: str,
    doc_type: str,
    content_type: str | None,
    error: DocumentConversionError,
) -> ContentArtifact:
    record_content_error(stage=f"doc_{doc_type}", url=input_url, error_type=error.code)
    return ContentArtifact(
        input_url=input_url,
        normalized_url=canonicalize_url(input_url),
        fetched_url=fetched_url,
        status="error",
        source_type=doc_type,
        fetch_backend=f"doc_converter_{doc_type}",
        content_type=content_type,
        markdown="",
        word_count=0,
        quality_score=0.0,
        error=ContentError(code=error.code, message=str(error), retryable=False),
    )


# ------------------------------------------------------------------
# Main Resolver Entrypoint
# ------------------------------------------------------------------


async def fetch_document_markdown(
    url: str,
    *,
    fetch_options: FetchOptions | None = None,
) -> ContentArtifact:
    """Fetch and convert document into clean LLM-ready Markdown."""
    options = fetch_options or FetchOptions()
    effective_url = rewrite_document_url(url)

    try:
        timeout_sec = options.stage_timeout_seconds or 30.0
        fetched = await safe_fetch_url(
            effective_url,
            timeout_seconds=timeout_sec,
            max_response_bytes=options.max_response_bytes,
        )
        doc_type = fetched.doc_type or get_doc_source_type(effective_url)
        markdown = ""

        if doc_type == "pdf":
            markdown = _convert_pdf_to_markdown(fetched.body, url)
        elif doc_type == "ipynb":
            text_content = fetched.text or fetched.body.decode("utf-8", errors="replace")
            markdown = _convert_ipynb_to_markdown(text_content, url)
        elif doc_type in ("csv", "tsv"):
            text_content = fetched.text or fetched.body.decode("utf-8", errors="replace")
            delimiter = "\t" if doc_type == "tsv" else ","
            markdown = _convert_csv_to_markdown(text_content, url, delimiter=delimiter)
        elif doc_type == "mhtml":
            markdown, _ = render_mhtml_markdown(fetched.body, url)
        elif doc_type in {"parquet", "arrow", "feather"}:
            try:
                markdown, _ = render_columnar_markdown(fetched.body, url, doc_type)
            except Exception as exc:
                return _office_error_artifact(
                    url,
                    fetched.fetched_url or url,
                    doc_type,
                    fetched.content_type,
                    DocumentConversionError("columnar_conversion_failed", str(exc)),
                )
        elif doc_type in ("docx", "pptx", "xlsx", "doc", "ppt", "xls", "epub"):
            filename = (
                os.path.basename(urllib.parse.urlparse(effective_url).path) or f"file.{doc_type}"
            )
            try:
                md_text = _convert_office_with_markitdown(fetched.body, filename)
            except DocumentConversionError as exc:
                return _office_error_artifact(
                    url,
                    fetched.fetched_url or url,
                    doc_type,
                    fetched.content_type,
                    exc,
                )
            markdown = f"# Document ({doc_type.upper()})\nSource: {url}\n\n{md_text}"
        else:
            # General fallback
            markdown = fetched.text or fetched.body.decode("utf-8", errors="replace")

        clean_text = sanitize_markdown(markdown)
        cls = classify_markdown(clean_text)
        word_count = len(clean_text.split())

        status = (
            "success" if clean_text.strip() and cls.status in ("success", "partial") else cls.status
        )

        record_content_resolution(
            stage=f"doc_{doc_type}",
            url=url,
            success=status == "success",
            size_bytes=len(clean_text.encode("utf-8")),
            word_count=word_count,
            extraction_method=f"doc_converter_{doc_type}",
        )

        return ContentArtifact(
            input_url=url,
            normalized_url=canonicalize_url(url),
            fetched_url=fetched.fetched_url or url,
            status=status,
            source_type=doc_type,
            fetch_backend=f"doc_converter_{doc_type}",
            content_type="text/markdown",
            markdown=clean_text,
            word_count=word_count,
            quality_score=1.0 if status == "success" else 0.5,
            error=None
            if status == "success"
            else ContentError(
                code=cls.reason or "doc_partial",
                message=cls.reason or "partial document extraction",
            ),
        )
    except SafeFetchError as exc:
        record_content_error(stage="document", url=url, error_type=exc.code)
        return ContentArtifact(
            input_url=url,
            normalized_url=canonicalize_url(url),
            fetched_url=url,
            status="error",
            source_type="document",
            fetch_backend="doc_converter_fetch",
            content_type=None,
            markdown="",
            word_count=0,
            quality_score=0.0,
            error=ContentError(code=exc.code, message=str(exc), retryable=False),
        )
    except Exception as exc:
        record_content_error(stage="document", url=url, error_type=type(exc).__name__)
        return ContentArtifact(
            input_url=url,
            normalized_url=canonicalize_url(url),
            fetched_url=url,
            status="error",
            source_type="document",
            fetch_backend="doc_converter_fetch",
            content_type=None,
            markdown="",
            word_count=0,
            quality_score=0.0,
            error=ContentError(code=type(exc).__name__, message=str(exc)[:500], retryable=True),
        )
