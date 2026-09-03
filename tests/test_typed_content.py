from __future__ import annotations

import json
from pathlib import Path

from kindly_web_search_mcp_server.content.artifact import ContentArtifact
from kindly_web_search_mcp_server.content.typed_content import (
    relabel_typed_artifact,
    strip_jina_frontmatter,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fetch_overhaul"


def test_relabel_jina_github_json_fixture() -> None:
    markdown = (_FIXTURES / "F-t1-jina-json.md").read_text(encoding="utf-8")
    artifact = ContentArtifact(
        input_url="https://api.github.com/repos/octocat/Hello-World",
        normalized_url="https://api.github.com/repos/octocat/Hello-World",
        fetched_url="https://api.github.com/repos/octocat/Hello-World",
        status="success",
        source_type="html",
        fetch_backend="jina_reader",
        markdown=markdown,
        content_type="text/markdown",
    )
    relabeled = relabel_typed_artifact(artifact)
    assert relabeled.source_type == "json"
    assert relabeled.fetch_backend == "typed_content"
    assert relabeled.origin_backend == "jina_reader"
    stripped = strip_jina_frontmatter(markdown).lstrip()
    parsed = json.loads(stripped)
    assert parsed["full_name"] == "octocat/Hello-World"


def test_relabel_without_strip_does_not_claim_parse_error_json() -> None:
    wrapped = "---\nurl: https://example.com\n---\n\nnot-json"
    artifact = ContentArtifact(
        input_url="https://example.com",
        normalized_url="https://example.com",
        fetched_url="https://example.com",
        status="success",
        source_type="html",
        fetch_backend="jina_reader",
        markdown=wrapped,
        content_type="text/markdown",
    )
    relabeled = relabel_typed_artifact(artifact)
    assert relabeled.source_type == "html"
    assert (relabeled.metadata or {}).get("parse_error") is None
