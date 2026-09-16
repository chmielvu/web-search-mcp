"""Crates.io specialized resolver.

The crate metadata and README tarball route through the shared package
renderer in :mod:`kindly_web_search_mcp_server.content.packages`.
"""

from __future__ import annotations

import re
import tarfile
import urllib.parse
import zipfile
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from ..documents import _as_dict, build_package_document
from ..http_utils import (
    bytes_with_cap,
    fetch_json,
    raise_for_status,
    request_with_redirect_validation,
)
from ..models import (
    AcquisitionError,
    Diagnostic,
    FetchContext,
    PackageDocument,
    ParsedURL,
    RawDocument,
    ResolverTarget,
)


class CratesError(RuntimeError):
    pass


@dataclass(frozen=True)
class CratesTarget:
    crate_name: str


_CRATES_HOSTS = frozenset({"crates.io", "www.crates.io"})
_CRATES_PATH_RE = re.compile(r"^/crates/([^/]+)")


def parse_crates_url(url: str) -> CratesTarget | None:
    """Parse a Crates.io URL."""

    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return None
    if parsed.hostname is None or parsed.hostname.lower() not in _CRATES_HOSTS:
        return None
    match = _CRATES_PATH_RE.match(parsed.path or "")
    if not match:
        return None
    name = match.group(1).strip()
    if not name:
        return None
    return CratesTarget(crate_name=name)


def match_crates(parsed: ParsedURL) -> ResolverTarget | None:
    target = parse_crates_url(parsed.url)
    if target is None:
        return None
    return ResolverTarget(url=parsed.url, kind="crates", values={"crate": target.crate_name})


async def fetch_crates_raw(target: ResolverTarget, ctx: FetchContext) -> RawDocument:
    """Acquire a Crates.io manifest and README tarball; return :class:`RawDocument`."""

    payload = await fetch_crates_payload(ctx, target)
    readme = await download_crates_readme(ctx, payload, target)
    package_doc: PackageDocument = crates_package_document(payload, target, readme)
    diagnostics = (
        Diagnostic(
            phase="acquisition",
            code="crates_fetched",
            message=f"Crates.io {package_doc.name}@{package_doc.version}",
        ),
    )
    metadata: dict[str, Any] = {
        "registry": package_doc.registry,
        "version": package_doc.version,
        "name": package_doc.name,
        "summary": package_doc.summary,
        "readme_format": readme.format,
    }
    return RawDocument(
        input_url=target.url,
        fetched_url=target.url,
        source_type="crates_io",
        fetch_backend="crates_api",
        body=package_doc,
        title=f"{package_doc.name} {package_doc.version or ''}".strip(),
        metadata=metadata,
        links=package_doc.links,
        diagnostics=diagnostics,
        complete=bool(package_doc.readme.text or package_doc.summary),
        scope="full",
    )


__all__ = [
    "CratesError",
    "CratesTarget",
    "fetch_crates_raw",
    "match_crates",
    "parse_crates_url",
]


@dataclass(frozen=True)
class CratesReadme:
    text: str
    format: str


async def fetch_crates_payload(ctx: FetchContext, target: ResolverTarget) -> dict[str, Any]:
    crate = target.values.get("crate") or target.values.get("name")
    if not crate:
        raise AcquisitionError(code="bad_target", message="Crates.io target missing crate name")
    url = f"https://crates.io/api/v1/crates/{crate}"
    headers = {
        "Accept": "application/json",
        "User-Agent": "kindly-web-search/1.0 (crates resolver)",
    }
    data = await fetch_json(ctx, url, what="Crates.io registry", headers=headers)
    if not isinstance(data, dict):
        raise AcquisitionError(code="crates_shape", message="Crates.io payload was not an object")
    crate_block = _as_dict(data.get("crate"))
    return {"crate": crate_block, "versions": data.get("versions") or []}


async def download_crates_readme(
    ctx: FetchContext, payload: dict[str, Any], target: ResolverTarget
) -> CratesReadme:
    crate_block = _as_dict(payload.get("crate"))
    max_version = str(crate_block.get("max_stable_version") or crate_block.get("max_version") or "")
    name = str(crate_block.get("name") or target.values.get("crate") or "")
    if not max_version:
        raise AcquisitionError(code="crates_no_version", message="Crates.io crate has no version")
    url = f"https://crates.io/api/v1/crates/{name}/{max_version}/download"
    headers = {"User-Agent": "kindly-web-search/1.0 (crates resolver)"}
    response = await request_with_redirect_validation(
        ctx, url, headers=headers, follow_redirects=True
    )
    raise_for_status(response, what="Crates.io crate tarball")
    body = bytes_with_cap(response, cap=ctx.max_response_bytes, what="Crates.io crate tarball")
    return _extract_crates_readme(body)


def crates_package_document(
    payload: dict[str, Any], target: ResolverTarget, readme: CratesReadme
) -> PackageDocument:
    crate_block = _as_dict(payload.get("crate"))
    name = str(crate_block.get("name") or target.values.get("crate") or "crate")
    version = crate_block.get("max_stable_version") or crate_block.get("max_version")
    description = str(crate_block.get("description") or "")
    keywords = crate_block.get("keywords") or []
    categories = crate_block.get("categories") or []
    links_pieces: list[dict[str, Any]] = []
    if crate_block.get("documentation"):
        links_pieces.append({"label": "documentation", "href": str(crate_block["documentation"])})
    if crate_block.get("repository"):
        links_pieces.append({"label": "repository", "href": str(crate_block["repository"])})
    if crate_block.get("homepage"):
        links_pieces.append({"label": "homepage", "href": str(crate_block["homepage"])})
    metadata = {
        "downloads": crate_block.get("downloads"),
        "recent_downloads": crate_block.get("recent_downloads"),
        "keywords": list(keywords) if isinstance(keywords, list) else [],
        "categories": list(categories) if isinstance(categories, list) else [],
        "max_version": version,
    }
    return build_package_document(
        name=name,
        url=target.url,
        registry="crates.io",
        summary=description,
        version=version if isinstance(version, str) else None,
        readme_text=readme.text,
        readme_format=readme.format,
        dependencies=(),
        links=tuple(links_pieces),
        metadata=metadata,
    )


_CRATES_README_NAMES = frozenset({"README.md", "README.markdown", "README", "README.txt"})


def _extract_crates_readme(payload: bytes) -> CratesReadme:
    """Pick the first ``README*`` from the Crates.io crate tarball.

    The README format is declared as ``application/octet-stream`` because the
    filename inside the tarball is the only format hint; we keep that literal
    format so the downstream renderer can decide.
    """

    candidates: list[tuple[str, str]] = []
    if payload[:2] == b"PK":
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                base = info.filename.rsplit("/", 1)[-1]
                if base in _CRATES_README_NAMES:
                    text = archive.read(info).decode("utf-8", errors="replace")
                    candidates.append((base, text))
    else:
        with tarfile.open(fileobj=BytesIO(payload), mode="r:*") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                base = member.name.rsplit("/", 1)[-1]
                if base in _CRATES_README_NAMES:
                    handle = archive.extractfile(member)
                    if handle is None:
                        continue
                    text = handle.read().decode("utf-8", errors="replace")
                    candidates.append((base, text))
    if not candidates:
        return CratesReadme(text="", format="application/octet-stream")
    order = ("README.md", "README.markdown", "README", "README.txt")
    candidates.sort(key=lambda item: order.index(item[0]) if item[0] in order else 99)
    name, text = candidates[0]
    if name.endswith((".md", ".markdown")):
        return CratesReadme(text=text, format="text/markdown")
    return CratesReadme(text=text, format="text/plain")
