from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestBrightDataProvider(unittest.TestCase):
    def test_parse_json_payload(self) -> None:
        from kindly_web_search_mcp_server.search.brightdata import _parse_result_text

        results = _parse_result_text(
            """
            {"organic": [{"title": "Example", "link": "https://example.com", "snippet": "snippet"}]}
            """.strip()
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Example")
        self.assertEqual(results[0].link, "https://example.com")
        self.assertEqual(results[0].snippet, "snippet")

    def test_parse_markdown_payload(self) -> None:
        from kindly_web_search_mcp_server.search.brightdata import _parse_result_text

        results = _parse_result_text(
            """
            Title: Example Title
            URL: https://example.com
            Description: first line
            Description: second line
            """.strip()
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Example Title")
        self.assertEqual(results[0].link, "https://example.com")
        self.assertEqual(results[0].snippet, "first line second line")

    def test_parse_yandex_markdown_payload(self) -> None:
        from kindly_web_search_mcp_server.search.brightdata import _parse_result_text

        results = _parse_result_text(
            """
            weather new york — Яндекс: нашлось 484 тыс. результатов

            *   [](https://yabs.yandex.kz/count/WNuejI_zOoVX2Ld-08KB0EEKJX5WW4uu8559hYwIG9KcY4Q_u_M6Ero_azLnz3fx2US1T9mgB7fywXotd_QTEwFs58y-dquxX-9FfrtVETIEti_RUKDnEvVQB72mT8UlXmxU1CIqt6pz-MppzJPQfLAfL6eufB7PuS6Ol6fyIyY4NpQw9ylh0XUv2CN4ZOEP00xYcGrSFpKVMWrxqq_DXrR3rfzfm9vc-wJHqZJP9utkCvXfCZGk0yXg242dmM3sMqMODEaPGLnxmw2ruHVNeweGyadPmlRBD2gRBt8epWk-qZVmXxUjES0WK-OfP3q5tSEOdSmGmI02D-W4awLcg-imtbflX0hqeZJm1zEr0LREUlboLcxgqNNDBvtNtFrlz5EQVFDsNTTrshoiko84GZVIz7W0A8wvKgbKXZ12NBRzfF8BoK0eHPGu2dW23_opPfGPq1ZuIFU0Xst_u2Wym_DJP1zxZ6feoQ24cizoqgaBQhRPTflDtw2nwEgQ1erR~2)
                Яндекс Браузер]

            *   [](https://www.accuweather.com/en/us/new-york/10021/weather-forecast/349727)
                Accuweather.com
                ## **New** **York**, **NY** **Weather** Forecast | AccuWeather
                Use Current Location. Recent. **New** **York**.

            *   [](https://meteum.ai/weather/en/new-york)
                Meteum.ai
                ## **Weather** in **New** **York** — **Weather** forecast in **New** **York**...
                **New** **York**, current **weather**: Clear.
            """.strip()
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(
            results[0].title,
            "New York, NY Weather Forecast | AccuWeather",
        )
        self.assertEqual(results[0].link, "https://www.accuweather.com/en/us/new-york/10021/weather-forecast/349727")
        self.assertIn("Use Current Location", results[0].snippet)
        self.assertEqual(
            results[1].title,
            "Weather in New York — Weather forecast in New York...",
        )
        self.assertEqual(results[1].link, "https://meteum.ai/weather/en/new-york")
        self.assertIn("current weather: Clear", results[1].snippet)

    def test_select_engines_prefers_bing_for_news_queries(self) -> None:
        from kindly_web_search_mcp_server.search.brightdata import _select_engines

        self.assertEqual(_select_engines("latest ai release"), ["yandex", "google", "bing"])

    def test_select_engines_uses_configured_default_engine(self) -> None:
        from kindly_web_search_mcp_server.search.brightdata import _select_engines

        self.assertEqual(
            _select_engines("plain search query", default_engine="yandex"),
            ["yandex", "google"],
        )

    def test_select_engines_defaults_to_yandex_with_google_fallback(self) -> None:
        from kindly_web_search_mcp_server.search.brightdata import _select_engines

        self.assertEqual(_select_engines("plain search query"), ["yandex", "google"])

    def test_search_one_engine_omits_cursor(self) -> None:
        from kindly_web_search_mcp_server.search import brightdata

        calls: list[tuple[str, dict[str, object]]] = []

        class FakeResult:
            content = [
                SimpleNamespace(
                    type="text",
                    text=(
                        '{"organic":[{"title":"Example","link":"https://example.com",'
                        '"snippet":"snippet"}]}'
                    ),
                )
            ]

        class FakeSession:
            async def __aenter__(self) -> "FakeSession":
                return self

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

            async def initialize(self) -> None:
                return None

            async def call_tool(self, name: str, arguments: dict[str, object]) -> FakeResult:
                calls.append((name, arguments))
                return FakeResult()

        class FakeStreamClient:
            def __init__(self, endpoint: str) -> None:
                self.endpoint = endpoint

            async def __aenter__(self) -> tuple[object, object, object]:
                return object(), object(), object()

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

        class FakeHealth:
            def is_healthy(self, name: str) -> bool:
                return True

            def mark_success(self, name: str) -> None:
                return None

            def mark_failure(self, name: str) -> None:
                return None

        with (
            patch.object(brightdata, "streamablehttp_client", FakeStreamClient),
            patch.object(brightdata, "ClientSession", lambda read, write: FakeSession()),
            patch.object(brightdata, "get_provider_health", return_value=FakeHealth()),
        ):
            results = self._run_async(
                brightdata._search_one_engine(
                    "example query",
                    "google",
                    "https://mcp.brightdata.com/mcp?token=test",
                )
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Example")
        self.assertEqual(calls, [("search_engine", {"query": "example query", "engine": "google"})])

    def test_search_one_engine_raises_on_upstream_error_envelope(self) -> None:
        from kindly_web_search_mcp_server.search import brightdata

        class FakeResult:
            content = [
                SimpleNamespace(
                    type="text",
                    text=(
                        '{"status_code":407,"headers":{"proxy-status":"'
                        'brd.superproxy.io; received-status=407; error=\\"http_request_denied\\"",'
                        '"x-brd-err-code":"client_10000","x-brd-err-msg":"Invalid authentication"}}'
                    ),
                )
            ]

        class FakeSession:
            async def __aenter__(self) -> "FakeSession":
                return self

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

            async def initialize(self) -> None:
                return None

            async def call_tool(self, name: str, arguments: dict[str, object]) -> FakeResult:
                return FakeResult()

        class FakeStreamClient:
            def __init__(self, endpoint: str) -> None:
                self.endpoint = endpoint

            async def __aenter__(self) -> tuple[object, object, object]:
                return object(), object(), object()

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

        class FakeHealth:
            def is_healthy(self, name: str) -> bool:
                return True

            def mark_success(self, name: str) -> None:
                return None

            def mark_failure(self, name: str) -> None:
                return None

        with (
            patch.object(brightdata, "streamablehttp_client", FakeStreamClient),
            patch.object(brightdata, "ClientSession", lambda read, write: FakeSession()),
            patch.object(brightdata, "get_provider_health", return_value=FakeHealth()),
        ):
            with self.assertRaises(brightdata.BrightDataError):
                self._run_async(
                    brightdata._search_one_engine(
                        "example query",
                        "google",
                        "https://mcp.brightdata.com/mcp?token=test",
                    )
                )

    def test_search_brightdata_raises_on_all_empty_results(self) -> None:
        from kindly_web_search_mcp_server.search import brightdata

        with (
            patch.object(brightdata, "_select_engines", return_value=["google", "bing"]),
            patch.object(brightdata, "_search_one_engine", new=AsyncMock(return_value=[])),
            patch.object(brightdata, "_get_endpoint", return_value="https://example.invalid"),
        ):
            with self.assertRaises(brightdata.BrightDataError):
                self._run_async(
                    brightdata.search_brightdata(
                        "OpenAI",
                        num_results=5,
                    )
                )

    def test_search_brightdata_falls_back_from_yandex_to_google(self) -> None:
        from kindly_web_search_mcp_server.search import brightdata

        yandex_error = brightdata.BrightDataError("yandex failed")
        google_results = [
            brightdata.WebSearchResult(
                title="Example",
                link="https://example.com",
                snippet="snippet",
            )
        ]

        async def fake_search_one_engine(query: str, engine: str, endpoint: str):
            if engine == "yandex":
                raise yandex_error
            return google_results

        with (
            patch.object(brightdata, "_search_one_engine", side_effect=fake_search_one_engine),
            patch.object(brightdata, "_get_endpoint", return_value="https://example.invalid"),
        ):
            results = self._run_async(
                brightdata.search_brightdata(
                    "weather new york",
                    num_results=5,
                )
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Example")

    @staticmethod
    def _run_async(awaitable):
        import asyncio

        return asyncio.run(awaitable)


if __name__ == "__main__":
    unittest.main()
