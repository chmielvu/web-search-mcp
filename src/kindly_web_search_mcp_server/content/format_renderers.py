"""Bounded renderers for structured text and safe document formats."""

from __future__ import annotations

import html as html_lib
import io
import json
import re
import time
import tomllib
from collections import Counter
from email import policy
from email.parser import BytesParser
from typing import Any

from .extract import extract_content_as_markdown

MAX_CONFIG_CHARS = 1_000_000
MAX_JSONL_RECORDS = 200
MAX_JSONL_LINE_CHARS = 200_000
MAX_SUBTITLE_CUES = 2_000
MAX_SUBTITLE_OUTPUT_CHARS = 250_000
MAX_SVG_ELEMENTS = 5_000
MAX_SVG_TEXT_CHARS = 100_000
MAX_MHTML_PARTS = 200
MAX_MHTML_PART_BYTES = 2_000_000

RenderedContent = tuple[str, dict[str, object], list[dict[str, object]]]


def _source_header(title: str, source_url: str) -> str:
    return f"# {title}\n\n**Source:** {source_url}\n"


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    """Convert parser values to bounded JSON-safe primitives."""
    if depth >= 6:
        return "<nested value omitted>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item, depth=depth + 1) for key, item in list(value.items())[:200]
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, depth=depth + 1) for item in list(value)[:200]]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def _parse_error_markdown(
    fmt: str,
    source_url: str,
    message: str,
    source: str,
) -> RenderedContent:
    bounded_source = source[:MAX_CONFIG_CHARS]
    markdown = (
        _source_header(f"{fmt.upper()} Document", source_url)
        + f"\n_Parsing failed: {message[:300]}_\n\n"
        + f"```{fmt}\n{bounded_source}\n```"
    )
    return markdown, {"format": fmt, "parse_error": message[:300]}, []


def _structured_value_markdown(
    fmt: str,
    source_url: str,
    value: Any,
    *,
    metadata: dict[str, object] | None = None,
) -> RenderedContent:
    rendered = json.dumps(_json_safe(value), ensure_ascii=False, indent=2)
    if len(rendered) > MAX_CONFIG_CHARS:
        rendered = rendered[:MAX_CONFIG_CHARS].rstrip() + "\n..."
    body = (
        _source_header(f"{fmt.upper()} Document", source_url)
        + "\n## Parsed structure\n\n"
        + f"```json\n{rendered}\n```"
    )
    result_metadata: dict[str, object] = {"format": fmt}
    if metadata:
        result_metadata.update(metadata)
    return body, result_metadata, []


def render_jsonl_markdown(text: str, source_url: str) -> RenderedContent:
    """Render newline-delimited JSON without loading unbounded records."""
    records: list[Any] = []
    errors: list[int] = []
    fields: set[str] = set()
    truncated = False
    for line_number, line in enumerate(text[:MAX_CONFIG_CHARS].splitlines(), start=1):
        if not line.strip():
            continue
        if len(line) > MAX_JSONL_LINE_CHARS:
            errors.append(line_number)
            continue
        if len(records) >= MAX_JSONL_RECORDS:
            truncated = True
            break
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            errors.append(line_number)
            continue
        records.append(value)
        if isinstance(value, dict):
            fields.update(str(key) for key in value.keys())

    rendered = json.dumps(_json_safe(records), ensure_ascii=False, indent=2)
    markdown = (
        _source_header("JSON Lines", source_url)
        + f"\n_Parsed records: {len(records)}_\n"
        + (f"_Fields: {', '.join(sorted(fields)[:100])}_\n" if fields else "")
        + (f"_Malformed lines: {', '.join(map(str, errors[:50]))}_\n" if errors else "")
        + ("_Record limit reached._\n" if truncated else "")
        + f"\n## Sample records\n\n```json\n{rendered}\n```"
    )
    metadata: dict[str, object] = {
        "format": "jsonl",
        "record_count": len(records),
        "fields": sorted(fields)[:100],
        "parse_errors": errors[:50],
        "truncated": truncated,
    }
    return markdown[:MAX_CONFIG_CHARS], metadata, []


