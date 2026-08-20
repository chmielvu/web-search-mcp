"""Specialized resolver for Hugging Face models and datasets (https://huggingface.co/<model_or_dataset>).

Fetches model card metadata and raw README markdown directly from the Hugging Face Hub API.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

from ..sanitize import sanitize_markdown


class HuggingFaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class HuggingFaceTarget:
    target_id: str
    is_dataset: bool


_HF_EXCLUDED_PATHS = {
    "models",
    "datasets",
    "spaces",
    "docs",
    "blog",
    "pricing",
    "login",
    "join",
    "settings",
}


def parse_huggingface_url(url: str) -> HuggingFaceTarget | None:
    """Parse a Hugging Face model or dataset URL."""
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if host not in ("huggingface.co", "www.huggingface.co"):
            return None

        path_parts = [p for p in (parsed.path or "").strip("/").split("/") if p]
        if not path_parts:
            return None

        if path_parts[0] == "datasets" and len(path_parts) >= 2:
            target_id = "/".join(path_parts[1:3])
            return HuggingFaceTarget(target_id=target_id, is_dataset=True)

        if path_parts[0] in _HF_EXCLUDED_PATHS:
            return None

        # Model URL format: /owner/model or /model
        target_id = "/".join(path_parts[:2])
        return HuggingFaceTarget(target_id=target_id, is_dataset=False)
    except Exception:
        return None


def render_huggingface_markdown(
    metadata: dict[str, Any],
    readme_text: str,
    target: HuggingFaceTarget,
    url: str,
) -> str:
    """Render Hugging Face model/dataset card to structured Markdown."""
    target_type = "Dataset" if target.is_dataset else "Model"
    name = metadata.get("id") or target.target_id
    pipeline_tag = metadata.get("pipeline_tag") or ""
    author = metadata.get("author") or ""
    downloads = metadata.get("downloads", 0)
    likes = metadata.get("likes", 0)
    tags = metadata.get("tags") or []
    license_tag = next((t.replace("license:", "") for t in tags if t.startswith("license:")), None)

    lines: list[str] = [
        f"# Hugging Face {target_type}: {name}",
        f"**Source:** {url}",
    ]

    meta_parts: list[str] = []
    if pipeline_tag:
        meta_parts.append(f"**Task:** `{pipeline_tag}`")
    if author:
        meta_parts.append(f"**Author:** {author}")
    if license_tag:
        meta_parts.append(f"**License:** `{license_tag}`")
    if downloads:
        meta_parts.append(f"**Downloads:** {downloads:,}")
    if likes:
        meta_parts.append(f"**Likes:** {likes:,}")
    if meta_parts:
        lines.append(" | ".join(meta_parts))

    if tags:
        clean_tags = [t for t in tags if not t.startswith("license:")][:10]
        if clean_tags:
            lines.append("\n**Tags:** " + ", ".join(f"`{t}`" for t in clean_tags))

    if readme_text.strip():
        lines.append("\n## Model Card & Documentation\n")
        lines.append(sanitize_markdown(readme_text.strip()))

    return "\n".join(lines).strip() + "\n"


async def fetch_huggingface_markdown(
    url: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """Fetch Hugging Face model/dataset metadata and README, returning clean Markdown."""
    target = parse_huggingface_url(url)
    if not target:
        raise HuggingFaceError(f"URL is not a recognized Hugging Face model or dataset URL: {url}")

    api_path = (
        f"/api/datasets/{target.target_id}"
        if target.is_dataset
        else f"/api/models/{target.target_id}"
    )
    api_url = f"https://huggingface.co{api_path}"

    raw_readme_path = (
        f"/datasets/{target.target_id}/raw/main/README.md"
        if target.is_dataset
        else f"/{target.target_id}/raw/main/README.md"
    )
    readme_url = f"https://huggingface.co{raw_readme_path}"

    async def _run(client: httpx.AsyncClient) -> str:
        headers = {"User-Agent": "kindly-web-search-mcp/1.0 (hf-resolver)"}
        meta_resp = await client.get(api_url, headers=headers)
        if meta_resp.status_code == 404:
            raise HuggingFaceError(f"Hugging Face item '{target.target_id}' not found (404).")
        if meta_resp.status_code != 200:
            raise HuggingFaceError(f"Hugging Face API returned HTTP {meta_resp.status_code}")
        metadata = meta_resp.json()

        # Fetch README
        readme_resp = await client.get(readme_url, headers=headers)
        readme_text = readme_resp.text if readme_resp.status_code == 200 else ""

        return render_huggingface_markdown(metadata, readme_text, target, url)

    if http_client is None:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            return await _run(client)
    return await _run(http_client)
