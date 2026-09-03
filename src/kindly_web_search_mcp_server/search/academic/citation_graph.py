"""Citation-graph and author lookups for academic_search.

Supports ``cited_by_paper_id`` (incoming citations), ``references_paper_id``
(outgoing bibliography), and ``author_id`` filters across Semantic Scholar
and OpenAlex — the two providers whose public APIs expose citation graphs.

References
- S2 Graph API: GET /graph/v1/paper/{id}/citations|references,
  GET /graph/v1/author/{id}/papers. Paper refs accept ``DOI:10.x/y``,
  ``ARXIV:1234.5678``, PMID/PMCID prefixed forms, or raw S2 paperIds.
- OpenAlex: filter ``cites:<WorkID>`` (incoming); outgoing references come
  from the work record's ``referenced_works`` list, hydrated in batch;
  author scoping via ``author.id`` / ORCID.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Literal

import httpx

from ...models import AcademicPaper

logger = logging.getLogger(__name__)

_S2_GRAPH = "https://api.semanticscholar.org/graph/v1"
_OPENALEX_API = "https://api.openalex.org"
_TIMEOUT = int(os.environ.get("ACADEMIC_GRAPH_TIMEOUT", "30"))


PaperRefKind = Literal["doi", "arxiv", "pmid", "openalex", "s2", "raw"]

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_ARXIV_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$|[a-z-]+/\d{7}$", re.IGNORECASE)
_OPENALEX_RE = re.compile(r"^[WCPA]\d+$", re.IGNORECASE)
_S2_RE = re.compile(r"^[0-9a-f]{40}$")


def classify_paper_ref(raw: str) -> tuple[PaperRefKind, str]:
    """Normalize a caller-supplied paper reference into (kind, canonical)."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("paper reference must be non-empty.")

    lower = text.lower()
    if lower.startswith("doi:"):
        return "doi", text[4:].strip()
    if lower.startswith("arxiv:"):
        return "arxiv", text[6:].strip()
    if lower.startswith("pmid:"):
        return "pmid", text[5:].strip()
    if lower.startswith(("https://openalex.org/", "http://openalex.org/")):
        return "openalex", text.rsplit("/", 1)[-1]
    if lower.startswith("https://doi.org/"):
        return "doi", text[len("https://doi.org/") :].strip()
    if lower.startswith("https://arxiv.org/abs/"):
        return "arxiv", text.rsplit("/", 1)[-1]

    if _OPENALEX_RE.match(text):
        return "openalex", text.upper()
    if _DOI_RE.match(text):
        return "doi", text
    if _ARXIV_RE.match(text):
        return "arxiv", text
    if _PMID_ISH(text):
        return "pmid", text
    if _S2_RE.match(text.lower()):
        return "s2", text.lower()
    return "raw", text


def _PMID_ISH(text: str) -> bool:
    return text.isdigit() and len(text) <= 9


# ---------------------------------------------------------------------------
# Semantic Scholar
# ---------------------------------------------------------------------------


def _s2_paper_ref(kind: PaperRefKind, value: str) -> str:
    if kind == "doi":
        return f"DOI:{value}"
    if kind == "arxiv":
        return f"ARXIV:{value}"
    if kind == "pmid":
        return f"PMID:{value}"
    if kind == "openalex":
        # S2 has no OpenAlex mapping; require an S2-native ref instead.
        raise ValueError(
            "OpenAlex IDs are not valid Semantic Scholar references; "
            "pass a DOI, arXiv ID, or S2 paperId."
        )
    return value


def _s2_headers() -> dict[str, str]:
    api_key = (os.environ.get("S2_API_KEY") or "").strip()
    return {"x-api-key": api_key} if api_key else {}


def _s2_fields() -> str:
    from .academic_s2 import S2_FIELDS

    return S2_FIELDS


def _papers_from_s2_items(items: object, *, wrapper_key: str | None) -> list[AcademicPaper]:
    from .academic_s2 import _normalize_paper

    out: list[AcademicPaper] = []
    if not isinstance(items, list):
        return out
    for item in items:
        payload = item.get(wrapper_key) if wrapper_key and isinstance(item, dict) else item
        paper = _normalize_paper(payload) if isinstance(payload, dict) else None
        if paper is not None:
            out.append(paper)
    return out


async def _s2_graph_get(path: str, params: dict[str, str | int]) -> list[dict] | dict:
    url = f"{_S2_GRAPH}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, params=params, headers=_s2_headers())
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 - fail-open like sibling providers
        logger.warning("Semantic Scholar graph lookup failed (%s): %s", path, exc)
        return []
    return data if isinstance(data, (list, dict)) else {}


async def search_s2_citations(paper_ref: str, *, limit: int = 20) -> list[AcademicPaper]:
    """Papers that cite ``paper_ref`` (incoming citations)."""
    kind, value = classify_paper_ref(paper_ref)
    data = await _s2_graph_get(
        f"/paper/{_s2_paper_ref(kind, value)}/citations",
        {"fields": _s2_fields(), "limit": max(1, min(limit, 100))},
    )
    items = data.get("data") if isinstance(data, dict) else data
    return _papers_from_s2_items(items, wrapper_key="citingPaper")[:limit]


