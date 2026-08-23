from __future__ import annotations

from kindly_web_search_mcp_server.content.typed_content import (
    SUPPORTED_TYPED_FORMATS,
    detect_content_format,
    render_typed_content,
)


def test_detects_jsonl_yaml_and_toml_by_extension_and_mime() -> None:
    assert detect_content_format("https://example.com/events.jsonl", "text/plain", "{}") == "jsonl"
    assert (
        detect_content_format("https://example.com/config", "application/yaml", "key: value")
        == "yaml"
    )
    assert (
        detect_content_format("https://example.com/config", "application/toml", "key = 1") == "toml"
    )
    assert {"jsonl", "yaml", "toml"}.issubset(SUPPORTED_TYPED_FORMATS)


def test_renders_jsonl_records_and_reports_bad_lines() -> None:
    markdown, metadata, links = render_typed_content(
        "jsonl",
        '{"id": 1, "name": "Ada"}\nnot-json\n{"id": 2, "name": "Grace"}\n',
        "https://example.com/events.jsonl",
    )

    assert "JSON Lines" in markdown
    assert '"Ada"' in markdown
    assert metadata["record_count"] == 2
    assert metadata["parse_errors"] == [2]
    assert links == []


def test_renders_yaml_with_safe_structured_values() -> None:
    markdown, metadata, _ = render_typed_content(
        "yaml",
        "service:\n  name: search\n  replicas: 2\n",
        "https://example.com/config.yaml",
    )

    assert "Parsed structure" in markdown
    assert '"service"' in markdown
    assert '"replicas": 2' in markdown
    assert metadata["format"] == "yaml"


def test_renders_toml_with_structured_values() -> None:
    markdown, metadata, _ = render_typed_content(
        "toml",
        '[project]\nname = "web-search-mcp"\nrequires-python = ">=3.12"\n',
        "https://example.com/pyproject.toml",
    )

    assert "Parsed structure" in markdown
    assert '"web-search-mcp"' in markdown
    assert metadata["format"] == "toml"
