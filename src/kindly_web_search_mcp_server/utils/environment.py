from __future__ import annotations

import os


def get_int_env(key: str, default: int) -> int:
    """Read an integer environment variable with safe fallback."""
    raw = (os.environ.get(key) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def get_float_env(key: str, default: float) -> float:
    """Read a float environment variable with safe fallback."""
    raw = (os.environ.get(key) or "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default