async def search_s2_references(paper_ref: str, *, limit: int = 20) -> list[AcademicPaper]:
    """Papers cited by ``paper_ref`` (its bibliography)."""
    kind, value = classify_paper_ref(paper_ref)
    data = await _s2_graph_get(
        f"/paper/{_s2_paper_ref(kind, value)}/references",
        {"fields": _s2_fields(), "limit": max(1, min(limit, 100))},
    )
    items = data.get("data") if isinstance(data, dict) else data
    return _papers_from_s2_items(items, wrapper_key="citedPaper")[:limit]


async def search_s2_author_papers(author_id: str, *, limit: int = 20) -> list[AcademicPaper]:
    """Papers authored by an S2 authorId."""
    aid = (author_id or "").strip()
    if not aid:
        return []
    data = await _s2_graph_get(
        f"/author/{aid}/papers",
        {"fields": _s2_fields(), "limit": max(1, min(limit, 100))},
    )
    items = data.get("data") if isinstance(data, dict) else data
    return _papers_from_s2_items(items, wrapper_key=None)[:limit]


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------


async def _openalex_get(params: dict[str, str]) -> dict:
    mailto = os.environ.get("OPENALEX_EMAIL", "").strip()
    headers = {"User-Agent": "web-search-mcp-academic/1.0"}
    if mailto:
        params = {"mailto": mailto, **params}
    api_key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if api_key:
        params["api_key"] = api_key
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{_OPENALEX_API}/works", params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenAlex works lookup failed: %s", exc)
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_work(raw: dict) -> AcademicPaper | None:
    from .academic_openalex import _normalize_openalex

    return _normalize_openalex(raw)


async def _resolve_openalex_id(ref: str) -> str | None:
    """Resolve any supported ref form to an OpenAlex W-id (or None)."""
    kind, value = classify_paper_ref(ref)
    if kind == "openalex":
        return value.upper()
    if kind == "doi":
        data = await _openalex_get({"filter": f"doi:{value}"})
        results = data.get("results") or []
        wid = results[0].get("id") if results else None
        return str(wid).rsplit("/", 1)[-1] if wid else None
    if kind == "arxiv":
        data = await _openalex_get({"filter": f"ids.arxiv:{value}"})
        results = data.get("results") or []
        wid = results[0].get("id") if results else None
        return str(wid).rsplit("/", 1)[-1] if wid else None
    logger.warning("Cannot map %s ref %r to OpenAlex; use DOI/arXiv/OpenAlex ID.", kind, ref)
    return None


async def search_openalex_citations(paper_ref: str, *, limit: int = 20) -> list[AcademicPaper]:
    """Papers that cite ``paper_ref`` via OpenAlex ``cites`` filter."""
    wid = await _resolve_openalex_id(paper_ref)
    if not wid:
        return []
    data = await _openalex_get(
        {
            "filter": f"cites:{wid}",
            "per-page": str(max(1, min(limit, 100))),
            "sort": "cited_by_count:desc",
        }
    )
    papers = [p for p in (_normalize_work(r) for r in data.get("results") or []) if p]
    return papers[:limit]


async def search_openalex_references(paper_ref: str, *, limit: int = 20) -> list[AcademicPaper]:
    """Papers cited BY ``paper_ref``, hydrated from ``referenced_works``."""
    wid = await _resolve_openalex_id(paper_ref)
    if not wid:
        return []
    record = await _openalex_get({"filter": f"ids.openalex:{wid}"})
    results = record.get("results") or []
    refs: list[str] = []
    if results and isinstance(results[0].get("referenced_works"), list):
        refs = [str(r).rsplit("/", 1)[-1] for r in results[0]["referenced_works"]]
    if not refs:
        return []
    selected = "|".join(refs[: max(1, min(limit, 100))])
    data = await _openalex_get({"filter": f"ids.openalex:{selected}", "per-page": str(len(refs[:limit]))})
    papers = [p for p in (_normalize_work(r) for r in data.get("results") or []) if p]
    return papers[:limit]


def _is_orcid(author_id: str) -> bool:
    return author_id.lower().startswith("orcid:") or "orcid.org" in author_id.lower()


async def search_openalex_author_papers(author_id: str, *, limit: int = 20) -> list[AcademicPaper]:
    """Papers by an OpenAlex author ID or ORCID."""
    aid = (author_id or "").strip()
    if not aid:
        return []
    if _is_orcid(aid):
        orcid = aid.split(":", 1)[1] if ":" in aid and aid.lower().startswith("orcid:") else aid
        orcid = orcid.rstrip("/")
        if not orcid.startswith("http"):
            orcid = f"https://orcid.org/{orcid}"
        filter_value = f"author.orcid:{orcid}"
    elif _OPENALEX_RE.match(aid):
        filter_value = f"author.id:{aid.upper()}"
    else:
        logger.warning("Unrecognized OpenAlex author reference: %r", author_id)
        return []
    data = await _openalex_get(
        {
            "filter": filter_value,
            "per-page": str(max(1, min(limit, 100))),
            "sort": "cited_by_count:desc",
        }
    )
    papers = [p for p in (_normalize_work(r) for r in data.get("results") or []) if p]
    return papers[:limit]
