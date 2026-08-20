"""Specialized resolver for raw Markdown and plain text files (.md, .txt, .markdown, .text).

Avoids heavy extraction (Jina, Crawl4AI, BeautifulSoup, Camoufox, Firecrawl)
by performing a direct safe HTTP fetch and returning LLM-ready markdown or text.
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Literal

from ..artifact import ContentArtifact, ContentError
from ..options import FetchOptions
from ..safe_fetch import SafeFetchError, safe_fetch_url
from ..sanitize import sanitize_markdown
from ..status_classifier import classify_markdown
from ...telemetry import record_content_error, record_content_resolution
from ...utils.url_canonicalize import canonicalize_url

LOGGER = logging.getLogger(__name__)

RAW_TEXT_EXTENSIONS: set[str] = {
    ".md",
    ".markdown",
    ".mdown",
    ".mkdn",
    ".txt",
    ".text",
    ".rst",
    ".org",
    ".log",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".csv",
    ".tsv",
    ".py",
    ".ts",
    ".js",
    ".rs",
    ".go",
    ".c",
    ".cpp",
    ".h",
    ".java",
    ".sh",
}
RAW_TEXT_HOSTS: set[str] = {"raw.githubusercontent.com", "gist.githubusercontent.com"}
NON_RAW_TEXT_EXTENSIONS: set[str] = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".doc",
    ".ppt",
    ".xls",
    ".epub",
    ".ipynb",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".bz2",
    ".xz",
    ".exe",
    ".bin",
    ".dylib",
    ".so",
    ".dll",
    ".mp3",
    ".mp4",
    ".wav",
    ".mov",
    ".avi",
}


def is_raw_text_url(url: str) -> bool:
    """Recognize if a URL points to a raw markdown/text file or raw text host.

    Matches URLs whose path extension is a recognized raw text extension,
    whose host is raw.githubusercontent.com / gist.githubusercontent.com,
    or whose path contains /raw/ on GitHub / GitLab, excluding binary
    and document formats like .pdf, .docx, .ipynb, etc.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        for non_ext in NON_RAW_TEXT_EXTENSIONS:
            if path.endswith(non_ext):
                return False
        for ext in RAW_TEXT_EXTENSIONS:
            if path.endswith(ext):
                return True
        if host in RAW_TEXT_HOSTS:
            return True
        if ("github.com" in host or "gitlab.com" in host) and "/raw/" in path:
            return True
        return False
    except Exception:
        return False


def get_raw_text_type(url: str) -> tuple[Literal["text/markdown", "text/plain"], str]:
    """Return (content_type, source_type) based on URL extension or host."""
    try:
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.lower()
        if path.endswith((".md", ".markdown", ".mdown", ".mkdn")):
            return "text/markdown", "markdown_file"
        if path.endswith((".txt", ".text", ".rst", ".org", ".log")):
            return "text/plain", "text_file"
    except Exception:
        pass
    return "text/markdown", "raw_text"


async def fetch_raw_text_markdown(
    url: str,
    *,
    fetch_options: FetchOptions | None = None,
) -> ContentArtifact:
    """Fetch raw markdown or text content directly without heavy extraction.

    Performs a safe HTTP GET, decodes response text, sanitizes lightly,
    and returns a ContentArtifact.
    """
    options = fetch_options or FetchOptions()
    content_type, source_type = get_raw_text_type(url)

    try:
        timeout_sec = options.stage_timeout_seconds or 20.0
        fetched = await safe_fetch_url(url, timeout_seconds=timeout_sec)
        raw_text = fetched.body.decode("utf-8", errors="replace")
        # Strip null characters if any
        raw_text = raw_text.replace("\x00", "")
        clean_text = sanitize_markdown(raw_text)

        cls = classify_markdown(clean_text)
        word_count = len(clean_text.split())

        status = (
            "success" if clean_text.strip() and cls.status in ("success", "partial") else cls.status
        )

        record_content_resolution(
            stage="raw_text",
            url=url,
            success=status == "success",
            size_bytes=len(clean_text.encode("utf-8")),
            word_count=word_count,
            extraction_method="safe_fetch_raw",
        )

        return ContentArtifact(
            input_url=url,
            normalized_url=canonicalize_url(url),
            fetched_url=fetched.fetched_url or url,
            status=status,
            source_type=source_type,
            fetch_backend="raw_text_fetch",
            content_type=fetched.content_type or content_type,
            markdown=clean_text,
            word_count=word_count,
            quality_score=1.0 if status == "success" else 0.4,
            error=None
            if status == "success"
            else ContentError(
                code=cls.reason or "raw_text_partial",
                message=cls.reason or "partial raw text",
            ),
        )
    except SafeFetchError as exc:
        record_content_error(stage="raw_text", url=url, error_type=exc.code)
        return ContentArtifact(
            input_url=url,
            normalized_url=canonicalize_url(url),
            fetched_url=url,
            status="error",
            source_type=source_type,
            fetch_backend="raw_text_fetch",
            content_type=None,
            markdown="",
            word_count=0,
            quality_score=0.0,
            error=ContentError(code=exc.code, message=str(exc), retryable=False),
        )
    except Exception as exc:
        record_content_error(stage="raw_text", url=url, error_type=type(exc).__name__)
        return ContentArtifact(
            input_url=url,
            normalized_url=canonicalize_url(url),
            fetched_url=url,
            status="error",
            source_type=source_type,
            fetch_backend="raw_text_fetch",
            content_type=None,
            markdown="",
            word_count=0,
            quality_score=0.0,
            error=ContentError(code=type(exc).__name__, message=str(exc)[:500], retryable=True),
        )
