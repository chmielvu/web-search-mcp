"""Specialized resolver for npm packages (https://www.npmjs.com/package/<pkg>).

Fetches package metadata and README directly from the npm registry API.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

from ..sanitize import sanitize_markdown


class NpmError(RuntimeError):
    pass


@dataclass(frozen=True)
class NpmTarget:
    package_name: str


_NPM_PATH_RE = re.compile(r"^/package/((?:@[^/]+/)?[^/]+)")


def parse_npm_url(url: str) -> NpmTarget | None:
    """Parse an npm package URL (e.g. https://www.npmjs.com/package/express or https://www.npmjs.com/package/@fastmcp/core)."""
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if host not in ("npmjs.com", "www.npmjs.com", "registry.npmjs.org"):
            return None
        m = _NPM_PATH_RE.match(parsed.path or "")
        if m:
            pkg = m.group(1).strip()
            if pkg:
                return NpmTarget(package_name=pkg)
        return None
    except Exception:
        return None


def render_npm_markdown(data: dict[str, Any], url: str) -> str:
    """Render npm package JSON data to structured Markdown."""
    name = data.get("name") or "npm Package"
    dist_tags = data.get("dist-tags", {})
    latest_version = dist_tags.get("latest") or ""
    description = data.get("description") or ""
    author = data.get("author", {})
    author_name = author.get("name") if isinstance(author, dict) else str(author or "")
    pkg_license = data.get("license") or ""
    homepage = data.get("homepage") or ""
    repository = data.get("repository", {})
    repo_url = repository.get("url") if isinstance(repository, dict) else str(repository or "")
    # Dependencies & readme from latest version
    versions = data.get("versions", {})
    latest_data = versions.get(latest_version, {}) if latest_version else {}
    dependencies = latest_data.get("dependencies", {})
    readme = data.get("readme") or latest_data.get("readme") or ""

    lines: list[str] = [
        f"# npm: {name} (v{latest_version})",
        f"**Source:** {url}",
    ]

    if description:
        lines.append(f"\n> {description}\n")

    meta_parts: list[str] = []
    if author_name:
        meta_parts.append(f"**Author:** {author_name}")
    if pkg_license:
        meta_parts.append(f"**License:** {pkg_license}")
    if meta_parts:
        lines.append(" | ".join(meta_parts))

    links: list[str] = []
    if homepage:
        links.append(f"[Homepage]({homepage})")
    if repo_url:
        clean_repo = repo_url.removeprefix("git+").removesuffix(".git")
        links.append(f"[Repository]({clean_repo})")
    if links:
        lines.append("\n**Links:** " + " • ".join(links))

    if dependencies:
        lines.append("\n### Dependencies")
        for dep_name, dep_ver in list(dependencies.items())[:15]:
            lines.append(f"- `{dep_name}`: `{dep_ver}`")
        if len(dependencies) > 15:
            lines.append(f"_... and {len(dependencies) - 15} more dependencies_")

    if readme.strip():
        lines.append("\n## Documentation & README\n")
        lines.append(sanitize_markdown(readme.strip()))

    return "\n".join(lines).strip() + "\n"


async def fetch_npm_package_markdown(
    url: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """Fetch npm package JSON from registry and return clean Markdown."""
    target = parse_npm_url(url)
    if not target:
        raise NpmError(f"URL is not a recognized npm package URL: {url}")

    # Safe url encoding for scoped packages (@scope/pkg -> @scope%2Fpkg)
    encoded_pkg = target.package_name.replace("/", "%2F")
    api_url = f"https://registry.npmjs.org/{encoded_pkg}"

    async def _run(client: httpx.AsyncClient) -> str:
        headers = {"User-Agent": "kindly-web-search-mcp/1.0 (package-resolver)"}
        resp = await client.get(api_url, headers=headers)
        if resp.status_code == 404:
            raise NpmError(f"npm package '{target.package_name}' not found (404).")
        if resp.status_code != 200:
            raise NpmError(f"npm registry returned HTTP {resp.status_code}")
        data = resp.json()
        return render_npm_markdown(data, url)

    if http_client is None:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            return await _run(client)
    return await _run(http_client)
