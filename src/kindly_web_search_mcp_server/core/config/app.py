from __future__ import annotations
from pydantic_settings import BaseSettings, SettingsConfigDict
from .search import SearchSettings
from .content import ContentSettings
from .analytics import AnalyticsSettings
from .inference import InferenceSettings

class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    search: SearchSettings = SearchSettings()
    content: ContentSettings = ContentSettings()
    analytics: AnalyticsSettings = AnalyticsSettings()
    inference: InferenceSettings = InferenceSettings()
