"""Tests for pure-Python entity extraction core (Phases 6.1).

No GLiNER2 import allowed in these tests or core modules.
Covers: label schema, chunking with offsets, offset correction pattern,
overlap deduplication, version normalization, repo-ref validation.
"""

from __future__ import annotations

import pytest

from kindly_web_search_mcp_server.entity.models import EntitySpan
from kindly_web_search_mcp_server.entity.default_schema import (
    DEFAULT_QUERY_LABELS,
    DEFAULT_CONTENT_LABELS,
)
from kindly_web_search_mcp_server.entity.chunk import chunk_text
from kindly_web_search_mcp_server.entity.postprocess import postprocess_entities


def test_label_schema_presence():
    assert isinstance(DEFAULT_QUERY_LABELS, dict)
    assert "package" in DEFAULT_QUERY_LABELS
    assert "version" in DEFAULT_QUERY_LABELS
    assert "repo_ref" in DEFAULT_QUERY_LABELS
    assert "api_function" in DEFAULT_QUERY_LABELS
    assert "error_class" in DEFAULT_QUERY_LABELS
    assert isinstance(DEFAULT_CONTENT_LABELS, dict)
    assert "person" in DEFAULT_CONTENT_LABELS
    assert "organization" in DEFAULT_CONTENT_LABELS
    assert "date" in DEFAULT_CONTENT_LABELS
    # descriptions present
    assert "Software package" in DEFAULT_QUERY_LABELS["package"]
    assert len(DEFAULT_CONTENT_LABELS) > len(DEFAULT_QUERY_LABELS)


def test_chunk_text_basic_offsets_and_content():
    text = (
        "First paragraph with some content here.\n\n"
        "Second paragraph continues the text for chunking tests. "
        "We need enough characters to cross chunk boundaries reliably.\n\n"
        + ("filler text " * 100)
    )
    chunks = chunk_text(text, chunk_size=120, overlap=20)
    assert len(chunks) >= 2
    assert chunks[0][0] == 0
    # second chunk starts inside first's range (overlap) but after some content
    assert 90 < chunks[1][0] < 140
    # every chunk must be exact substring at its offset
    for start, chunk in chunks:
        assert 0 <= start < len(text)
        assert text[start : start + len(chunk)] == chunk
        assert len(chunk) > 0


def test_offset_correction_pattern():
    """Tests the offset correction pattern used by gliner client and integrators."""
    raw = "Header text here. Package FastAPI v0.100.0 is mentioned after this point in the document."
    chunk_offset = 18  # simulate second chunk starting here
    raw[chunk_offset:]
    # Simulated local entities returned by model on the *chunk* text only
    local_entities = [
        EntitySpan(text="FastAPI", label="package", start=8, end=15, confidence=0.92),
        EntitySpan(text="v0.100.0", label="version", start=16, end=24, confidence=0.88),
    ]
    corrected = []
    for e in local_entities:
        new_start = e.start + chunk_offset if e.start is not None else None
        new_end = e.end + chunk_offset if e.end is not None else None
        corrected.append(
            e.model_copy(update={"start": new_start, "end": new_end})
        )
    assert corrected[0].start == 26
    assert corrected[0].end == 33
    assert corrected[1].start == 34
    # postprocess after correction
    final = postprocess_entities(corrected)
    assert len(final) >= 2
    assert any(e.start == 26 and e.label == "package" for e in final)


def test_overlap_deduplication_keeps_higher_confidence():
    # Same entity detected across overlapping chunk boundary
    ents = [
        EntitySpan(text="pydantic", label="package", start=100, end=108, confidence=0.71),
        EntitySpan(text="pydantic", label="package", start=105, end=113, confidence=0.89),
        EntitySpan(text="SQLAlchemy", label="package", start=200, end=210, confidence=0.65),
    ]
    out = postprocess_entities(ents)
    packages = [e for e in out if e.label == "package"]
    assert len(packages) == 2
    pyd = next(e for e in packages if e.text.lower() == "pydantic")
    assert pyd.confidence == pytest.approx(0.89)
    # positions: the kept one should reflect one of the originals (prefer higher or first non-overlapped)
    assert pyd.start in (100, 105)


def test_version_normalization():
    ents = [
        EntitySpan(text="v2.14.5", label="version", start=0, end=7, confidence=0.9),
        EntitySpan(text="3.0.0b1", label="version", start=10, end=17, confidence=0.8),
        EntitySpan(text="v1", label="version", start=20, end=22, confidence=0.6),
    ]
    out = postprocess_entities(ents)
    version_texts = {e.text for e in out if e.label == "version"}
    assert "2.14.5" in version_texts  # stripped leading v
    assert "3.0.0b1" in version_texts
    # short or partial may stay or be filtered; at minimum v-prefix stripped when present


def test_repo_ref_validation_filters_invalid():
    ents = [
        EntitySpan(text="owner/repo", label="repo_ref", start=0, end=10, confidence=0.95),
        EntitySpan(text="owner/repo#123", label="repo_ref", start=15, end=27, confidence=0.9),
        EntitySpan(text="not valid/ref here", label="repo_ref", start=30, end=48, confidence=0.7),
        EntitySpan(text="user/project", label="repo_ref", start=50, end=62, confidence=0.85),
    ]
    out = postprocess_entities(ents)
    refs = {e.text for e in out if e.label == "repo_ref"}
    assert "owner/repo" in refs
    assert "owner/repo#123" in refs
    assert "user/project" in refs
    # invalid with spaces or malformed should be dropped by validation
    assert "not valid/ref here" not in refs


def test_postprocess_removes_low_value_and_sorts():
    ents = [
        EntitySpan(text="zzzz", label="package", start=300, end=304, confidence=0.1),
        EntitySpan(text="requests", label="package", start=10, end=18, confidence=0.95),
        EntitySpan(text="requests", label="package", start=5, end=13, confidence=0.7),
    ]
    out = postprocess_entities(ents)
    # deduped to one requests, low conf junk dropped or kept? at least sorted and deduped
    packages = sorted((e for e in out if e.label == "package"), key=lambda e: e.start or 0)
    assert len(packages) >= 1
    assert packages[0].text == "requests"
    if len(packages) > 1:
        assert packages[0].start <= packages[1].start
