"""HTTP transport for content acquisition, with two deliberate layers.

* **Borrowed-context transport** (:func:`request_with_redirect_validation`,
  :func:`fetch_json`, :func:`fetch_text`) — resolvers never own an
  ``httpx.AsyncClient``; they borrow the client and absolute deadline from
  :class:`FetchContext` and re-validate every redirect hop. Failures raise
  :class:`~kindly_web_search_mcp_server.content.models.AcquisitionError`.
* **Standalone SSRF-guarded fetch** (:func:`safe_fetch_url`) — callers
  outside the pipeline that must create their own client. It browser-
  impersonates via curl_cffi when available, falls back to httpx, resolves
  DNS before every hop, and raises :class:`SafeFetchError`.

No classification, sanitizing passes, scoring, or artifact construction here.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from .models import AcquisitionError, FetchContext

LOGGER = logging.getLogger(__name__)

_MAX_REDIRECTS = 5


# ---------------------------------------------------------------------------
# Public-host guards
# ---------------------------------------------------------------------------


def _is_public_host(host: str) -> bool:
    """Return True when ``host`` is a public hostname by pattern (no DNS)."""
    if not host:
        return False
    lowered = host.lower()
    if lowered in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return False
    if lowered.endswith(".localhost") or lowered.endswith(".local"):
        return False
    return not lowered.endswith(".internal")


def _validate_public_hostname(url: str) -> None:
    """Reject non-http(s) schemes and private-looking hosts (no DNS lookup)."""
    parsed = httpx.URL(url)
    host = (parsed.host or "").lower()
    if parsed.scheme not in {"http", "https"}:
        raise AcquisitionError(
            code="unsafe_scheme",
            message=f"Unsupported scheme: {parsed.scheme}",
            status="blocked",
            retryable=False,
        )
    if not host:
        raise AcquisitionError(
            code="missing_host",
            message="Target URL is missing a hostname",
            status="blocked",
            retryable=False,
        )
    if not _is_public_host(host):
        raise AcquisitionError(
            code="private_host",
            message=f"Private host refused: {host}",
            status="blocked",
            retryable=False,
        )


def _coerce_redirect(target: str, current_url: str) -> str:
    return urljoin(current_url, target)


# ---------------------------------------------------------------------------
# Borrowed-context transport (FetchContext client, AcquisitionError failures)
# ---------------------------------------------------------------------------


async def request_with_redirect_validation(
    ctx: FetchContext,
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    follow_redirects: bool = True,
    max_redirects: int = _MAX_REDIRECTS,
) -> httpx.Response:
    """Issue a single request through the borrowed client with manual redirects.

    The borrowed :class:`FetchContext` client is expected to disable its own
    redirect following; this helper re-implements the hop with one public-host
    validation per redirect.

    On deadline exhaust, :meth:`FetchContext.timeout` raises ``TimeoutError``
    directly. AcquisitionError wrapping is the producer's responsibility.
    """

    base_headers = {
        "User-Agent": "kindly-web-search/1.0",
        "Accept": "*/*",
    }
    merged_headers = {**base_headers, **(headers or {})}
    timeout = ctx.timeout()
    current = url
    redirects = 0
    while True:
        _validate_public_hostname(current)
        # httpx raises TimeoutError when the borrowed timeout elapses.
        kwargs: dict[str, Any] = {
            "headers": merged_headers,
            "params": params,
            "timeout": timeout,
            "follow_redirects": False,
        }
        if json_body is not None:
            kwargs["json"] = json_body
        response = await ctx.http_client.request(method, current, **kwargs)
        if 300 <= response.status_code < 400 and "location" in response.headers:
            if not follow_redirects:
                return response
            redirects += 1
            if redirects > max_redirects:
                raise AcquisitionError(
                    code="redirect_loop",
                    message=f"Too many redirects ({redirects}) for {url}",
                    status="error",
                    retryable=False,
                )
            current = _coerce_redirect(response.headers["location"], current)
            continue
        return response


def raise_for_status(response: httpx.Response, *, what: str) -> None:
    """Translate an HTTP error into a stable ``AcquisitionError``."""

    if response.status_code >= 400:
        raise AcquisitionError(
            code=f"http_{response.status_code}",
            message=f"{what} returned HTTP {response.status_code}",
            status="error",
            http_status=response.status_code,
            retryable=response.status_code >= 500,
        )


def bytes_with_cap(response: httpx.Response, *, cap: int, what: str) -> bytes:
    """Read the entire response body while enforcing the borrowed size limit."""

    content_length = response.headers.get("content-length")
    if content_length:
        try:
            declared = int(content_length)
        except ValueError:
            declared = 0
        if declared > cap:
            raise AcquisitionError(
                code="response_too_large",
                message=f"{what} declared {declared} bytes exceeding cap {cap}",
                status="blocked",
                retryable=False,
            )
    body = response.content
    if len(body) > cap:
        raise AcquisitionError(
            code="response_too_large",
            message=f"{what} body {len(body)} bytes exceeds cap {cap}",
            status="blocked",
            retryable=False,
        )
    return body


async def fetch_json(
    ctx: FetchContext,
    url: str,
    *,
    what: str,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | list[Any]:
    """GET ``url`` through the borrowed client and decode a JSON body."""
    response = await request_with_redirect_validation(
        ctx, url, headers=headers, params=params, follow_redirects=True
    )
    raise_for_status(response, what=what)
    try:
        return response.json()
    except Exception as exc:
        raise AcquisitionError(
            code="json_parse_error", message=f"{what}: {exc}", status="error", retryable=False
        ) from exc


async def fetch_text(
    ctx: FetchContext,
    url: str,
    *,
    what: str,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> str:
    """GET ``url`` through the borrowed client and return the decoded text."""
    response = await request_with_redirect_validation(
        ctx, url, headers=headers, params=params, follow_redirects=True
    )
    raise_for_status(response, what=what)
    return response.text


# ---------------------------------------------------------------------------
# Standalone SSRF-guarded fetch (own client, SafeFetchError failures)
# ---------------------------------------------------------------------------


class SafeFetchError(RuntimeError):
    """A standalone fetch failed before producing usable content."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SafeFetchResult:
    """One completed standalone fetch with transport evidence attached."""

    input_url: str
    fetched_url: str
    content_type: str | None
    body: bytes
    text: str
    is_pdf: bool = False
    doc_type: str | None = None
    status_code: int = 200
    response_headers: dict[str, str] | None = None


