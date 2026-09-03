from __future__ import annotations
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class SearchSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SEARCH_", env_file=".env", env_file_encoding="utf-8", extra="ignore")
    searxng_base_url: str = Field(default="", description="SearXNG base URL")
    search_http_connect_timeout_seconds: float = Field(default=10.0)
    search_retrieve_budget_seconds: float = Field(default=20.0)
    rrf_k: int = Field(default=60)
    serpapi_default_engine: str = Field(default="yahoo")