def render_yaml_markdown(text: str, source_url: str) -> RenderedContent:
    """Safely parse bounded YAML documents with PyYAML's safe loader."""
    if len(text) > MAX_CONFIG_CHARS:
        return _parse_error_markdown("yaml", source_url, "input exceeds size limit", text)
    try:
        import yaml

        documents: list[Any] = []
        loader = yaml.safe_load_all(text)
        for value in loader:
            documents.append(value)
            if len(documents) >= 20:
                break
    except Exception as exc:
        return _parse_error_markdown("yaml", source_url, str(exc), text)
    value: Any = documents[0] if len(documents) == 1 else documents
    return _structured_value_markdown(
        "yaml",
        source_url,
        value,
        metadata={"document_count": len(documents)},
    )


def render_toml_markdown(text: str, source_url: str) -> RenderedContent:
    """Parse TOML with the Python standard library parser."""
    if len(text) > MAX_CONFIG_CHARS:
        return _parse_error_markdown("toml", source_url, "input exceeds size limit", text)
    try:
        value = tomllib.loads(text)
    except Exception as exc:
        return _parse_error_markdown("toml", source_url, str(exc), text)
    return _structured_value_markdown("toml", source_url, value)


def render_rtf_markdown(text: str, source_url: str) -> RenderedContent:
    """Convert RTF control words to plain text using the bounded striprtf parser."""
    if len(text) > MAX_CONFIG_CHARS:
        return _parse_error_markdown("rtf", source_url, "input exceeds size limit", text)
    try:
        from striprtf.striprtf import rtf_to_text
    except ImportError as exc:  # pragma: no cover - clean-install dependency gate
        raise RuntimeError("striprtf is required for RTF conversion") from exc
    try:
        plain = rtf_to_text(text)
    except Exception as exc:
        return _parse_error_markdown("rtf", source_url, str(exc), text)
    markdown = _source_header("RTF Document", source_url) + "\n" + plain.strip()
    return markdown[:MAX_CONFIG_CHARS], {"format": "rtf"}, []


_TIMESTAMP_RE = re.compile(
    r"^\s*((?:\d{2}:)?\d{2}:\d{2}[\.,]\d{3})\s*-->\s*"
    r"((?:\d{2}:)?\d{2}:\d{2}[\.,]\d{3})(?:\s+.*)?$"
)


def _subtitle_cues(text: str) -> list[tuple[str, str, str]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    blocks = re.split(r"\n\s*\n", normalized)
    cues: list[tuple[str, str, str]] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines()]
        if not lines or lines[0].upper() in {"WEBVTT", "NOTE", "STYLE", "REGION"}:
            continue
        timestamp_index = next(
            (index for index, line in enumerate(lines) if _TIMESTAMP_RE.match(line)),
            None,
        )
        if timestamp_index is None:
            continue
        match = _TIMESTAMP_RE.match(lines[timestamp_index])
        if match is None:
            continue
        caption = " ".join(lines[timestamp_index + 1 :]).strip()
        caption = html_lib.unescape(re.sub(r"<[^>]+>", "", caption))
        if caption:
            cues.append((match.group(1), match.group(2), caption))
        if len(cues) >= MAX_SUBTITLE_CUES:
            break
    return cues


def render_subtitle_markdown(text: str, source_url: str, fmt: str) -> RenderedContent:
    cues = _subtitle_cues(text)
    lines = [_source_header(f"{fmt.upper()} Transcript", source_url).rstrip(), ""]
    for start, end, caption in cues:
        lines.extend([f"## {start} → {end}", caption, ""])
        if sum(len(line) + 1 for line in lines) >= MAX_SUBTITLE_OUTPUT_CHARS:
            break
    if not cues:
        lines.append("_No valid subtitle cues found._")
    markdown = "\n".join(lines).strip()
    return markdown, {"format": fmt, "cue_count": len(cues)}, []


