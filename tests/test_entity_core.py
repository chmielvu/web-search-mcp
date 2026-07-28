"""Tests for pure-Python, source-grounded entity processing."""

from __future__ import annotations

import pytest

from kindly_web_search_mcp_server.entity.default_schema import (
    DEFAULT_CONTENT_LABELS,
    DEFAULT_QUERY_LABELS,
)
from kindly_web_search_mcp_server.entity.models import EntitySpan
from kindly_web_search_mcp_server.entity.chunk import chunk_text
from kindly_web_search_mcp_server.entity.postprocess import postprocess_entities


def test_label_schema_presence():
    assert isinstance(DEFAULT_QUERY_LABELS, dict)
    for label in (
        "package",
        "version",
        "repo_ref",
        "api_function",
        "error_class",
        "language",
        "platform",
        "provider",
    ):
        assert label in DEFAULT_QUERY_LABELS
    assert set(DEFAULT_QUERY_LABELS).issubset(DEFAULT_CONTENT_LABELS)
    assert "person" in DEFAULT_CONTENT_LABELS


def test_chunk_text_basic_offsets_and_content():
    text = (
        "First paragraph with some content here.\n\n"
        "Second paragraph continues the text for chunking tests. "
        "We need enough characters to cross chunk boundaries reliably.\n\n" + ("filler text " * 100)
    )
    chunks = chunk_text(text, chunk_size=120, overlap=20)
    assert len(chunks) >= 2
    assert chunks[0][0] == 0
    assert 90 < chunks[1][0] < 140
    for start, chunk in chunks:
        assert 0 <= start < len(text)
        assert text[start : start + len(chunk)] == chunk
        assert len(chunk) > 0


def test_chunk_text_covers_source_after_early_boundary_cut():
    text = "A" * 200 + "\n\n" + "B" * 200 + "\n\n" + "C" * 200
    chunks = chunk_text(text, chunk_size=400, overlap=50)

    covered = {index for start, chunk in chunks for index in range(start, start + len(chunk))}
    assert covered == set(range(len(text)))


def test_offset_correction_preserves_source_surface_text():
    chunk_offset = 18
    local_entities = [
        EntitySpan(text="FastAPI", label="package", start=8, end=15, confidence=0.92),
        EntitySpan(text="v0.100.0", label="version", start=16, end=24, confidence=0.88),
    ]
    corrected = [
        entity.model_copy(
            update={
                "start": entity.start + chunk_offset if entity.start is not None else None,
                "end": entity.end + chunk_offset if entity.end is not None else None,
            }
        )
        for entity in local_entities
    ]
    final = postprocess_entities(corrected)
    assert any(entity.start == 26 and entity.label == "package" for entity in final)
    assert any(entity.text == "v0.100.0" for entity in final)


def test_same_surface_text_at_distinct_offsets_is_retained():
    entities = [
        EntitySpan(text="FastAPI", label="package", start=0, end=7, confidence=0.91),
        EntitySpan(text="FastAPI", label="package", start=20, end=27, confidence=0.88),
    ]
    output = postprocess_entities(entities)
    assert [(entity.start, entity.end) for entity in output] == [(0, 7), (20, 27)]


def test_overlap_merges_same_label_and_keeps_highest_confidence():
    entities = [
        EntitySpan(text="pydantic", label="package", start=100, end=108, confidence=0.71),
        EntitySpan(text="pydantic", label="package", start=105, end=113, confidence=0.89),
        EntitySpan(text="SQLAlchemy", label="package", start=200, end=210, confidence=0.65),
    ]
    output = postprocess_entities(entities)
    packages = [entity for entity in output if entity.label == "package"]
    assert len(packages) == 2
    pydantic = next(entity for entity in packages if entity.text.lower() == "pydantic")
    assert pydantic.confidence == pytest.approx(0.89)


def test_version_surface_text_is_not_rewritten():
    output = postprocess_entities(
        [EntitySpan(text="v2.14.5", label="version", start=0, end=7, confidence=0.9)]
    )
    assert output[0].text == "v2.14.5"


def test_repo_ref_validation_filters_invalid():
    output = postprocess_entities(
        [
            EntitySpan(text="owner/repo", label="repo_ref", start=0, end=10, confidence=0.95),
            EntitySpan(text="owner/repo#123", label="repo_ref", start=15, end=27, confidence=0.9),
            EntitySpan(
                text="not valid/ref here", label="repo_ref", start=30, end=48, confidence=0.7
            ),
            EntitySpan(text="user/project", label="repo_ref", start=50, end=62, confidence=0.85),
        ]
    )
    refs = {entity.text for entity in output if entity.label == "repo_ref"}
    assert refs == {"owner/repo", "owner/repo#123", "user/project"}
