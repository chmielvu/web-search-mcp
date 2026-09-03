from __future__ import annotations
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class AnalyticsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ANALYTICS_", env_file=".env", env_file_encoding="utf-8", extra="ignore")
    duckdb_path: str = Field(default="duckdb_data/analytics/search_events.duckdb")
    enabled: bool = Field(default=True)
    vss_enabled: bool = Field(default=True)
    flockmtl_enabled: bool = Field(default=True)
    judge_enabled: bool = Field(default=True)
