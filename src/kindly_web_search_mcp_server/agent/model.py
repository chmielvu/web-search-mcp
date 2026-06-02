from __future__ import annotations

from langchain_openai import ChatOpenAI

from .config import AgenticResearchConfig


def build_chat_model(config: AgenticResearchConfig | None = None) -> ChatOpenAI:
    cfg = config or AgenticResearchConfig()
    if not cfg.api_key.strip():
        raise RuntimeError(
            "NANOGPT_API_KEY is not set. Agentic research cannot start without it."
        )
    return ChatOpenAI(
        model=cfg.model_name,
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        temperature=cfg.temperature,
        timeout=cfg.timeout_seconds,
        max_retries=cfg.max_retries,
        streaming=True,
        use_responses_api=False,
    )
