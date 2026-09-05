"""Behavior tests for the terminal MMR fallback selector."""

from __future__ import annotations

from typing import Any
import math

import pytest

from kindly_web_search_mcp_server.rerank.diversity import select_mmr_slate


_EMBEDDINGS = [
    [1.0, 0.0],
    [0.0, 1.0],
    [-1.0, 0.0],
    [0.0, -1.0],
]
_URLS = [
    "https://a.test/0",
    "https://b.test/1",
    "https://c.test/2",
    "https://d.test/3",
]


def test_relevance_changes_mmr_selection_and_returns_residuals() -> None:
    slate = select_mmr_slate(
        _EMBEDDINGS,
        _URLS,
        output_size=2,
        relevance_scores=[0.1, 0.9, 0.5, 0.2],
        lambda_param=1.0,
    )

    assert slate.triggered
    assert slate.selected_indices[:2] == (1, 2)
    assert slate.selected_indices[2:] == (0, 3)
    assert len(slate.selected_indices) == len(_EMBEDDINGS)
    assert set(slate.selected_indices) == set(range(len(_EMBEDDINGS)))
    assert slate.diversity_penalties[1] == 0.0


def test_host_cap_relaxes_only_to_fill_requested_slate() -> None:
    urls = [f"https://same.test/{index}" for index in range(4)]
    slate = select_mmr_slate(
        _EMBEDDINGS,
        urls,
        output_size=3,
        relevance_scores=[0.9, 0.8, 0.7, 0.6],
        lambda_param=1.0,
        max_per_host=1,
    )

    assert slate.selected_indices[:3] == (0, 1, 2)
    assert slate.selected_indices[3:] == (3,)
    assert slate.host_overflow_count == 3


@pytest.mark.parametrize(
    "kwargs",
    [
        {"output_size": 0, "relevance_scores": [0.1, 0.2, 0.3, 0.4]},
        {"output_size": 2, "relevance_scores": [0.1, 0.2]},
        {"output_size": 2, "relevance_scores": [0.1, math.nan, 0.3, 0.4]},
        {"output_size": 2, "relevance_scores": [0.1, 0.2, 0.3, 0.4], "lambda_param": 1.1},
        {"output_size": 2, "relevance_scores": [0.1, 0.2, 0.3, 0.4], "max_per_host": 0},
    ],
)
def test_invalid_mmr_inputs_raise_value_error(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        select_mmr_slate(_EMBEDDINGS, _URLS, **kwargs)


def test_embedding_dimension_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="equal dimensions"):
        select_mmr_slate(
            [[1.0, 0.0], [0.0, 1.0, 0.0]],
            _URLS[:2],
            output_size=1,
            relevance_scores=[0.5, 0.4],
        )
