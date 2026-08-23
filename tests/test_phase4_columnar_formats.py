from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any

import pytest

from kindly_web_search_mcp_server.content.format_renderers import render_columnar_markdown
from kindly_web_search_mcp_server.content.safe_fetch import _sniff_doc_type

pa = pytest.importorskip("pyarrow")


def _table() -> Any:
    return pa.table({"id": [1, 2], "name": ["Ada", "Grace"]})


def _parquet_bytes(table: Any) -> bytes:
    import pyarrow.parquet as pq

    sink = io.BytesIO()
    pq.write_table(table, sink)
    return sink.getvalue()


def _arrow_bytes(table: Any) -> bytes:
    sink = pa.BufferOutputStream()
    writer = pa.ipc.new_file(sink, table.schema)
    writer.write_table(table)
    writer.close()
    return sink.getvalue().to_pybytes()


def _feather_bytes(table: Any) -> bytes:
    import pyarrow.feather as feather

    sink = io.BytesIO()
    feather.write_feather(table, sink)
    return sink.getvalue()


@pytest.mark.parametrize(
    ("fmt", "suffix", "builder"),
    [
        ("parquet", ".parquet", _parquet_bytes),
        ("arrow", ".arrow", _arrow_bytes),
        ("feather", ".feather", _feather_bytes),
    ],
)
def test_columnar_renderer_returns_schema_and_bounded_samples(
    fmt: str,
    suffix: str,
    builder: Callable[[Any], bytes],
) -> None:
    body = builder(_table())

    markdown, metadata = render_columnar_markdown(body, f"https://example.com/data{suffix}", fmt)

    assert "Schema" in markdown
    assert "name" in markdown
    assert metadata["format"] == fmt
    assert metadata["sample_row_count"] <= 100
    assert metadata["bounded"] is True


def test_columnar_sniffing_uses_extension() -> None:
    assert (
        _sniff_doc_type("application/octet-stream", "https://example.com/data.parquet", b"")
        == "parquet"
    )
    assert (
        _sniff_doc_type("application/octet-stream", "https://example.com/data.arrow", b"")
        == "arrow"
    )
    assert (
        _sniff_doc_type("application/octet-stream", "https://example.com/data.feather", b"")
        == "feather"
    )


def test_columnar_renderer_rejects_oversized_body() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        render_columnar_markdown(
            b"x" * (5 * 1024 * 1024 + 1),
            "https://example.com/data.parquet",
            "parquet",
        )