def render_svg_markdown(text: str, source_url: str) -> RenderedContent:
    """Extract safe SVG text and bounded structural metadata without rendering it."""
    if len(text) > MAX_CONFIG_CHARS:
        return _parse_error_markdown("svg", source_url, "input exceeds size limit", text)
    if re.search(r"<!\s*(?:doctype|entity)", text, flags=re.IGNORECASE):
        return _parse_error_markdown("svg", source_url, "DOCTYPE and ENTITY are blocked", text)
    try:
        from defusedxml import ElementTree

        root = ElementTree.fromstring(text)
    except Exception as exc:
        return _parse_error_markdown("svg", source_url, str(exc), text)

    counts: Counter[str] = Counter()
    text_nodes: list[str] = []
    for index, element in enumerate(root.iter()):
        if index >= MAX_SVG_ELEMENTS:
            break
        local = str(element.tag).rsplit("}", 1)[-1].lower()
        counts[local] += 1
        if local in {"title", "desc", "text", "tspan"} and element.text:
            text_nodes.append(" ".join(element.text.split()))
        for attribute in ("aria-label", "title"):
            value = element.attrib.get(attribute)
            if value:
                text_nodes.append(" ".join(value.split()))
    text_nodes = list(dict.fromkeys(text_nodes))
    text_body = "\n".join(f"- {value}" for value in text_nodes)[:MAX_SVG_TEXT_CHARS]
    attributes = {
        key: root.attrib[key]
        for key in ("width", "height", "viewBox", "aria-label")
        if key in root.attrib
    }
    markdown = (
        _source_header("SVG Graphic", source_url)
        + f"\n**Elements:** {sum(counts.values())}\n"
        + f"**Element types:** {', '.join(f'{name}={count}' for name, count in counts.most_common(20))}\n"
        + (
            f"**Root attributes:** `{json.dumps(attributes, ensure_ascii=False)}`\n"
            if attributes
            else ""
        )
        + (
            f"\n## Accessible text\n{text_body}\n"
            if text_body
            else "\n_No accessible text nodes found._\n"
        )
    )
    return (
        markdown[:MAX_CONFIG_CHARS],
        {
            "format": "svg",
            "element_count": sum(counts.values()),
            "text_node_count": len(text_nodes),
            "attributes": attributes,
        },
        [],
    )


def render_mhtml_markdown(body: bytes, source_url: str) -> tuple[str, dict[str, object]]:
    """Extract the first bounded HTML/text MIME part without fetching resources."""
    if len(body) > MAX_CONFIG_CHARS:
        raise ValueError("MHTML input exceeds size limit")
    message = BytesParser(policy=policy.default).parsebytes(body)
    html_text: str | None = None
    plain_text: str | None = None
    parts_seen = 0
    for part in message.walk():
        if part.is_multipart():
            continue
        parts_seen += 1
        if parts_seen > MAX_MHTML_PARTS:
            break
        raw_payload: object = part.get_payload(decode=True)
        if isinstance(raw_payload, bytes):
            payload = raw_payload
        elif isinstance(raw_payload, str):
            payload = raw_payload.encode(part.get_content_charset() or "utf-8", errors="replace")
        else:
            payload = b""
        if len(payload) > MAX_MHTML_PART_BYTES:
            continue
        charset = part.get_content_charset() or "utf-8"
        decoded = payload.decode(charset, errors="replace")
        content_type = part.get_content_type().lower()
        if content_type == "text/html" and html_text is None:
            html_text = decoded
        elif content_type == "text/plain" and plain_text is None:
            plain_text = decoded
    if html_text is not None:
        markdown = extract_content_as_markdown(html_text, url=source_url)
        chosen = "text/html"
    elif plain_text is not None:
        markdown = plain_text.strip()
        chosen = "text/plain"
    else:
        markdown = "_No supported text part found._"
        chosen = None
    return (
        _source_header("MHTML Document", source_url) + "\n" + markdown,
        {"format": "mhtml", "part_count": parts_seen, "selected_part": chosen},
    )


MAX_COLUMNAR_BYTES = 5 * 1024 * 1024
MAX_COLUMNAR_COLUMNS = 50
MAX_COLUMNAR_SAMPLE_ROWS = 100
MAX_COLUMNAR_RENDER_CHARS = 250_000
MAX_COLUMNAR_PARSE_SECONDS = 10.0


def _columnar_budget(started: float) -> None:
    if time.monotonic() - started > MAX_COLUMNAR_PARSE_SECONDS:
        raise TimeoutError("columnar conversion exceeded its parse-time budget")


def _escape_cell(value: Any) -> str:
    return str(_json_safe(value, depth=1)).replace("|", "\\|").replace("\n", " ")


