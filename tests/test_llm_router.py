from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _make_mock_response(content: str = "hello"):
    return type(
        "Resp",
        (),
        {
            "choices": [
                type(
                    "Choice",
                    (),
                    {
                        "message": type(
                            "Message",
                            (),
                            {"content": content},
                        )()
                    },
                )()
            ]
        },
    )()


class TestLLMRouter(unittest.IsolatedAsyncioTestCase):
    def test_worker_endpoints_keep_provider_models_and_add_litellm_route_models(self) -> None:
        from kindly_web_search_mcp_server.llm.config import build_worker_endpoints
        from kindly_web_search_mcp_server.settings import settings

        with (
            patch.object(settings, "cerebras_rewrite_model", "gpt-oss-120b"),
            patch.object(settings, "groq_rewrite_model", "openai/gpt-oss-120b"),
            patch.object(settings, "vercel_rewrite_model", "openai/gpt-oss-20b"),
        ):
            endpoints = build_worker_endpoints()

        self.assertEqual(
            [endpoint.model for endpoint in endpoints],
            [
                "gpt-oss-120b",
                "openai/gpt-oss-120b",
                "openai/gpt-oss-20b",
            ],
        )
        self.assertEqual(
            [endpoint.route_model for endpoint in endpoints],
            [
                "cerebras/gpt-oss-120b",
                "groq/openai/gpt-oss-120b",
                "openai/gpt-oss-20b",
            ],
        )

    async def test_router_sends_litellm_route_model_but_reports_provider_model(self) -> None:
        from kindly_web_search_mcp_server.llm.models import LLMEndpoint
        from kindly_web_search_mcp_server.llm.router import LLMRouter

        endpoint = LLMEndpoint(
            name="groq",
            model="openai/gpt-oss-120b",
            route_model="groq/openai/gpt-oss-120b",
            base_url="https://api.groq.com/openai/v1",
            api_key="test",
            timeout_seconds=10.0,
        )
        router = LLMRouter((endpoint,))

        with patch(
            "kindly_web_search_mcp_server.llm.router.acompletion",
            new_callable=AsyncMock,
        ) as mock_completion:
            mock_completion.return_value = _make_mock_response()

            generation = await router.complete_text(
                messages=[{"role": "user", "content": "hello"}],
            )

        self.assertEqual(
            mock_completion.await_args.kwargs["model"],
            "groq/openai/gpt-oss-120b",
        )
        self.assertEqual(generation.endpoint.model, "openai/gpt-oss-120b")

    async def test_groq_endpoint_drops_reasoning_effort(self) -> None:
        from kindly_web_search_mcp_server.llm.models import LLMEndpoint
        from kindly_web_search_mcp_server.llm.router import LLMRouter

        router = LLMRouter(
            (
                LLMEndpoint(
                    name="groq",
                    model="groq/openai/gpt-oss-120b",
                    base_url="https://api.groq.com/openai/v1",
                    api_key="test",
                    timeout_seconds=10.0,
                ),
            )
        )

        with patch(
            "kindly_web_search_mcp_server.llm.router.acompletion",
            new_callable=AsyncMock,
        ) as mock_completion:
            mock_completion.return_value = _make_mock_response()

            await router.complete_text(
                messages=[{"role": "user", "content": "hello"}],
                reasoning_effort="low",
            )

        self.assertNotIn("reasoning_effort", mock_completion.await_args.kwargs)

    async def test_cerebras_endpoint_drops_reasoning_effort(self) -> None:
        from kindly_web_search_mcp_server.llm.models import LLMEndpoint
        from kindly_web_search_mcp_server.llm.router import LLMRouter

        router = LLMRouter(
            (
                LLMEndpoint(
                    name="cerebras",
                    model="cerebras/llama3-70b",
                    base_url="https://api.cerebras.ai/v1",
                    api_key="test",
                    timeout_seconds=10.0,
                ),
            )
        )

        with patch(
            "kindly_web_search_mcp_server.llm.router.acompletion",
            new_callable=AsyncMock,
        ) as mock_completion:
            mock_completion.return_value = _make_mock_response()

            await router.complete_text(
                messages=[{"role": "user", "content": "hello"}],
                reasoning_effort="low",
            )

        self.assertNotIn("reasoning_effort", mock_completion.await_args.kwargs)

    async def test_vercel_endpoint_drops_reasoning_effort(self) -> None:
        from kindly_web_search_mcp_server.llm.models import LLMEndpoint
        from kindly_web_search_mcp_server.llm.router import LLMRouter

        router = LLMRouter(
            (
                LLMEndpoint(
                    name="vercel",
                    model="openai/gpt-oss-20b",
                    base_url="https://api.vercel.com/v1",
                    api_key="test",
                    timeout_seconds=10.0,
                ),
            )
        )

        with patch(
            "kindly_web_search_mcp_server.llm.router.acompletion",
            new_callable=AsyncMock,
        ) as mock_completion:
            mock_completion.return_value = _make_mock_response()

            await router.complete_text(
                messages=[{"role": "user", "content": "hello"}],
                reasoning_effort="low",
            )

        self.assertNotIn("reasoning_effort", mock_completion.await_args.kwargs)

    async def test_unsupported_provider_keeps_reasoning_effort(self) -> None:
        from kindly_web_search_mcp_server.llm.models import LLMEndpoint
        from kindly_web_search_mcp_server.llm.router import LLMRouter

        router = LLMRouter(
            (
                LLMEndpoint(
                    name="openai",
                    model="openai/gpt-4o",
                    base_url="https://api.openai.com/v1",
                    api_key="test",
                    timeout_seconds=10.0,
                ),
            )
        )

        with patch(
            "kindly_web_search_mcp_server.llm.router.acompletion",
            new_callable=AsyncMock,
        ) as mock_completion:
            mock_completion.return_value = _make_mock_response()

            await router.complete_text(
                messages=[{"role": "user", "content": "hello"}],
                reasoning_effort="low",
            )

        self.assertEqual(mock_completion.await_args.kwargs.get("reasoning_effort"), "low")
