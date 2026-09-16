"""PyPI specialized resolver returning RawDocument candidates."""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

from ..documents import _as_dict, _as_str, _link_from_pairs, build_package_document
from ..http_utils import fetch_json
from ..models import (
    AcquisitionError,
    Diagnostic,
    FetchContext,
    PackageDocument,
    ParsedURL,
    RawDocument,
    ResolverTarget,
)


class PyPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class PyPITarget:
    package_name: str


_PYPI_HOSTS = frozenset({"pypi.org", "www.pypi.org", "pypi.io", "www.pypi.io"})
_PYPI_PATH_RE = re.compile(r"^/(?:project|pypi)/([^/]+)")


def parse_pypi_url(url: str) -> PyPITarget | None:
    """Parse a PyPI package URL (e.g. https://pypi.org/project/fastapi/)."""

    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return None
    if parsed.hostname is None or parsed.hostname.lower() not in _PYPI_HOSTS:
        return None
    match = _PYPI_PATH_RE.match(parsed.path or "")
    if not match:
        return None
    name = match.group(1).strip()
    if not name:
        return None
    return PyPITarget(package_name=name)


def _resolve_target_from_url(url: str) -> ResolverTarget | None:
    target = parse_pypi_url(url)
    if target is None:
        return None
    return ResolverTarget(url=url, kind="pypi", values={"package": target.package_name})


def match_pypi(parsed: ParsedURL) -> ResolverTarget | None:
    """Return a :class:`ResolverTarget` for any PyPI package URL."""

    return _resolve_target_from_url(parsed.url)


async def fetch_pypi_raw(target: ResolverTarget, ctx: FetchContext) -> RawDocument:
    """Acquire a PyPI JSON envelope and return a :class:`RawDocument`."""

    payload = await fetch_pypi_payload(ctx, target)
    package_doc: PackageDocument = pypi_package_document(payload, target)
    diagnostics = (
        Diagnostic(
            phase="acquisition",
            code="pypi_fetched",
            message=f"PyPI {package_doc.name}@{package_doc.version}",
        ),
    )
    metadata: dict[str, Any] = {
        "registry": package_doc.registry,
        "version": package_doc.version,
        "name": package_doc.name,
        "summary": package_doc.summary,
        "dependencies": list(package_doc.dependencies),
    }
    return RawDocument(
        input_url=target.url,
        fetched_url=target.url,
        source_type="pypi_package",
        fetch_backend="pypi_json",
        body=package_doc,
        title=f"{package_doc.name} {package_doc.version or ''}".strip(),
        metadata=metadata,
        links=package_doc.links,
        diagnostics=diagnostics,
        complete=bool(package_doc.readme.text or package_doc.summary),
        scope="full",
    )


__all__ = [
    "PyPIError",
    "PyPITarget",
    "fetch_pypi_raw",
    "match_pypi",
    "parse_pypi_url",
]


async def fetch_pypi_payload(ctx: FetchContext, target: ResolverTarget) -> dict[str, Any]:
    package = target.values.get("package") or target.values.get("name")
    if not package:
        raise AcquisitionError(code="bad_target", message="PyPI target missing package name")
    url = f"https://pypi.org/pypi/{package}/json"
    data = await fetch_json(ctx, url, what="PyPI registry")
    if not isinstance(data, dict):
        raise AcquisitionError(code="pypi_shape", message="PyPI payload was not an object")
    return data


def pypi_package_document(data: dict[str, Any], target: ResolverTarget) -> PackageDocument:
    info = _as_dict(data.get("info"))
    name = str(info.get("name") or target.values.get("package") or "package")
    version = _as_str(info.get("version"))
    summary = str(info.get("summary") or "")
    description = str(info.get("description") or "")
    requires_dist = info.get("requires_dist") or []
    project_urls = info.get("project_urls") or {}
    links = _link_from_pairs(project_urls) if isinstance(project_urls, dict) else ()
    requires_python = info.get("requires_python")
    readme_format = (
        "text/markdown" if str(description).lstrip().startswith(("#", "!")) else "text/plain"
    )
    metadata = {
        "author": info.get("author"),
        "license": info.get("license")
        if not requires_python
        else [info.get("license"), requires_python],
    }
    return build_package_document(
        name=name,
        url=target.url,
        registry="pypi",
        summary=summary,
        version=version,
        readme_text=description,
        readme_format=readme_format,
        dependencies=tuple(str(dep) for dep in requires_dist if isinstance(dep, str)),
        links=links,
        metadata=metadata,
    )
