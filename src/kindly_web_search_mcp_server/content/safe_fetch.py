from __future__ import annotations

import ipaddress
import socket
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


def _host_is_local(host: str) -> bool:
    lowered = host.lower()
    return lowered in {"localhost", "127.0.0.1", "::1"} or lowered.endswith(
        ".localhost"
    )


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
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
    ):
        raise SafeFetchError(
            "private_host", "Private or local network targets are not allowed"
        )


def _iter_resolved_ips(hostname: str) -> Iterable[ipaddress._BaseAddress]:
    infos = socket.getaddrinfo(hostname, None)
    for entry in infos:
        sockaddr = entry[4]
        if not sockaddr:
            continue
        ip_raw = sockaddr[0]
        try:
            yield ipaddress.ip_address(ip_raw)
        except ValueError:
            continue


def _validate_resolved_ips(hostname: str) -> None:
    for ip in _iter_resolved_ips(hostname):
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            raise SafeFetchError(
                "private_ip_resolved", f"Resolved IP is not public: {ip}"
            )


def validate_public_url(url: str) -> None:
    _validate_scheme(url)
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip()
    if not host:
        raise SafeFetchError("invalid_url", "URL host is missing")
    _validate_host_public(host)
    _validate_resolved_ips(host)


def _is_pdf(content_type: str | None, fetched_url: str, body: bytes) -> bool:
    ctype = (content_type or "").lower()
    if "application/pdf" in ctype:
        return True
    if urlparse(fetched_url).path.lower().endswith(".pdf"):
        return True
    return body.startswith(b"%PDF-")


async def safe_fetch_url(
    url: str,
    *,
    timeout_seconds: float = 20.0,
    max_response_bytes: int = 8_000_000,
) -> SafeFetchResult:
    validate_public_url(url)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    timeout = httpx.Timeout(timeout_seconds)

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        async with client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            fetched_url = str(response.url)
            validate_public_url(fetched_url)

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
            is_pdf = _is_pdf(content_type, fetched_url, body)
            text = ""
            if not is_pdf:
                encoding = response.encoding or "utf-8"
                text = body.decode(encoding, errors="replace")

            return SafeFetchResult(
                input_url=url,
                fetched_url=fetched_url,
                content_type=content_type,
                body=body,
                text=text,
                is_pdf=is_pdf,
            )
