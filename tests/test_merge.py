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
    telemetry_stub.record_diversity_removal = lambda *a, **k: None
    telemetry_stub.RERANK_STAGE = "rerank.stage"
    telemetry_stub.RERANK_INPUT_COUNT = "rerank.input_count"
    telemetry_stub.RERANK_OUTPUT_COUNT = "rerank.output_count"
    telemetry_stub.SEARCH_QUERY = "search.query"
    sys.modules["kindly_web_search_mcp_server.telemetry"] = telemetry_stub

    otel_stub = types.ModuleType("opentelemetry")
    otel_stub.trace = types.SimpleNamespace(
        get_tracer=lambda *a, **k: types.SimpleNamespace(start_as_current_span=lambda *x, **y: None),
        SpanKind=types.SimpleNamespace(INTERNAL="internal"),
    )
    sys.modules["opentelemetry"] = otel_stub


import types

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

    def test_host_cap_reduces_clustering_in_top_k(self) -> None:
        provider_a = [
            self._r("a.com", 1),
            self._r("a.com", 2),
            self._r("a.com", 3),
            self._r("b.com", 1),
            self._r("c.com", 1),
        ]
        provider_b = [
            self._r("a.com", 1, "brave"),
            self._r("a.com", 2, "brave"),
            self._r("d.com", 1, "brave"),
        ]

        merged = merge_search_results(
            [provider_a, provider_b],
            max_per_host=2,
            host_cap_top_k=5,
            enable_telemetry=False,
        )

        top_hosts = [r.link.split("/")[2] for r in merged[:5]]
        self.assertLessEqual(top_hosts.count("a.com"), 2)
        self.assertEqual(top_hosts[0], "a.com")

    def test_deterministic_tie_breaks_and_interleave_rest(self) -> None:
        results = [
            self._r("alpha.com", 1),
            self._r("alpha.com", 2),
            self._r("alpha.com", 3),
            self._r("beta.com", 1),
            self._r("gamma.com", 1),
        ]

        merged = merge_search_results(
            [results],
            max_per_host=1,
            host_cap_top_k=5,
            enable_telemetry=False,
        )

        ordered_hosts = [r.link.split("/")[2] for r in merged[:5]]
        self.assertEqual(
            ordered_hosts,
            ["alpha.com", "beta.com", "gamma.com", "alpha.com", "alpha.com"],
        )

    def test_cap_window_backfills_when_unique_hosts_are_insufficient(self) -> None:
        results = [
            self._r("same.com", 1),
            self._r("same.com", 2),
            self._r("same.com", 3),
            self._r("same.com", 4),
            self._r("other.com", 1),
        ]

        merged = merge_search_results(
            [results],
            max_per_host=1,
            host_cap_top_k=3,
            enable_telemetry=False,
        )

        # The window includes one same.com + one other.com first, then backfills.
        top_hosts = [r.link.split("/")[2] for r in merged[:3]]
        self.assertEqual(top_hosts[:2], ["same.com", "other.com"])
        self.assertEqual(len(merged), 5)


if __name__ == "__main__":
    unittest.main()
