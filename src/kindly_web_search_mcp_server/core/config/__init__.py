"""Core config composition root — breaking replacement for settings.py monolith."""
from .search import SearchSettings
from .content import ContentSettings
from .analytics import AnalyticsSettings
from .inference import InferenceSettings
from .app import AppSettings

__all__ = ["SearchSettings", "ContentSettings", "AnalyticsSettings", "InferenceSettings", "AppSettings"]
