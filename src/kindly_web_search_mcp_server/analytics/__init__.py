"""Analytics sinks for offline tuning and inspection."""

from .duckdb_store import append_event

__all__ = ["append_event"]
