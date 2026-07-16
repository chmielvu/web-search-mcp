from __future__ import annotations

import sys
import unittest
from pathlib import Path


import types

try:
    import opentelemetry  # noqa: F401
except ModuleNotFoundError:
    telemetry_stub = types.ModuleType("kindly_web_search_mcp_server.telemetry")
    telemetry_stub.RRF_INPUT_LISTS = "rrf.input_lists"
    telemetry_stub.RRF_INPUT_TOTAL = "rrf.input_total"
    telemetry_stub.record_rrf_merge = lambda *a, **k: None
    telemetry_stub.record_rrf_score = lambda *a, **k: None
    telemetry_stub.record_merge = lambda *a, **k: None
    telemetry_stub.record_rerank_stage = lambda *a, **k: None
    telemetry_stub.RERANK_STAGE = "rerank.stage"
    telemetry_stub.RERANK_INPUT_COUNT = "rerank.input_count"
    telemetry_stub.RERANK_OUTPUT_COUNT = "rerank.output_count"
    telemetry_stub.SEARCH_QUERY = "search.query"
    sys.modules["kindly_web_search_mcp_server.telemetry"] = telemetry_stub

    otel_stub = types.ModuleType("opentelemetry")
    otel_stub.trace = types.SimpleNamespace(
        get_tracer=lambda *a, **k: types.SimpleNamespace(
            start_as_current_span=lambda *x, **y: None
        ),
        SpanKind=types.SimpleNamespace(INTERNAL="internal"),
    )
    sys.modules["opentelemetry"] = otel_stub


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.search.merge import merge_search_results


class TestMergeHostCap(unittest.TestCase):
    def _r(self, host: str, idx: int, provider: str = "searxng") -> WebSearchResult:
        return WebSearchResult(
            title=f"{host}-{idx}",
            link=f"https://{host}/p/{idx}",
            snippet=f"snippet-{idx}",
            providers=[provider],
        )

    def test_reciprocal_rank_fusion_contract(self) -> None:
        from kindly_web_search_mcp_server.search.merge import reciprocal_rank_fusion

        list_a = [self._r("a.com", 1), self._r("b.com", 2)]
        list_b = [self._r("b.com", 2)]

        fused = reciprocal_rank_fusion([list_a, list_b], k=60)
        self.assertEqual(len(fused), 2)
        self.assertEqual(fused[0][0].link.split("/")[2], "b.com")
        self.assertAlmostEqual(fused[0][1], 1 / 61 + 1 / 62)
        self.assertAlmostEqual(fused[1][1], 1 / 61)

    def test_pure_rrf_uses_rank_only(self) -> None:
        list_a = [self._r("a.com", 1), self._r("b.com", 2)]
        list_b = [self._r("b.com", 2)]

        merged = merge_search_results(
            [list_a, list_b],
            k=60,
            enable_telemetry=False,
        )

        self.assertEqual(merged[0].link.split("/")[2], "b.com")
        self.assertAlmostEqual(merged[0].score, 1 / 61 + 1 / 62)
        self.assertAlmostEqual(merged[1].score, 1 / 61)


if __name__ == "__main__":
    unittest.main()
