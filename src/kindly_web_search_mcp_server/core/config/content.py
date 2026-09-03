from __future__ import annotations
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class ContentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CONTENT_", env_file=".env", env_file_encoding="utf-8", extra="ignore")
    jina_timeout_seconds: float = Field(default=25.0)
    crawl4ai_timeout_seconds: float = Field(default=30.0)
    camoufox_timeout_seconds: float = Field(default=35.0)
    local_extract_timeout_seconds: float = Field(default=20.0)
    youtube_transcript_languages: list[str] = Field(default_factory=lambda: ["en"])
