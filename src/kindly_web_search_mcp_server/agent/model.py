from __future__ import annotations

from typing import Any

from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from .config import AgenticResearchConfig


class ChatModelFallbackChain(Runnable[Any, Any]):
    def __init__(self, models: list[Any]) -> None:
        if not models:
            raise ValueError("ChatModelFallbackChain requires at least one model.")
        self.models = tuple(models)
        self.runnable = self.models[0]
        self.fallbacks = list(self.models[1:])

    def _raise_or_return(self, errors: list[Exception]) -> None:
        if errors:
            raise errors[0]
        raise RuntimeError("ChatModelFallbackChain failed without an exception.")

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        errors: list[Exception] = []
        for model in self.models:
            try:
                return model.invoke(input, config=config, **kwargs)
            except Exception as exc:
                errors.append(exc)
        self._raise_or_return(errors)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        errors: list[Exception] = []
        for model in self.models:
            try:
                return await model.ainvoke(input, config=config, **kwargs)
            except Exception as exc:
                errors.append(exc)
        self._raise_or_return(errors)

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ChatModelFallbackChain":
        return ChatModelFallbackChain(
            [model.bind_tools(tools, **kwargs) for model in self.models]
        )


def _build_chat_model(cfg: AgenticResearchConfig, model_name: str) -> ChatOpenAI:
    if not cfg.api_key.strip():
        raise RuntimeError(
            "NANOGPT_API_KEY is not set. Agentic research cannot start without it."
        )
    return ChatOpenAI(
        model=model_name,
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        temperature=cfg.temperature,
        timeout=cfg.timeout_seconds,
        max_retries=cfg.max_retries,
        streaming=False,
        use_responses_api=False,
    )


def _build_gemini_model(cfg: AgenticResearchConfig) -> Any:
    if not cfg.gemini_api_key.strip():
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Gemini agentic fallback cannot start."
        )
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError as exc:
        raise RuntimeError(
            "langchain-google-genai is required for the Gemini agentic fallback."
        ) from exc

    return ChatGoogleGenerativeAI(
        model=cfg.gemini_fallback_model,
        api_key=cfg.gemini_api_key,
        temperature=cfg.temperature,
        timeout=cfg.timeout_seconds,
        max_retries=cfg.max_retries,
    )


def _build_hf_router_model(cfg: AgenticResearchConfig) -> ChatOpenAI:
    if not cfg.hf_token.strip():
        raise RuntimeError(
            "HF_TOKEN is not set. Hugging Face router agentic fallback cannot start."
        )
    return ChatOpenAI(
        model=cfg.hf_fallback_model,
        base_url=cfg.hf_router_base_url,
        api_key=cfg.hf_token,
        temperature=cfg.temperature,
        timeout=cfg.timeout_seconds,
        max_retries=cfg.max_retries,
        streaming=False,
        use_responses_api=False,
    )


def build_chat_model(config: AgenticResearchConfig | None = None) -> Any:
    cfg = config or AgenticResearchConfig()
    model_names = cfg.model_chain()
    primary = _build_chat_model(cfg, model_names[0])
    fallbacks = [_build_chat_model(cfg, model_name) for model_name in model_names[1:]]
    if cfg.gemini_api_key.strip() and cfg.gemini_fallback_model.strip():
        fallbacks.append(_build_gemini_model(cfg))
    if cfg.hf_token.strip() and cfg.hf_fallback_model.strip():
        fallbacks.append(_build_hf_router_model(cfg))
    if not fallbacks:
        return primary
    return ChatModelFallbackChain([primary, *fallbacks])
