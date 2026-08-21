from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindly_web_search_mcp_server.youtube.analysis import analyze_transcript


@pytest.mark.asyncio
async def test_transcript_analysis_calls_vps_without_global_opt_in_flag() -> None:
    client = MagicMock()
    client._model_name = "fastino/gliner2-multi-v1"
    client.extract_transcript_chunk = AsyncMock(
        return_value=(
            {
                "model_version": "fastino/gliner2-multi-v1",
                "entities": {
                    "person": [{"text": "Alice", "start": 0, "end": 5, "confidence": 0.93}]
                },
                "structured_data": {"people": [{"name": "Alice", "role": "speaker"}]},
            },
            4.0,
        )
    )

    with patch(
        "kindly_web_search_mcp_server.youtube.analysis.get_gliner_client",
        return_value=client,
    ):
        result = await analyze_transcript("Alice explains the topic")

    client.extract_transcript_chunk.assert_awaited_once()
    assert result.status == "success"
    assert result.entities[0].text == "Alice"
    assert result.entities[0].label == "person"
    assert result.structured_data == {"people": [{"name": "Alice", "role": "speaker"}]}


@pytest.mark.asyncio
async def test_transcript_analysis_fails_open() -> None:
    client = MagicMock()
    client._model_name = "fastino/gliner2-multi-v1"
    client.extract_transcript_chunk = AsyncMock(side_effect=TimeoutError("VPS timeout"))

    with patch(
        "kindly_web_search_mcp_server.youtube.analysis.get_gliner_client",
        return_value=client,
    ):
        result = await analyze_transcript("A transcript")

    assert result.status == "error"
    assert result.entities == []
    assert result.warnings
