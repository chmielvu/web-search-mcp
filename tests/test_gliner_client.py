"""Tests for lazy GLiNER2 client (Phase 6.2).

These tests MUST pass *without* the gliner2 package installed.
They mock GLiNER2.from_pretrained and verify:
- lazy loading (import + from_pretrained only on first use)
- use of asyncio.to_thread for CPU-bound call
- settings propagation (model, threshold)
- output normalized to list[EntitySpan]
- explicit disabled state when KINDLY_ENTITY_EXTRACTION_ENABLED=false
- error events emitted on load/extract failure (no silent fail)
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindly_web_search_mcp_server.entity.gliner_client import (
    GLiNER2Client,
    get_gliner_client,
    is_entity_extraction_enabled,
)
from kindly_web_search_mcp_server.entity.models import EntitySpan
from kindly_web_search_mcp_server.settings import Settings


def test_gliner_not_imported_at_module_level(monkeypatch):
    """Core guarantee: gliner2 must never be imported unless explicitly used."""
    # Remove any accidental prior import
    for mod in list(sys.modules):
        if "gliner" in mod.lower():
            del sys.modules[mod]
    # Force reimport of our client module under clean state
    import importlib

    import kindly_web_search_mcp_server.entity.gliner_client as gc

    importlib.reload(gc)
    assert "gliner2" not in sys.modules
    assert "gliner" not in sys.modules


def test_is_entity_extraction_enabled_defaults_false(monkeypatch):
    """Default must be disabled (explicit opt-in)."""
    monkeypatch.delenv("KINDLY_ENTITY_EXTRACTION_ENABLED", raising=False)
    # fresh settings
    s = Settings()
    # The setting is populated in __post_init__ or directly; check helper
    assert is_entity_extraction_enabled() is False


def test_is_entity_extraction_enabled_from_env(monkeypatch):
    monkeypatch.setenv("KINDLY_ENTITY_EXTRACTION_ENABLED", "true")
    assert is_entity_extraction_enabled() is True
    monkeypatch.setenv("KINDLY_ENTITY_EXTRACTION_ENABLED", "false")
    assert is_entity_extraction_enabled() is False


@pytest.mark.asyncio
async def test_lazy_load_and_to_thread(monkeypatch):
    """First call triggers lazy from_pretrained inside to_thread; subsequent reuse."""
    monkeypatch.setenv("KINDLY_ENTITY_EXTRACTION_ENABLED", "true")
    monkeypatch.setenv("KINDLY_GLINER_MODEL", "fastino/gliner2-base-v1")
    monkeypatch.setenv("KINDLY_GLINER_THRESHOLD", "0.4")

    import kindly_web_search_mcp_server.entity.gliner_client as gc_mod

    gc_mod._gliner_client = None

    fake_model = MagicMock()
    fake_model.extract_entities.return_value = [
        {"text": "FastAPI", "label": "package", "start": 0, "end": 7, "score": 0.91},
        {"text": "0.100", "label": "version", "start": 8, "end": 13, "score": 0.85},
    ]

    call_log = []

    def fake_from_pretrained(name):
        call_log.append(("from_pretrained", name))
        return fake_model

    with patch.dict(sys.modules, {"gliner2": MagicMock()}):
        gliner2_mod = sys.modules["gliner2"]
        gliner2_mod.GLiNER2 = MagicMock()
        gliner2_mod.GLiNER2.from_pretrained = fake_from_pretrained

        # Patch asyncio.to_thread to record and delegate
        real_to_thread = asyncio.to_thread
        to_thread_calls = []

        async def recording_to_thread(func, *a, **k):
            to_thread_calls.append((func, a, k))
            return await real_to_thread(func, *a, **k)

        with patch("asyncio.to_thread", recording_to_thread):
            client = get_gliner_client()  # should not load yet
            assert client._model is None  # type: ignore[attr-defined]
            assert len(call_log) == 0

            ents = await client.extract_entities("Use FastAPI 0.100 here")

            assert len(call_log) == 1
            assert call_log[0] == ("from_pretrained", "fastino/gliner2-base-v1")
            assert len(to_thread_calls) >= 1
            assert isinstance(ents, list)
            assert all(isinstance(e, EntitySpan) for e in ents)
            assert ents[0].text == "FastAPI"
            assert ents[0].label == "package"
            assert ents[0].confidence == pytest.approx(0.91)
            assert ents[1].text == "0.100"
            assert client._model is fake_model  # cached

            # second call must not re-from_pretrained
            await client.extract_entities("again")
            assert len(call_log) == 1


@pytest.mark.asyncio
async def test_threshold_propagation(monkeypatch):
    monkeypatch.setenv("KINDLY_ENTITY_EXTRACTION_ENABLED", "true")
    monkeypatch.setenv("KINDLY_GLINER_THRESHOLD", "0.75")
    # Direct os.environ to guarantee resolve_threshold sees it even if prior tests left snapshot
    import os
    os.environ["KINDLY_GLINER_THRESHOLD"] = "0.75"

    # Ensure fresh client so settings/env snapshot in client sees monkeypatch
    import kindly_web_search_mcp_server.entity.gliner_client as gc_mod

    gc_mod._gliner_client = None

    fake_model = MagicMock()
    fake_model.extract_entities.return_value = []

    with patch.dict(sys.modules, {"gliner2": MagicMock()}):
        sys.modules["gliner2"].GLiNER2 = MagicMock()
        sys.modules["gliner2"].GLiNER2.from_pretrained = lambda n: fake_model

        client = get_gliner_client()
        client._model = None  # ensure load uses the just-patched from_pretrained
        await client.extract_entities("query")
        # the call to model.extract_entities must have used the threshold
        fake_model.extract_entities.assert_called()
        _, call_kwargs = fake_model.extract_entities.call_args
        # GLiNER2 API: extract_entities(text, labels, threshold=...)
        assert call_kwargs.get("threshold") == 0.75 or 0.75 in call_kwargs.values()


@pytest.mark.asyncio
async def test_disabled_returns_empty_and_no_load(monkeypatch):
    monkeypatch.setenv("KINDLY_ENTITY_EXTRACTION_ENABLED", "false")

    import kindly_web_search_mcp_server.entity.gliner_client as gc_mod

    gc_mod._gliner_client = None

    client = get_gliner_client()
    ents = await client.extract_entities("anything")
    assert ents == []

    # ensure we never tried to touch gliner2
    assert "gliner2" not in sys.modules or not hasattr(
        sys.modules.get("gliner2"), "GLiNER2"
    )


@pytest.mark.asyncio
async def test_extract_error_emits_event_and_returns_empty(monkeypatch, caplog):
    """Enabled but broken -> explicit error event, never silent, returns [] ."""
    monkeypatch.setenv("KINDLY_ENTITY_EXTRACTION_ENABLED", "true")
    import os
    os.environ["KINDLY_ENTITY_EXTRACTION_ENABLED"] = "true"

    # reset singleton so load path is exercised with the failing from_pretrained
    import kindly_web_search_mcp_server.entity.gliner_client as gc_mod

    gc_mod._gliner_client = None

    def boom(name):
        raise RuntimeError("model download failed for test")

    captured_events = []

    def fake_emit(logger, event, **fields):
        captured_events.append((event, fields))  # record everything for robust assert

    import kindly_web_search_mcp_server.entity.gliner_client as gc_mod

    # ensure no stale gliner2 from prior tests interferes with this patch.dict
    sys.modules.pop("gliner2", None)
    sys.modules.pop("gliner", None)

    with patch.object(gc_mod, "emit_observability_event", fake_emit):
        with patch.dict(sys.modules, {"gliner2": MagicMock()}):
            sys.modules["gliner2"].GLiNER2 = MagicMock()
            sys.modules["gliner2"].GLiNER2.from_pretrained = boom

            client = gc_mod.get_gliner_client()
            client._model = None  # force the load path under the boom patch
            sys.modules["gliner2"].GLiNER2.from_pretrained = boom
            ents = await client.extract_entities("will fail to load")

            assert ents == []
            # must have emitted an error event (load failure path)
            assert any("entity" in (ev[0] or "") for ev in captured_events), f"no entity events: {captured_events}"
            assert any("error" in (ev[0] or "") or "failure" in str(ev[1]) for ev in captured_events)


def test_get_gliner_client_is_singleton(monkeypatch):
    monkeypatch.setenv("KINDLY_ENTITY_EXTRACTION_ENABLED", "true")

    import kindly_web_search_mcp_server.entity.gliner_client as gc_mod

    gc_mod._gliner_client = None

    with patch.dict(sys.modules, {"gliner2": MagicMock()}):
        sys.modules["gliner2"].GLiNER2 = MagicMock()
        sys.modules["gliner2"].GLiNER2.from_pretrained = lambda n: MagicMock()

        c1 = get_gliner_client()
        c2 = get_gliner_client()
        assert c1 is c2
