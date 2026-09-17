from __future__ import annotations

from types import SimpleNamespace

import pytest

from kindly_web_search_mcp_server.index.web_results_index import WebResultsIndex
from kindly_web_search_mcp_server.search.providers import qdrant as qdrant_provider
from kindly_web_search_mcp_server.search.types import ScoredHit, SearchHit
from kindly_web_search_mcp_server.settings import settings


class _FakeQdrantClient:
    def __init__(self) -> None:
        self.points = []
        self.read_points = []

    async def upsert(self, *, collection_name, points, wait) -> None:
        del collection_name, wait
        self.points = list(points)

    async def query_points(self, **kwargs):
        del kwargs
        return SimpleNamespace(points=self.read_points)

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_qdrant_index_roundtrip_preserves_native_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_hit = SearchHit(
        title="Native evidence",
        url="https://example.com/native",
        snippet="Provider snippet",
        domain="example.com",
        adapter="exa",
        engine_rank=2,
        provider_score=0.93,
        source_name="Researcher",
        source_kind="official",
        published="2026-09-17",
        highlights=("Supporting passage",),
        source_engines=("google", "bing"),
        origin_adapters=("archive",),
        answer_kind="featured_snippet",
    )
    result = ScoredHit(hit=source_hit, providers=("exa", "tavily"))
    fake_client = _FakeQdrantClient()
    index = WebResultsIndex(url="https://qdrant.example")
    index._collection_ok = True
    monkeypatch.setattr(index, "_client", fake_client)

    await index.index_results(
        [result],
        [[0.1, 0.2]],
        [{"indices": [1], "values": [1.0]}],
        intent="research",
        entities=[{"text": "Native evidence"}],
    )

    payload = fake_client.points[0].payload
    assert payload["source_name"] == "Researcher"
    assert payload["highlights"] == ["Supporting passage"]
    assert payload["origin_adapters"] == ["exa", "tavily", "archive"]
    assert payload["answer_kind"] == "featured_snippet"

    fake_client.read_points = [SimpleNamespace(payload=payload, score=0.87)]
    monkeypatch.setattr(settings, "qdrant_space_url", "https://qdrant.example")
    monkeypatch.setattr(
        qdrant_provider,
        "AsyncQdrantClient",
        lambda **_: fake_client,
    )

    call = await qdrant_provider.search_qdrant(
        "native evidence",
        num_results=1,
        query_embedding=[0.1, 0.2],
    )

    restored = call.hits[0]
    assert restored.adapter == "qdrant"
    assert restored.provider_score == 0.87
    assert restored.engine_rank == 2
    assert restored.source_name == "Researcher"
    assert restored.source_kind == "official"
    assert restored.published == "2026-09-17"
    assert restored.highlights == ("Supporting passage",)
    assert restored.source_engines == ("google", "bing")
    assert restored.origin_adapters == ("exa", "tavily", "archive")
    assert restored.answer_kind == "featured_snippet"