def _host_is_local(host: str) -> bool:
    lowered = host.lower()
    return lowered in {"localhost", "127.0.0.1", "::1"} or lowered.endswith(".localhost")


def _validate_scheme(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise SafeFetchError(
            "unsupported_scheme",
            f"Unsupported URL scheme: {parsed.scheme or 'missing'}",
        )


def _validate_host_public(host: str) -> None:
    if _host_is_local(host):
        raise SafeFetchError("private_host", "Localhost/private hosts are not allowed")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise SafeFetchError("private_host", "Private or local network targets are not allowed")


def _ips_from_addrinfo(infos: Iterable[tuple]) -> Iterable[ipaddress._BaseAddress]:
    for entry in infos:
        sockaddr = entry[4]
        if not sockaddr:
            continue
        ip_raw = sockaddr[0]
        try:
            yield ipaddress.ip_address(ip_raw)
        except ValueError:
            continue


async def _iter_resolved_ips(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(hostname, None)
    except OSError as exc:
        raise SafeFetchError(
            "dns_resolution_failed", f"DNS resolution failed for '{hostname}': {exc}"
        ) from exc
    return list(_ips_from_addrinfo(infos))  # type: ignore[arg-type]


async def _validate_resolved_ips(hostname: str) -> None:
    for ip in await _iter_resolved_ips(hostname):
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise SafeFetchError("private_ip_resolved", f"Resolved IP is not public: {ip}")


async def validate_public_url(url: str) -> None:
    """Fully validate a URL: scheme, hostname, and every resolved IP address."""
    _validate_scheme(url)
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip()
    if not host:
        raise SafeFetchError("invalid_url", "URL host is missing")
    _validate_host_public(host)
    await _validate_resolved_ips(host)


_ALLOWED_TEXT_CONTENT_SUBSTRINGS: tuple[str, ...] = (
    "text/",
    "application/xhtml+xml",
    "application/xml",
    "application/json",
    "application/jsonl",
    "application/ndjson",
    "application/x-ndjson",
    "application/rss+xml",
    "application/atom+xml",
    "application/csv",
    "application/x-yaml",
    "application/yaml",
    "text/yaml",
    "application/toml",
    "text/x-toml",
    "application/javascript",
    "application/x-javascript",
    "application/rtf",
    "text/rtf",
    "text/vtt",
    "application/vtt",
    "application/x-subrip",
    "text/srt",
    "application/srt",
    "image/svg+xml",
    "application/vnd.apache.parquet",
    "application/x-parquet",
    "application/parquet",
    "application/vnd.apache.arrow.file",
    "application/vnd.apache.arrow.stream",
    "application/vnd.apache.feather",
)

_RAW_TEXT_EXTENSIONS: set[str] = {
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
    ".jsonl",
    ".ndjson",
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


def _is_raw_or_text_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host in {"raw.githubusercontent.com", "gist.githubusercontent.com"}:
            return True
        if ("github.com" in host or "gitlab.com" in host) and "/raw/" in parsed.path.lower():
            return True
        path = parsed.path.lower()
        return any(path.endswith(ext) for ext in _RAW_TEXT_EXTENSIONS)
    except Exception:
        return False


def _sniff_doc_type(content_type: str | None, fetched_url: str, body: bytes) -> str | None:
    """Sniff document type from content-type, URL path, or magic bytes."""
    ctype = (content_type or "").lower()
    path = urlparse(fetched_url).path.lower()

    if (
        "multipart/related" in ctype
        or "message/rfc822" in ctype
        or path.endswith((".mht", ".mhtml"))
    ):
        return "mhtml"
    if "parquet" in ctype or path.endswith(".parquet"):
        return "parquet"
    if "arrow" in ctype or path.endswith(".arrow"):
        return "arrow"
    if "feather" in ctype or path.endswith(".feather"):
        return "feather"
    if "application/pdf" in ctype or path.endswith(".pdf") or body.startswith(b"%PDF-"):
        return "pdf"
    if (
        "wordprocessingml" in ctype
        or path.endswith(".docx")
        or (path.endswith(".doc") and not path.endswith(".dockerfile"))
    ):
        return "docx"
    if "presentationml" in ctype or path.endswith(".pptx") or path.endswith(".ppt"):
        return "pptx"
    if (
        "spreadsheetml" in ctype
        or "excel" in ctype
        or path.endswith(".xlsx")
        or path.endswith(".xls")
    ):
        return "xlsx"
    if "epub" in ctype or path.endswith(".epub"):
        return "epub"
    if path.endswith(".ipynb"):
        return "ipynb"
    if "text/csv" in ctype or path.endswith(".csv"):
        return "csv"
    if "tab-separated" in ctype or path.endswith(".tsv"):
        return "tsv"
    return None


def _content_type_allowed(
    content_type: str | None, body: bytes, fetched_url: str, url: str
) -> None:
    """Raise when a non-document response carries an unsupported content type."""
    lowered = (content_type or "").lower()
    is_allowed_text_type = any(t in lowered for t in _ALLOWED_TEXT_CONTENT_SUBSTRINGS)
    is_text_target = _is_raw_or_text_url(url) or _is_raw_or_text_url(fetched_url)
    if lowered and not is_allowed_text_type:
        if ("application/octet-stream" in lowered or "binary/octet-stream" in lowered) and (
            is_text_target or (body and b"\x00" not in body[:1024])
        ):
            return
        raise SafeFetchError(
            "unsupported_content_type",
            f"Expected HTML/XML/markdown/plain/document but got content-type={content_type}",
        )


async def safe_fetch_url(
    url: str,
    *,
    timeout_seconds: float = 20.0,
    max_response_bytes: int = 15_000_000,
) -> SafeFetchResult:
    """Fetch ``url`` with DNS-level SSRF guards, size caps, and doc sniffing.

    Tries a browser-impersonating curl_cffi session first and falls back to
    a plain httpx client when impersonation is unavailable or fails.
    """
    await validate_public_url(url)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/markdown,text/plain,application/pdf,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    # Try curl_cffi with browser impersonation (JA3/JA4 TLS fingerprinting)
    try:
        from curl_cffi.requests import AsyncSession  # type: ignore[import-not-found,import-untyped]

        current_url = url
        redirect_count = 0
        async with AsyncSession(impersonate="chrome124", follow_redirects=False) as session:
            while True:
                resp = await session.get(current_url, headers=headers, timeout=int(timeout_seconds))
                if 300 <= resp.status_code < 400 and "location" in resp.headers:
                    redirect_count += 1
                    if redirect_count > _MAX_REDIRECTS:
                        raise SafeFetchError(
                            "too_many_redirects", f"Too many redirects ({redirect_count}) for {url}"
                        )
                    location = resp.headers["location"]
                    current_url = urljoin(current_url, location)
                    await validate_public_url(current_url)
                    continue
                break

            if resp.status_code >= 400:
                raise SafeFetchError(
                    f"http_{resp.status_code}",
                    f"HTTP {resp.status_code} fetching {url}",
                )
            fetched_url = str(resp.url) or current_url
            await validate_public_url(fetched_url)

            body = resp.content
            if len(body) > max_response_bytes:
                raise SafeFetchError(
                    "response_too_large",
                    f"Response exceeds max allowed size: {len(body)} bytes",
                )

            content_type = resp.headers.get("content-type")
            doc_type = _sniff_doc_type(content_type, fetched_url, body)
            is_pdf = doc_type == "pdf"
            if not doc_type:
                _content_type_allowed(content_type, body, fetched_url, url)

            text = ""
            if not is_pdf and doc_type not in {"docx", "pptx", "xlsx", "epub"}:
                encoding = resp.encoding or "utf-8"
                text = body.decode(encoding, errors="replace")
            return SafeFetchResult(
                input_url=url,
                fetched_url=fetched_url,
                content_type=content_type,
                body=body,
                text=text,
                is_pdf=is_pdf,
                doc_type=doc_type,
                status_code=resp.status_code,
                response_headers=dict(resp.headers),
            )
    except SafeFetchError:
        raise
    except Exception:
        # Fallback to standard httpx client
        pass

    timeout = httpx.Timeout(timeout_seconds)
    current_url = url
    redirect_count = 0
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
        while True:
            async with client.stream("GET", current_url, headers=headers) as response:
                if 300 <= response.status_code < 400 and "location" in response.headers:
                    redirect_count += 1
                    if redirect_count > _MAX_REDIRECTS:
                        raise SafeFetchError(
                            "too_many_redirects", f"Too many redirects ({redirect_count}) for {url}"
                        )
                    location = response.headers["location"]
                    current_url = urljoin(current_url, location)
                    await validate_public_url(current_url)
                    continue

                response.raise_for_status()
                fetched_url = str(response.url) or current_url
                await validate_public_url(fetched_url)

                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        declared = int(content_length)
                    except ValueError:
                        declared = 0
                    if declared > max_response_bytes:
                        raise SafeFetchError(
                            "response_too_large",
                            f"Response exceeds max allowed size: {declared} bytes",
                        )

                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_response_bytes:
                        raise SafeFetchError(
                            "response_too_large",
                            f"Streamed response exceeds max allowed size: {total} bytes",
                        )
                    chunks.append(chunk)

                body = b"".join(chunks)
                content_type = response.headers.get("content-type")
                doc_type = _sniff_doc_type(content_type, fetched_url, body)
                is_pdf = doc_type == "pdf"
                if not doc_type:
                    _content_type_allowed(content_type, body, fetched_url, url)
                text = ""
                if not is_pdf and doc_type not in {"docx", "pptx", "xlsx", "epub"}:
                    encoding = response.encoding or "utf-8"
                    text = body.decode(encoding, errors="replace")
                return SafeFetchResult(
                    input_url=url,
                    fetched_url=fetched_url,
                    content_type=content_type,
                    body=body,
                    text=text,
                    is_pdf=is_pdf,
                    doc_type=doc_type,
                    status_code=response.status_code,
                    response_headers=dict(response.headers),
                )


# ---------------------------------------------------------------------------
# HTML tooling (BeautifulSoup + markdownify)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# llms.txt probe
# ---------------------------------------------------------------------------


__all__ = [
    # standalone SSRF-guarded fetch
    "SafeFetchError",
    "SafeFetchResult",
    "bytes_with_cap",
    "fetch_json",
    "fetch_text",
    "raise_for_status",
    # borrowed-context transport
    "request_with_redirect_validation",
    "safe_fetch_url",
    "validate_public_url",
]
