from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


_REPO_ROOT = Path(__file__).resolve().parents[1]


def load_repo_env() -> Path:
    """Load the repo .env file and return its path."""
    env_path = _REPO_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    load_dotenv()
    return env_path


_PROXY_NAMES = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
_SECRET_MARKERS = (
    "API_KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PROXY",
    "BASE_URL",
)


def redact_env_value(name: str, value: str) -> str:
    """Redact values that are sensitive or proxy-related."""
    normalized_name = name.strip().upper()
    if normalized_name in _PROXY_NAMES:
        return "***REDACTED***"
    if any(marker in normalized_name for marker in _SECRET_MARKERS):
        return "***REDACTED***"
    return value

