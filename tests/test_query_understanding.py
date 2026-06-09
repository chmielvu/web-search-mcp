from __future__ import annotations

from kindly_web_search_mcp_server.entity.models import EntitySpan
from kindly_web_search_mcp_server.search.understanding.models import QueryUnderstandingResult


def test_query_understanding_result_preserves_terms() -> None:
    result = QueryUnderstandingResult(
        intent="comparison",
        confidence=0.88,
        entities=[EntitySpan(text="FastAPI", label="package", start=0, end=7, confidence=0.9)],
        preserved_terms=["FastAPI", "Pydantic"],
        compared_entities=["FastAPI", "Starlette"],
        rationale="Compare frameworks.",
    )

    assert result.schema_version == "0.2"
    assert result.must_keep_terms == ["FastAPI", "Pydantic"]
    assert result.compared_entities == ["FastAPI", "Starlette"]
