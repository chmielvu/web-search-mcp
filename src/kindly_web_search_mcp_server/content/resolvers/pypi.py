"""Specialized resolver for PyPI packages (https://pypi.org/project/<pkg>/).

Fetches package metadata and documentation directly from the PyPI JSON API.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

from ..sanitize import sanitize_markdown


class PyPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class PyPITarget:
    package_name: str


_PYPI_PATH_RE = re.compile(r"^/(?:project|pypi)/([^/]+)")


def parse_pypi_url(url: str) -> PyPITarget | None:
    """Parse a PyPI package URL (e.g. https://pypi.org/project/fastapi/ or https://pypi.org/project/fastapi/0.110.0/)."""
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if host not in ("pypi.org", "www.pypi.org", "pypi.python.org"):
            return None
        m = _PYPI_PATH_RE.match(parsed.path or "")
        if m:
            pkg = m.group(1).strip()
            if pkg:
                return PyPITarget(package_name=pkg)
        return None
    except Exception:
        return None


def render_pypi_markdown(data: dict[str, Any], url: str) -> str:
    """Render PyPI package JSON data to structured Markdown."""
    info = data.get("info", {})
    name = info.get("name") or "Python Package"
    version = info.get("version") or ""
    summary = info.get("summary") or ""
    author = info.get("author") or info.get("author_email") or ""
    pkg_license = info.get("license") or ""
    requires_python = info.get("requires_python") or ""
    home_page = info.get("home_page") or info.get("package_url") or url
    project_urls = info.get("project_urls") or {}
    requires_dist = info.get("requires_dist") or []
    description = info.get("description") or ""

    lines: list[str] = [
        f"# PyPI: {name} (v{version})",
        f"**Source:** {url}",
    ]

    meta_parts: list[str] = []
    if summary:
        lines.append(f"\n> {summary}\n")
    if author:
        meta_parts.append(f"**Author:** {author}")
    if pkg_license:
        meta_parts.append(f"**License:** {pkg_license}")
    if requires_python:
        meta_parts.append(f"**Python:** `{requires_python}`")
    if meta_parts:
        lines.append(" | ".join(meta_parts))

    # Project URLs
    links: list[str] = []
    if home_page and home_page != url:
        links.append(f"[Homepage]({home_page})")
    if project_urls:
        for label, target_url in list(project_urls.items())[:5]:
            if target_url and target_url != home_page:
                links.append(f"[{label}]({target_url})")
    if links:
        lines.append("\n**Links:** " + " • ".join(links))

    # Dependencies (first 15)
    if requires_dist:
        lines.append("\n### Dependencies")
        for req in requires_dist[:15]:
            lines.append(f"- `{req}`")
        if len(requires_dist) > 15:
            lines.append(f"_... and {len(requires_dist) - 15} more dependencies_")

    # Full README / description
    if description.strip():
        lines.append("\n## Documentation & README\n")
        lines.append(sanitize_markdown(description.strip()))

    return "\n".join(lines).strip() + "\n"


async def fetch_pypi_package_markdown(
    url: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """Fetch PyPI package JSON and return clean Markdown."""
    target = parse_pypi_url(url)
    if not target:
        raise PyPIError(f"URL is not a recognized PyPI package URL: {url}")

    api_url = f"https://pypi.org/pypi/{target.package_name}/json"

    async def _run(client: httpx.AsyncClient) -> str:
        headers = {"User-Agent": "kindly-web-search-mcp/1.0 (package-resolver)"}
        resp = await client.get(api_url, headers=headers)
        if resp.status_code == 404:
            raise PyPIError(f"PyPI package '{target.package_name}' not found (404).")
        if resp.status_code != 200:
            raise PyPIError(f"PyPI API returned HTTP {resp.status_code}")
        data = resp.json()
        return render_pypi_markdown(data, url)

    if http_client is None:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            return await _run(client)
    return await _run(http_client)
