"""Typed content routing for JSON, XML/RSS/Atom, and CSV/TSV responses."""

from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse


_JSON_MIMES = {"application/json", "text/json"}
_RSS_MIMES = {"application/rss+xml", "application/atom+xml", "application/rdf+xml"}
_XML_MIMES = {"application/xml", "text/xml"}
_CSV_MIMES = {"text/csv", "application/csv"}
_MAX_CSV_ROWS = 500


def _mime(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _local_name(tag: str) -> str:
    return re.sub(r"^\{[^}]+\}", "", tag).lower()


def detect_content_format(url: str, content_type: str | None, text: str) -> str | None:
    """Return a typed format or ``None`` when the response is HTML/text."""
    mime = _mime(content_type)
    path = urlparse(url).path.lower()

    if mime in _JSON_MIMES or path.endswith(".json"):
        return "json"
    if mime in _RSS_MIMES:
        return "rss" if mime != "application/atom+xml" else "atom"
    if mime in _CSV_MIMES or path.endswith(".csv"):
        return "csv"
    if path.endswith(".tsv"):
        return "tsv"
    if mime in _XML_MIMES or path.endswith((".xml", ".rss", ".atom")):
        stripped = (text or "").lstrip().lower()[:500]
        if re.search(r"<\s*(?:[\w.-]+:)?rss(?:\s|>)", stripped):
            return "rss"
        if re.search(r"<\s*(?:[\w.-]+:)?feed(?:\s|>)", stripped):
            return "atom"
        return "xml"

    stripped = (text or "").lstrip()
    if stripped.startswith(("{", "[")):
        try:
            json.loads(stripped)
        except Exception:
            pass
        else:
            return "json"
    return None


def _json_markdown(text: str) -> tuple[str, dict[str, object], list[dict[str, object]]]:
    try:
        value = json.loads(text)
        rendered = json.dumps(value, ensure_ascii=False, indent=2)
        metadata: dict[str, object] = {
            "format": "json",
            "json_type": type(value).__name__,
        }
    except (TypeError, ValueError, json.JSONDecodeError):
        rendered = text
        metadata = {"format": "json", "parse_error": True}
    return f"```json\n{rendered}\n```", metadata, []


def _child_text(element: ET.Element, names: tuple[str, ...]) -> str:
    for child in element:
        if _local_name(child.tag) in names:
            return " ".join((child.text or "").split())
    return ""


def _child_link(element: ET.Element, atom: bool) -> str:
    for child in element:
        if _local_name(child.tag) != "link":
            continue
        href = (child.attrib.get("href") or "").strip()
        if atom and href:
            return href
        text = " ".join((child.text or "").split())
        if text:
            return text
    return ""


def _feed_markdown(
    text: str,
    source_url: str,
    fmt: str,
) -> tuple[str, dict[str, object], list[dict[str, object]]]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return f"```xml\n{text}\n```", {"format": fmt, "parse_error": True}, []

    atom = fmt == "atom" or _local_name(root.tag) == "feed"
    container = root
    if _local_name(root.tag) == "rss":
        container = next(
            (child for child in root if _local_name(child.tag) == "channel"),
            root,
        )
    item_name = "entry" if atom else "item"
    title = _child_text(container, ("title",))
    description = _child_text(container, ("subtitle", "description"))
    lines: list[str] = []
    if title:
        lines.append(f"# {title}")
    if description:
        lines.extend(["", description])

    links: list[dict[str, object]] = []
    item_count = 0
    for item in container:
        if _local_name(item.tag) != item_name:
            continue
        item_count += 1
        item_title = _child_text(item, ("title",))
        item_url = _child_link(item, atom)
        item_description = _child_text(item, ("summary", "content", "description"))
        published = _child_text(item, ("published", "updated", "pubdate", "date"))
        heading = item_title or item_url or f"Entry {item_count}"
        lines.extend(["", f"## {heading}"])
        if published:
            lines.append(f"Date: {published}")
        if item_url:
            lines.append(f"Source: {item_url}")
            parsed = urlparse(item_url)
            links.append(
                {
                    "url": item_url,
                    "text": item_title or item_url,
                    "domain": parsed.netloc.lower() or None,
                    "internal": parsed.netloc.lower() == urlparse(source_url).netloc.lower(),
                }
            )
        if item_description:
            lines.extend(["", item_description])

    metadata = {"format": fmt, "title": title, "item_count": item_count}
    return "\n".join(lines).strip() or f"# {fmt.upper()} feed\n\nSource: {source_url}", metadata, links


def _csv_markdown(text: str, source_url: str, delimiter: str) -> tuple[str, dict[str, object], list[dict[str, object]]]:
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows: list[list[str]] = []
    for index, row in enumerate(reader):
        if index >= _MAX_CSV_ROWS:
            break
        cleaned = [cell.strip().replace("\n", " ").replace("|", "\\|") for cell in row]
        if any(cleaned):
            rows.append(cleaned)
    if not rows:
        return f"# Data Table\n\nSource: {source_url}\n\n_Empty table_", {"format": "tsv" if delimiter == "\t" else "csv", "row_count": 0}, []

    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    lines = [
        f"# Data Table\nSource: {source_url}",
        "",
        "| " + " | ".join(normalized[0]) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in normalized[1:])
    if len(rows) >= _MAX_CSV_ROWS:
        lines.extend(["", f"_Note: Table truncated to first {_MAX_CSV_ROWS} rows_"])
    return "\n".join(lines), {"format": "tsv" if delimiter == "\t" else "csv", "row_count": len(rows) - 1}, []


def render_typed_content(
    fmt: str,
    text: str,
    source_url: str,
) -> tuple[str, dict[str, object], list[dict[str, object]]]:
    """Render a detected typed response to LLM-ready Markdown."""
    if fmt == "json":
        return _json_markdown(text)
    if fmt in {"rss", "atom", "xml"}:
        if fmt == "xml":
            return f"```xml\n{text}\n```", {"format": "xml"}, []
        return _feed_markdown(text, source_url, fmt)
    if fmt in {"csv", "tsv"}:
        return _csv_markdown(text, source_url, "\t" if fmt == "tsv" else ",")
    raise ValueError(f"Unsupported typed content format: {fmt}")
