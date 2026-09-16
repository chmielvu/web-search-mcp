"""npm specialized resolver returning RawDocument candidates."""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

from ..documents import build_package_document
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


class NpmError(RuntimeError):
    pass


@dataclass(frozen=True)
class NpmTarget:
    package_name: str


_NPM_HOSTS = frozenset({"npmjs.com", "www.npmjs.com", "npmjs.org", "www.npmjs.org"})
_NPM_PATH_RE = re.compile(r"^/package/((?:@[^/]+/)?[^/]+)")


def parse_npm_url(url: str) -> NpmTarget | None:
    """Parse an npm package URL."""

    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return None
    if parsed.hostname is None or parsed.hostname.lower() not in _NPM_HOSTS:
        return None
    match = _NPM_PATH_RE.match(parsed.path or "")
    if not match:
        return None
    package = match.group(1).strip()
    if not package:
        return None
    return NpmTarget(package_name=package)


def match_npm(parsed: ParsedURL) -> ResolverTarget | None:
    target = parse_npm_url(parsed.url)
    if target is None:
        return None
    return ResolverTarget(url=parsed.url, kind="npm", values={"package": target.package_name})


async def fetch_npm_raw(target: ResolverTarget, ctx: FetchContext) -> RawDocument:
    """Acquire an npm registry JSON envelope and return a :class:`RawDocument`."""

    payload = await fetch_npm_payload(ctx, target)
    package_doc: PackageDocument = npm_package_document(payload, target)
    diagnostics = (
        Diagnostic(
            phase="acquisition",
            code="npm_fetched",
            message=f"npm {package_doc.name}@{package_doc.version}",
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
        source_type="npm_package",
        fetch_backend="npm_registry",
        body=package_doc,
        title=f"{package_doc.name} {package_doc.version or ''}".strip(),
        metadata=metadata,
        links=package_doc.links,
        diagnostics=diagnostics,
        complete=bool(package_doc.readme.text or package_doc.summary),
        scope="full",
    )


__all__ = [
    "NpmError",
    "NpmTarget",
    "fetch_npm_raw",
    "match_npm",
    "parse_npm_url",
]


async def fetch_npm_payload(ctx: FetchContext, target: ResolverTarget) -> dict[str, Any]:
    package = target.values.get("package") or target.values.get("name")
    if not package:
        raise AcquisitionError(code="bad_target", message="npm target missing package name")
    encoded = package.replace("/", "%2F")
    url = f"https://registry.npmjs.org/{encoded}"
    headers = {"Accept": "application/json"}
    data = await fetch_json(ctx, url, what="npm registry", headers=headers)
    if not isinstance(data, dict):
        raise AcquisitionError(code="npm_shape", message="npm payload was not an object")
    return data


def npm_package_document(data: dict[str, Any], target: ResolverTarget) -> PackageDocument:
    name = str(data.get("name") or target.values.get("package") or "package")
    dist_tags = data.get("dist-tags") or {}
    latest_version = dist_tags.get("latest") if isinstance(dist_tags, dict) else None
    if not latest_version:
        versions = data.get("versions")
        if isinstance(versions, dict) and versions:
            latest_version = next(iter(versions.keys()), None)
    versions = data.get("versions") or {}
    latest_data = versions.get(latest_version) if isinstance(versions, dict) else None
    latest_data = latest_data if isinstance(latest_data, dict) else {}
    description = str(data.get("description") or "")
    readme_text = str(data.get("readme") or latest_data.get("readme") or "")
    deps = latest_data.get("dependencies") or {}
    dependency_names: tuple[str, ...] = ()
    if isinstance(deps, dict):
        dependency_names = tuple(name for name in deps)
    links_pieces: list[dict[str, Any]] = []
    if data.get("homepage"):
        links_pieces.append({"label": "homepage", "href": str(data["homepage"])})
    repository = data.get("repository")
    if isinstance(repository, dict):
        repo_url = repository.get("url") if isinstance(repository.get("url"), str) else ""
        if repo_url:
            clean_repo = repo_url.removeprefix("git+").removesuffix(".git")
            links_pieces.append({"label": "repository", "href": clean_repo})
    elif isinstance(repository, str) and repository:
        links_pieces.append({"label": "repository", "href": repository})
    readme_format = "text/markdown" if readme_text.lstrip().startswith(("#", "!")) else "text/plain"
    metadata = {
        "version": latest_version,
        "license": data.get("license"),
        "author": data.get("author"),
    }
    return build_package_document(
        name=name,
        url=target.url,
        registry="npm",
        summary=description,
        version=latest_version,
        readme_text=readme_text,
        readme_format=readme_format,
        dependencies=dependency_names,
        links=tuple(links_pieces),
        metadata=metadata,
    )
