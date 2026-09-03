from __future__ import annotations
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class InferenceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INFERENCE_", env_file=".env", env_file_encoding="utf-8", extra="ignore")
    openai_api_key: str = Field(default="")
    gemini_api_key: str = Field(default="")
    judge_gemini_model: str = Field(default="gemma-4-26b-a4b-it")
    judge_nanogpt_model: str = Field(default="deepseek/deepseek-v4-flash-0731:thinking")
    judge_nanogpt_base_url: str = Field(default="https://nano-gpt.com/api/subscription/v1")
