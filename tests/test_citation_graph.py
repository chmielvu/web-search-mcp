"""Unit tests for citation-graph reference classification."""

from __future__ import annotations

import pytest

from kindly_web_search_mcp_server.search.academic.citation_graph import (
    classify_paper_ref,
)


class TestClassifyPaperRef:
    def test_doi_forms(self) -> None:
        assert classify_paper_ref("10.1038/s41586-024-0001-1") == (
            "doi",
            "10.1038/s41586-024-0001-1",
        )
        assert classify_paper_ref("DOI:10.1038/x") == ("doi", "10.1038/x")
        assert classify_paper_ref("https://doi.org/10.1038/x") == ("doi", "10.1038/x")

    def test_arxiv_forms(self) -> None:
        assert classify_paper_ref("2401.12345") == ("arxiv", "2401.12345")
        assert classify_paper_ref("arXiv:2401.12345v2") == ("arxiv", "2401.12345v2")
        assert classify_paper_ref("https://arxiv.org/abs/2401.12345") == (
            "arxiv",
            "2401.12345",
        )

    def test_openalex_and_s2(self) -> None:
        assert classify_paper_ref("W2741809807") == ("openalex", "W2741809807")
        assert classify_paper_ref("https://openalex.org/W2741809807") == (
            "openalex",
            "W2741809807",
        )
        s2_id = "a" * 40
        assert classify_paper_ref(s2_id) == ("s2", s2_id)

    def test_raw_passthrough(self) -> None:
        kind, value = classify_paper_ref("some corpus id")
        assert kind == "raw"
        assert value == "some corpus id"

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            classify_paper_ref("   ")