def _arrow_sample_markdown(
    fmt: str,
    source_url: str,
    schema: Any,
    row_count: int | None,
    records: list[dict[str, Any]],
    *,
    parse_seconds: float,
) -> tuple[str, dict[str, object]]:
    fields = list(schema) if schema is not None else []
    fields = fields[:MAX_COLUMNAR_COLUMNS]
    names = [getattr(field, "name", str(field)) for field in fields]
    types = [str(getattr(field, "type", "unknown")) for field in fields]
    markdown = _source_header(f"{fmt.upper()} Dataset", source_url)
    if row_count is not None:
        markdown += f"\n**Rows:** {row_count}\n"
    markdown += f"**Columns:** {len(names)}\n\n## Schema\n\n| Column | Type |\n| --- | --- |\n"
    markdown += "".join(
        f"| {_escape_cell(name)} | {_escape_cell(kind)} |\n" for name, kind in zip(names, types)
    )
    if records:
        markdown += "\n## Sample rows\n\n"
        sample_names = names[:20]
        markdown += "| " + " | ".join(sample_names) + " |\n"
        markdown += "| " + " | ".join("---" for _ in sample_names) + " |\n"
        for record in records[:MAX_COLUMNAR_SAMPLE_ROWS]:
            markdown += (
                "| " + " | ".join(_escape_cell(record.get(name)) for name in sample_names) + " |\n"
            )
    metadata = {
        "format": fmt,
        "row_count": row_count,
        "column_count": len(names),
        "columns": [{"name": name, "type": kind} for name, kind in zip(names, types)],
        "sample_row_count": len(records),
        "bounded": True,
        "parse_seconds": round(parse_seconds, 4),
    }
    return markdown[:MAX_COLUMNAR_RENDER_CHARS], metadata


def render_columnar_markdown(
    body: bytes,
    source_url: str,
    fmt: str,
) -> tuple[str, dict[str, object]]:
    """Read schema and a bounded sample from Parquet, Arrow, or Feather."""
    started = time.monotonic()
    if len(body) > MAX_COLUMNAR_BYTES:
        raise ValueError(f"columnar input exceeds {MAX_COLUMNAR_BYTES} bytes")
    try:
        import pyarrow as pa
    except ImportError as exc:  # pragma: no cover - clean-install dependency gate
        raise RuntimeError("pyarrow is required for columnar conversion") from exc

    row_count: int | None = None
    if fmt == "parquet":
        import pyarrow.parquet as pq

        parquet_file = pq.ParquetFile(io.BytesIO(body))
        schema = parquet_file.schema_arrow
        columns = schema.names[:MAX_COLUMNAR_COLUMNS]
        batch = next(
            parquet_file.iter_batches(
                batch_size=MAX_COLUMNAR_SAMPLE_ROWS,
                columns=columns,
                use_threads=False,
            ),
            None,
        )
        records = batch.to_pylist() if batch is not None else []
        row_count = parquet_file.metadata.num_rows if parquet_file.metadata else None
    elif fmt == "feather":
        import pyarrow.feather as feather

        try:
            reader = pa.ipc.open_file(io.BytesIO(body))
            schema = reader.schema
            batch = reader.get_batch(0) if reader.num_record_batches else None
            records = batch.to_pylist() if batch is not None else []
        except Exception:
            table = feather.read_table(io.BytesIO(body), columns=None)
            schema = table.schema
            row_count = table.num_rows
            records = (
                table.select(schema.names[:MAX_COLUMNAR_COLUMNS])
                .slice(0, MAX_COLUMNAR_SAMPLE_ROWS)
                .to_pylist()
            )
    else:
        try:
            reader = pa.ipc.open_file(io.BytesIO(body))
            schema = reader.schema
            batch = reader.get_batch(0) if reader.num_record_batches else None
        except Exception:
            reader = pa.ipc.open_stream(io.BytesIO(body))
            schema = reader.schema
            batch = reader.read_next_batch()
        records = batch.to_pylist() if batch is not None else []
    _columnar_budget(started)
    return _arrow_sample_markdown(
        fmt,
        source_url,
        schema,
        row_count,
        records,
        parse_seconds=time.monotonic() - started,
    )
