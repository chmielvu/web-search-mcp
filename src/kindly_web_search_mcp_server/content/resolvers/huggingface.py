"""Hugging Face Hub specialized resolver returning RawDocument candidates."""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Any


from ..models import (
    AcquisitionError,
    Diagnostic,
    FetchContext,
    ParsedURL,
    RawDocument,
    RepositoryDocument,
    ResolverTarget,
)
from ..documents import (
    _as_dict,
    _as_list,
    _as_str,
    build_repository_document,
)
from ..http_utils import fetch_json, fetch_text


class HuggingFaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class HuggingFaceTarget:
    target_id: str
    is_dataset: bool


_HF_HOSTS = frozenset({"huggingface.co", "www.huggingface.co"})
_HF_EXCLUDED_PATHS = frozenset(
    {"models", "datasets", "spaces", "docs", "blog", "pricing", "login", "join", "settings"}
)


def parse_huggingface_url(url: str) -> HuggingFaceTarget | None:
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if host not in _HF_HOSTS:
            return None
        parts = [p for p in (parsed.path or "").strip("/").split("/") if p]
        if not parts:
            return None
        if parts[0] == "datasets" and len(parts) >= 2:
            return HuggingFaceTarget(target_id="/".join(parts[1:3]), is_dataset=True)
        if parts[0] in _HF_EXCLUDED_PATHS:
            return None
        return HuggingFaceTarget(target_id="/".join(parts[:2]), is_dataset=False)
    except Exception:
        return None


def match_huggingface(parsed: ParsedURL) -> ResolverTarget | None:
    target = parse_huggingface_url(parsed.url)
    if target is None:
        return None
    kind = "dataset" if target.is_dataset else "model"
    return ResolverTarget(
        url=parsed.url,
        kind=f"hf_{kind}",
        values={
            "target_id": target.target_id,
            "kind": "dataset" if target.is_dataset else "model",
        },
    )


async def fetch_huggingface_raw(target: ResolverTarget, ctx: FetchContext) -> RawDocument:
    """Acquire Hugging Face metadata and README; return a :class:`RawDocument`."""

    payload = await fetch_huggingface_payload(ctx, target)
    readme_text, readme_format = await fetch_huggingface_readme(ctx, target, payload)
    repo_doc: RepositoryDocument = huggingface_repository_document(
        payload, target, readme_text, readme_format
    )
    diagnostics = (
        Diagnostic(
            phase="acquisition",
            code="hf_fetched",
            message=f"Hugging Face {target.values.get('kind', 'model')} {repo_doc.name}",
        ),
    )
    metadata: dict[str, Any] = {
        "registry": "huggingface",
        "kind": target.values.get("kind", "model"),
        "name": repo_doc.name,
        "links": list(repo_doc.links),
    }
    return RawDocument(
        input_url=target.url,
        fetched_url=target.url,
        source_type="huggingface_hub",
        fetch_backend="hf_api",
        body=repo_doc,
        title=repo_doc.name,
        metadata=metadata,
        links=repo_doc.links,
        diagnostics=diagnostics,
        complete=bool(repo_doc.readme.text or repo_doc.files),
        scope="full",
    )


__all__ = [
    "HuggingFaceError",
    "HuggingFaceTarget",
    "parse_huggingface_url",
    "match_huggingface",
    "fetch_huggingface_raw",
]


async def fetch_huggingface_payload(ctx: FetchContext, target: ResolverTarget) -> dict[str, Any]:
    target_id = target.values.get("target_id") or target.values.get("name")
    kind = target.values.get("kind") or "model"
    if not target_id:
        raise AcquisitionError(code="bad_target", message="HF target missing target_id")
    endpoint = (
        f"https://huggingface.co/api/datasets/{target_id}"
        if kind == "dataset"
        else f"https://huggingface.co/api/models/{target_id}"
    )
    headers = {"Accept": "application/json"}
    data = await fetch_json(ctx, endpoint, what="Hugging Face Hub API", headers=headers)
    if not isinstance(data, dict):
        raise AcquisitionError(code="huggingface_shape", message="Hub payload was not an object.")
    return data


async def fetch_huggingface_readme(
    ctx: FetchContext, target: ResolverTarget, payload: dict[str, Any]
) -> tuple[str, str]:
    siblings = _as_list(payload.get("siblings"))
    readme_name = ""
    for sibling in siblings:
        if not isinstance(sibling, dict):
            continue
        filename = _as_str(sibling.get("rfilename")) or ""
        if filename.lower() in {"readme.md", "readme.markdown"}:
            readme_name = filename
            break
    target_id = target.values.get("target_id") or target.values.get("name") or ""
    kind = target.values.get("kind") or "model"
    branch = "main"
    if isinstance(payload.get("sha"), str):
        branch = payload["sha"]
    if not readme_name:
        return "", "text/plain"
    headers = {"Accept": "text/markdown"}
    raw_url = f"https://huggingface.co/{kind}s/{target_id}/resolve/{branch}/{readme_name}"
    text = await fetch_text(ctx, raw_url, what="Hugging Face README", headers=headers)
    if not text:
        return "", "text/plain"
    return text, "text/markdown"


def huggingface_repository_document(
    payload: dict[str, Any],
    target: ResolverTarget,
    readme_text: str,
    readme_format: str,
) -> RepositoryDocument:
    target_id = str(payload.get("modelId") or target.values.get("target_id") or "model")
    pipeline_tag = _as_str(payload.get("pipeline_tag"))
    tags = _as_list(payload.get("tags"))
    card_data = _as_dict(payload.get("cardData"))
    links: list[dict[str, Any]] = []
    if isinstance(card_data, dict):
        for key in ("project_page", "paper", "repository", "demo", "model_zoo"):
            value = card_data.get(key)
            if isinstance(value, str) and value:
                links.append({"label": key, "href": value})
    metadata = {
        "pipeline_tag": pipeline_tag,
        "tags": [tag for tag in tags if isinstance(tag, str)],
        "library": payload.get("library_name"),
        "downloads": payload.get("downloads"),
        "likes": payload.get("likes"),
    }
    files: list[str] = []
    for sibling in _as_list(payload.get("siblings")):
        if not isinstance(sibling, dict):
            continue
        filename = _as_str(sibling.get("rfilename"))
        if filename:
            files.append(filename)
    return build_repository_document(
        name=target_id,
        url=target.url,
        readme_text=readme_text,
        readme_format=readme_format,
        files=tuple(files),
        links=tuple(links),
        metadata=metadata,
    )
