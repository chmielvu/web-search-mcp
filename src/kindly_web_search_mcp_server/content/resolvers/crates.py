"""Specialized resolver for Rust Crates (https://crates.io/crates/<crate>).

Fetches crate metadata and README directly from the Crates.io REST API.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

from ..sanitize import sanitize_markdown


class CratesError(RuntimeError):
    pass


@dataclass(frozen=True)
class CratesTarget:
    crate_name: str


_CRATES_PATH_RE = re.compile(r"^/crates/([^/]+)")


def parse_crates_url(url: str) -> CratesTarget | None:
    """Parse a Crates.io URL (e.g. https://crates.io/crates/serde or https://crates.io/crates/tokio/1.38.0)."""
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if host not in ("crates.io", "www.crates.io"):
            return None
        m = _CRATES_PATH_RE.match(parsed.path or "")
        if m:
            name = m.group(1).strip()
            if name:
                return CratesTarget(crate_name=name)
        return None
    except Exception:
        return None


def render_crates_markdown(data: dict[str, Any], readme_text: str, url: str) -> str:
    """Render Crates.io metadata and README to structured Markdown."""
    crate = data.get("crate", {})
    name = crate.get("name") or "Rust Crate"
    max_version = crate.get("max_version") or ""
    description = crate.get("description") or ""
    documentation = crate.get("documentation") or ""
    homepage = crate.get("homepage") or ""
    repository = crate.get("repository") or ""
    downloads = crate.get("downloads", 0)
    recent_downloads = crate.get("recent_downloads", 0)
    keywords = crate.get("keywords") or []
    categories = crate.get("categories") or []

    lines: list[str] = [
        f"# Crates.io: {name} (v{max_version})",
        f"**Source:** {url}",
    ]

    if description:
        lines.append(f"\n> {description}\n")

    meta_parts: list[str] = []
    if downloads:
        meta_parts.append(f"**Downloads:** {downloads:,}")
    if recent_downloads:
        meta_parts.append(f"**Recent:** {recent_downloads:,}")
    if meta_parts:
        lines.append(" | ".join(meta_parts))

    links: list[str] = []
    if documentation:
        links.append(f"[Docs]({documentation})")
    if repository:
        links.append(f"[Repository]({repository})")
    if homepage and homepage not in (documentation, repository):
        links.append(f"[Homepage]({homepage})")
    if links:
        lines.append("\n**Links:** " + " • ".join(links))

    if categories or keywords:
        tags = [f"`{c}`" for c in (categories + keywords)[:10]]
        if tags:
            lines.append("\n**Keywords & Categories:** " + ", ".join(tags))

    if readme_text.strip():
        lines.append("\n## Documentation & README\n")
        lines.append(sanitize_markdown(readme_text.strip()))

    return "\n".join(lines).strip() + "\n"


async def fetch_crates_markdown(
    url: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """Fetch Crates.io metadata and README, returning clean Markdown."""
    target = parse_crates_url(url)
    if not target:
        raise CratesError(f"URL is not a recognized Crates.io URL: {url}")

    api_url = f"https://crates.io/api/v1/crates/{target.crate_name}"

    async def _run(client: httpx.AsyncClient) -> str:
        headers = {"User-Agent": "kindly-web-search-mcp/1.0 (crates-resolver)"}
        resp = await client.get(api_url, headers=headers)
        if resp.status_code == 404:
            raise CratesError(f"Crate '{target.crate_name}' not found on Crates.io (404).")
        if resp.status_code != 200:
            raise CratesError(f"Crates.io API returned HTTP {resp.status_code}")
        data = resp.json()

        version = data.get("crate", {}).get("max_version")
        readme_text = ""
        if version:
            readme_url = f"https://crates.io/api/v1/crates/{target.crate_name}/{version}/readme"
            r_readme = await client.get(readme_url, headers=headers)
            if r_readme.status_code == 200:
                readme_text = r_readme.text

        return render_crates_markdown(data, readme_text, url)

    if http_client is None:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            return await _run(client)
    return await _run(http_client)
