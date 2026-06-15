from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestLLMRouter(unittest.IsolatedAsyncioTestCase):
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

        with (
            patch(
                "kindly_web_search_mcp_server.llm.router.acompletion",
                new_callable=AsyncMock,
            ) as mock_completion,
            patch(
                "kindly_web_search_mcp_server.llm.router.ensure_langfuse_litellm_callbacks",
                return_value=False,
            ),
            patch(
                "kindly_web_search_mcp_server.llm.router.build_langfuse_litellm_kwargs",
                return_value={},
            ),
        ):
            mock_completion.return_value = type(
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
                                    {"content": "hello"},
                                )()
                            },
                        )()
                    ]
                },
            )()

            await router.complete_text(
                messages=[{"role": "user", "content": "hello"}],
                reasoning_effort="low",
            )

        self.assertNotIn("reasoning_effort", mock_completion.await_args.kwargs)

