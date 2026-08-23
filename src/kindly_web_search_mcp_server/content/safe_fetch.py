from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

import httpx


class SafeFetchError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SafeFetchResult:
    input_url: str
    fetched_url: str
    content_type: str | None
    body: bytes
    text: str
    is_pdf: bool
    doc_type: str | None = None
    status_code: int = 200


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
    infos = await loop.getaddrinfo(hostname, None)
    return list(_ips_from_addrinfo(infos))  # type: ignore[arg-type]


async def _validate_resolved_ips(hostname: str) -> None:
    for ip in await _iter_resolved_ips(hostname):
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise SafeFetchError("private_ip_resolved", f"Resolved IP is not public: {ip}")


async def validate_public_url(url: str) -> None:
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
        for ext in _RAW_TEXT_EXTENSIONS:
            if path.endswith(ext):
                return True
        return False
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


def _is_pdf(content_type: str | None, fetched_url: str, body: bytes) -> bool:
    return _sniff_doc_type(content_type, fetched_url, body) == "pdf"


async def safe_fetch_url(
    url: str,
    *,
    timeout_seconds: float = 20.0,
    max_response_bytes: int = 15_000_000,
) -> SafeFetchResult:
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

        async with AsyncSession(impersonate="chrome124", follow_redirects=True) as session:
            resp = await session.get(url, headers=headers, timeout=int(timeout_seconds))
            if resp.status_code >= 400:
                raise SafeFetchError(
                    f"http_{resp.status_code}",
                    f"HTTP {resp.status_code} fetching {url}",
                )
            fetched_url = str(resp.url)
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
                lowered = (content_type or "").lower()
                is_allowed_text_type = any(t in lowered for t in _ALLOWED_TEXT_CONTENT_SUBSTRINGS)
                is_text_target = _is_raw_or_text_url(url) or _is_raw_or_text_url(fetched_url)
                if lowered and not is_allowed_text_type:
                    if (
                        "application/octet-stream" in lowered or "binary/octet-stream" in lowered
                    ) and (is_text_target or (body and b"\x00" not in body[:1024])):
                        pass
                    else:
                        raise SafeFetchError(
                            "unsupported_content_type",
                            f"Expected HTML/XML/markdown/plain/document but got content-type={content_type}",
                        )

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
            )
    except SafeFetchError:
        raise
    except Exception:
        # Fallback to standard httpx client
        pass

    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        async with client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            fetched_url = str(response.url)
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
                lowered = (content_type or "").lower()
                is_allowed_text_type = any(t in lowered for t in _ALLOWED_TEXT_CONTENT_SUBSTRINGS)
                is_text_target = _is_raw_or_text_url(url) or _is_raw_or_text_url(fetched_url)
                if lowered and not is_allowed_text_type:
                    if (
                        "application/octet-stream" in lowered or "binary/octet-stream" in lowered
                    ) and (is_text_target or (body and b"\x00" not in body[:1024])):
                        pass
                    else:
                        raise SafeFetchError(
                            "unsupported_content_type",
                            f"Expected HTML/XML/markdown/plain/document but got content-type={content_type}",
                        )
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
            )
