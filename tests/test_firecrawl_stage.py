"""Focused tests for the Firecrawl batch scrape stage."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kindly_web_search_mcp_server.content import firecrawl_stage
from kindly_web_search_mcp_server.content.options import FetchOptions


def _make_doc(source_url: str, markdown: str, links: list | None = None) -> MagicMock:
    doc = MagicMock()
    doc.metadata_dict = {"source_url": source_url, "url": source_url}
    doc.markdown = markdown
    doc.links = links or []
    return doc


def _install_client(monkeypatch, batch_scrape: AsyncMock) -> MagicMock:
    client = MagicMock(batch_scrape=batch_scrape)
    monkeypatch.setattr(firecrawl_stage, "_client", client)
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.content.firecrawl_stage.settings.firecrawl_api_key",
        "fc-test",
    )
    return client


async def test_run_firecrawl_batch_maps_documents(monkeypatch):
    batch_scrape = AsyncMock(
        return_value=MagicMock(data=[_make_doc("https://example.com", "# Hello")])
    )
    _install_client(monkeypatch, batch_scrape)

    result = await firecrawl_stage.run_firecrawl_batch(
        ["https://example.com"], options=FetchOptions(), batch_params=None
    )

    assert list(result) == ["https://example.com"]
    artifact = result["https://example.com"]
    assert artifact.fetch_backend == "firecrawl_cloud"
    assert artifact.status == "success"
    assert artifact.markdown == "# Hello"
    batch_scrape.assert_awaited_once()


async def test_run_firecrawl_batch_empty_markdown_is_error(monkeypatch):
    batch_scrape = AsyncMock(return_value=MagicMock(data=[_make_doc("https://example.com", "")]))
    _install_client(monkeypatch, batch_scrape)

    result = await firecrawl_stage.run_firecrawl_batch(
        ["https://example.com"], options=FetchOptions(), batch_params=None
    )

    artifact = result["https://example.com"]
    assert artifact.status == "error"
    assert artifact.error is not None
    assert artifact.error.code == "firecrawl_empty"


async def test_run_firecrawl_batch_exception_returns_none(monkeypatch):
    batch_scrape = AsyncMock(side_effect=RuntimeError("boom"))
    _install_client(monkeypatch, batch_scrape)

    result = await firecrawl_stage.run_firecrawl_batch(
        ["https://example.com"], options=FetchOptions(), batch_params=None
    )

    assert result is None


async def test_run_firecrawl_batch_missing_url_omitted(monkeypatch):
    batch_scrape = AsyncMock(
        return_value=MagicMock(data=[_make_doc("https://other.com", "# Other")])
    )
    _install_client(monkeypatch, batch_scrape)

    result = await firecrawl_stage.run_firecrawl_batch(
        ["https://example.com"], options=FetchOptions(), batch_params=None
    )

    assert result == {}


async def test_run_firecrawl_batch_include_links_adds_format(monkeypatch):
    batch_scrape = AsyncMock(return_value=MagicMock(data=[]))
    _install_client(monkeypatch, batch_scrape)

    await firecrawl_stage.run_firecrawl_batch(
        ["https://example.com"],
        options=FetchOptions(include_links=True),
        batch_params=None,
    )

    _, kwargs = batch_scrape.call_args
    assert "links" in kwargs["formats"]


async def test_run_firecrawl_batch_no_api_key_returns_none(monkeypatch):
    monkeypatch.setattr(firecrawl_stage, "_client", None)
    monkeypatch.setattr(
        "kindly_web_search_mcp_server.content.firecrawl_stage.settings.firecrawl_api_key",
        "",
    )

    result = await firecrawl_stage.run_firecrawl_batch(
        ["https://example.com"], options=FetchOptions(), batch_params=None
    )

    assert result is None


async def test_run_firecrawl_batch_invalid_urls_mapped_to_error(monkeypatch):
    response_mock = MagicMock(data=[], invalid_urls=["https://invalid-domain.xyz"])
    batch_scrape = AsyncMock(return_value=response_mock)
    _install_client(monkeypatch, batch_scrape)

    result = await firecrawl_stage.run_firecrawl_batch(
        ["https://invalid-domain.xyz"], options=FetchOptions(), batch_params=None
    )

    assert result is not None
    assert "https://invalid-domain.xyz" in result
    artifact = result["https://invalid-domain.xyz"]
    assert artifact.status == "error"
    assert artifact.error is not None
    assert artifact.error.code == "invalid_url"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
