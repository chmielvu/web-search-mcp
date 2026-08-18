"""dLibra digital libraries provider for academic search.

Accesses dLibra instances (WBC Poznań, JBC Jagiellonian, FBC) through the
OAI-PMH harvesting protocol. No API key required.

Note: OAI-PMH has NO free-text keyword search — ListRecords only supports
date ranges and sets. ``query`` is accepted for a uniform provider interface
but is not sent to the endpoint; the provider surfaces the most recent
harvested records, optionally filtered by ``year_from``/``year_to``.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

import httpx

from ...models import AcademicPaper

logger = logging.getLogger(__name__)

PRESETS: dict[str, str] = {
    "wbc": "https://www.wbc.poznan.pl/dlibra/oai-pmh-repository.xml",
    "jbc": "https://jbc.bj.uj.edu.pl/dlibra/oai-pmh-repository.xml",
    "fbc": "https://fbc.pionier.net.pl/oai-pmh-repository.xml",
}

DEFAULT_BASE_URL = PRESETS["wbc"]

_NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "fbc": "https://fbc.pionier.net.pl/schemas/fbc/1.0/",
}

_ABSTRACT_MAX_CHARS = 2000
_YEAR_RE = re.compile(r"\b\d{4}\b")


def _element_text(element: ET.Element | None) -> str | None:
    """Return stripped text of an XML element, or None when absent/empty."""
    if element is None or element.text is None:
        return None
    text = element.text.strip()
    return text or None


def _normalize_dlibra(record: ET.Element) -> AcademicPaper | None:
    """Normalize one OAI-PMH ``record`` element into AcademicPaper.

    Handles both oai_dc (dc: elements) and dace (fbc: elements, used by FBC).
    """
    header = record.find("oai:header", _NS)
    identifier = _element_text(header.find("oai:identifier", _NS)) if header is not None else None
    if not identifier:
        return None

    # dace format (FBC) uses fbc:title/fbc:creator/...; oai_dc uses dc:*.
    title = _element_text(record.find(".//dc:title", _NS))
    if not title:
        title = _element_text(record.find(".//fbc:title", _NS))
    if not title:
        return None

    authors = [
        creator.text.strip()
        for creator in record.findall(".//dc:creator", _NS)
        if creator.text and creator.text.strip()
    ]
    if not authors:
        authors = [
            creator.text.strip()
            for creator in record.findall(".//fbc:creator", _NS)
            if creator.text and creator.text.strip()
        ]

    year = None
    date_text = _element_text(record.find(".//dc:date", _NS))
    if not date_text:
        date_text = _element_text(record.find(".//fbc:date", _NS))
    if date_text:
        year_match = _YEAR_RE.search(date_text)
        if year_match:
            try:
                year = int(year_match.group(0))
            except ValueError:
                year = None

    venue = _element_text(record.find(".//dc:publisher", _NS))
    if not venue:
        venue = _element_text(record.find(".//fbc:publisher", _NS))

    description = _element_text(record.find(".//dc:description", _NS))
    if not description:
        description = _element_text(record.find(".//fbc:description", _NS))
    abstract = description[:_ABSTRACT_MAX_CHARS] if description else None

    url = None
    for identifier_el in record.findall(".//dc:identifier", _NS):
        value = _element_text(identifier_el)
        if value and value.startswith("http"):
            url = value
            break
    if not url:
        url = _element_text(record.find(".//fbc:url", _NS))
    if not url:
        # No resolvable link from this record — skip it.
        return None

    return AcademicPaper(
        title=title,
        authors=authors,
        abstract=abstract,
        year=year,
        venue=venue,
        citations=None,
        url=url or f"https://dlibra/{identifier}",
        pdf_url=None,
        source="dlibra",
        source_id=identifier,
        external_ids=None,
        fields_of_study=None,
        is_open_access=True,
        source_type="archive",
    )


async def search_dlibra(
    query: str,
    *,
    limit: int = 5,
    base_url: str | None = None,
    metadata_prefix: str = "oai_dc",
    year_from: int | None = None,
    year_to: int | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> list[AcademicPaper]:
    """Search dLibra digital libraries via OAI-PMH ListRecords.

    Returns an empty list on any error (fail gracefully, never raise).
    """
    if not query.strip():
        return []

    url = base_url or DEFAULT_BASE_URL
    params: dict[str, str] = {"verb": "ListRecords", "metadataPrefix": metadata_prefix}
    if year_from is not None:
        params["from"] = f"{year_from}-01-01"
    if year_to is not None:
        params["until"] = f"{year_to}-12-31"

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=90.0)
    try:
        response = await client.get(url, params=params)
        response.raise_for_status()
        root = ET.fromstring(response.text)

        # OAI-PMH errors come back as <error> elements inside the root.
        error_el = root.find("oai:error", _NS)
        if error_el is not None:
            code = error_el.get("code", "")
            if code == "cannotDisseminateFormat" and metadata_prefix != "dace":
                # Some instances (FBC) only expose the dace format.
                logger.info("dLibra %s rejects %s; retrying with dace", url, metadata_prefix)
                return await search_dlibra(
                    query,
                    limit=limit,
                    base_url=base_url,
                    metadata_prefix="dace",
                    year_from=year_from,
                    year_to=year_to,
                    http_client=http_client,
                )
            logger.warning("dLibra OAI-PMH error for %s: %s", url, code)
            return []

        papers: list[AcademicPaper] = []
        for record in root.findall(".//oai:record", _NS):
            paper = _normalize_dlibra(record)
            if paper is not None:
                papers.append(paper)
                if len(papers) >= limit:
                    break

        # A resumptionToken is deliberately NOT followed: ListRecords only
        # pages through very large harvests, and we surface a single bounded
        # page of records to keep requests cheap and latency predictable.
        return papers
    except Exception as exc:  # Provider errors are non-fatal.
        logger.warning("dLibra search failed for %s: %s", url, exc)
        return []
    finally:
        if owns_client:
            await client.aclose()
