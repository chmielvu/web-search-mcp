from kindly_web_search_mcp_server.rerank.bm25 import score_candidates, tokenize_candidate


def test_bm25_observable_fixture() -> None:
    scores = score_candidates("alpha", ["alpha alpha beta", "beta gamma"])
    assert scores[0] > 0
    assert scores[1] == 0


def test_bm25_empty_boundaries() -> None:
    assert score_candidates("alpha", []) == []
    assert score_candidates("!!!", ["alpha", "beta"]) == [0.0, 0.0]


def test_identifier_tokens_survive() -> None:
    tokens = tokenize_candidate("C C++ C# .NET v1.2.3 AB-1042-X CVE-2026-1234")
    for identifier in ("c", "c++", "c#", ".net", "v1.2.3", "ab-1042-x", "cve-2026-1234"):
        assert identifier in tokens


def test_cjk_emits_unigrams_and_bigrams() -> None:
    tokens = tokenize_candidate("搜索引擎")
    assert "搜" in tokens
    assert "搜索" in tokens
