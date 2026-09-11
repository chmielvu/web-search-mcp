"""Google Discovery Engine / Agent Search provider.

One adapter, many apps: add a future news engine as another ``APPS`` row.
Builtin ``search-1`` serves ``general`` and ``ai_coding_and_infrastructure``.

Auth is OAuth, never an API key. ADC only when a credentials file is
present, else ``gcloud auth print-access-token``. Token mint is warmed
during planning so the 15s retrieve cap is search, not gcloud. Every
call sends ``x-goog-user-project``.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ...models import WebSearchResult
from ...settings import settings
from ...utils.url_canonicalize import extract_domain_from_url
from ..intents import normalize_intent
from ..options import SearchOptions
from .base import (
    ProviderRequestError,
    ProviderRequestMetadata,
    _with_metadata,
    get_provider_request_metadata,
    provider_retry_max_retries,
    run_provider,
    set_provider_request_metadata,
)

PROVIDER_NAME = "google_discovery_engine"
_API_ROOT = "https://discoveryengine.googleapis.com/v1"
_TOKEN_TTL_SECONDS = 55 * 60
_GCLOUD_TIMEOUT_SECONDS = 15
_TAG_RE = re.compile(r"<[^>]+>")
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LOGGER = logging.getLogger(__name__)


class DiscoveryEngineError(ProviderRequestError):
    pass


class DiscoveryEngineConfigError(DiscoveryEngineError):
    pass


@dataclass(frozen=True, slots=True)
class DiscoveryEngineApp:
    """One Agent Search engine and the intents it may run for."""

    app_id: str
    intents: frozenset[str]
    project: str
    engine_id: str
    location: str = "global"
    collection: str = "default_collection"
    serving_config_id: str = "default_search"

    @property
    def serving_config(self) -> str:
        return (
            f"projects/{self.project}/locations/{self.location}/"
            f"collections/{self.collection}/engines/{self.engine_id}/"
            f"servingConfigs/{self.serving_config_id}"
        )

    @property
    def search_url(self) -> str:
        return f"{_API_ROOT}/{self.serving_config}:search"


# Future engines (news, …) are extra rows here — not a new adapter.
APPS: tuple[DiscoveryEngineApp, ...] = (
    DiscoveryEngineApp(
        app_id="search-1",
        intents=frozenset({"general", "ai_coding_and_infrastructure"}),
        project="magdalenka-ecosystem",
        engine_id="search-1_1788901153517",
    ),
)

_token_lock = threading.Lock()
_cached_token: str | None = None
_cached_token_expires_at = 0.0
_cached_auth_mode: str | None = None
_adc_unavailable = False


def app_for_intent(intent: str | None) -> DiscoveryEngineApp | None:
    key = normalize_intent(intent)
    for app in APPS:
        if key in app.intents:
            return app
    return None


def _plain_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return html.unescape(_TAG_RE.sub("", value)).replace("\xa0", " ").strip()


def _resolve_gcloud_bin() -> str | None:
    configured = (settings.discovery_engine_gcloud_bin or "").strip()
    if configured and Path(configured).exists():
        return configured
    for name in ("gcloud.cmd", "gcloud"):
        found = shutil.which(name)
        if found:
            return found
    if os.name == "nt":
        fallback = (
            Path.home()
            / "AppData"
            / "Local"
            / "Google"
            / "Cloud SDK"
            / "google-cloud-sdk"
            / "bin"
            / "gcloud.cmd"
        )
        if fallback.is_file():
            return str(fallback)
    return None


def credentials_available() -> bool:
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip():
        return True
    return _resolve_gcloud_bin() is not None


def _well_known_adc_path() -> Path:
    if os.name == "nt":
        return (
            Path.home() / "AppData" / "Roaming" / "gcloud" / "application_default_credentials.json"
        )
    return Path.home() / ".config" / "gcloud" / "application_default_credentials.json"


def _adc_credentials_present() -> bool:
    configured = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if configured:
        return Path(configured).is_file()
    return _well_known_adc_path().is_file()


def reset_token_cache() -> None:
    global _cached_token, _cached_token_expires_at, _cached_auth_mode
    with _token_lock:
        _cached_token = None
        _cached_token_expires_at = 0.0
        _cached_auth_mode = None


def _token_from_adc() -> str | None:
    try:
        import google.auth
        from google.auth.transport.requests import Request as GoogleAuthRequest
    except ImportError:
        return None
    try:
        credentials, _project = google.auth.default(
            scopes=("https://www.googleapis.com/auth/cloud-platform",)
        )
    except Exception:
        return None
    if not getattr(credentials, "valid", False) or not getattr(credentials, "token", None):
        credentials.refresh(GoogleAuthRequest())
    token = getattr(credentials, "token", None)
    return token if isinstance(token, str) and token.strip() else None


def _token_from_gcloud() -> str:
    binary = _resolve_gcloud_bin()
    if not binary:
        raise DiscoveryEngineConfigError(
            "Discovery Engine auth needs gcloud or Application Default Credentials. "
            "Run `gcloud auth login` (or `gcloud auth application-default login`)."
        )
    try:
        completed = subprocess.run(
            [binary, "auth", "print-access-token", "--quiet"],
            capture_output=True,
            text=True,
            timeout=_GCLOUD_TIMEOUT_SECONDS,
            creationflags=_CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DiscoveryEngineConfigError(
            "gcloud auth print-access-token timed out. Run `gcloud auth login` and retry."
        ) from exc
    token = (completed.stdout or "").strip()
    if completed.returncode != 0 or not token:
        detail = (completed.stderr or completed.stdout or "gcloud failed").strip()[:300]
        raise DiscoveryEngineConfigError(
            f"gcloud auth print-access-token failed. Run `gcloud auth login` and retry. {detail}"
        )
    return token


def _fetch_token() -> tuple[str, str]:
    global _adc_unavailable
    if not _adc_unavailable:
        if not _adc_credentials_present():
            _adc_unavailable = True
        else:
            token = _token_from_adc()
            if token:
                return token, "adc"
            _adc_unavailable = True
    return _token_from_gcloud(), "gcloud_user"


def _mint_token(*, force: bool = False) -> tuple[str, str]:
    global _cached_token, _cached_token_expires_at, _cached_auth_mode
    with _token_lock:
        now = time.monotonic()
        if (
            not force
            and _cached_token
            and _cached_auth_mode
            and now < _cached_token_expires_at
        ):
            return _cached_token, _cached_auth_mode
        token, mode = _fetch_token()
        _cached_token = token
        _cached_auth_mode = mode
        _cached_token_expires_at = time.monotonic() + _TOKEN_TTL_SECONDS
        return token, mode


async def _get_access_token(*, force: bool = False) -> tuple[str, str]:
    if not force:
        now = time.monotonic()
        with _token_lock:
            if _cached_token and _cached_auth_mode and now < _cached_token_expires_at:
                return _cached_token, _cached_auth_mode
    return await asyncio.to_thread(_mint_token, force=force)


async def warm_access_token() -> None:
    """Mint and cache a token outside the retrieve ``wait_for``.

    No-op under pytest so ``plan_search`` tests do not spawn gcloud.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        await _get_access_token()
    except DiscoveryEngineConfigError as exc:
        LOGGER.warning("Discovery Engine token warm failed: %s", exc)


def _parse_results(data: dict[str, Any], *, num_results: int) -> list[WebSearchResult]:
    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        raise DiscoveryEngineError("Discovery Engine response missing `results` list.")
    results: list[WebSearchResult] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        document = item.get("document")
        if not isinstance(document, dict):
            continue
        derived = document.get("derivedStructData")
        if not isinstance(derived, dict):
            continue
        title = _plain_text(derived.get("title")) or _plain_text(derived.get("htmlTitle"))
        link = _plain_text(derived.get("link")) or _plain_text(derived.get("formattedUrl"))
        snippet = ""
        snippets = derived.get("snippets")
        if isinstance(snippets, list):
            for entry in snippets:
                if isinstance(entry, dict):
                    snippet = _plain_text(entry.get("snippet"))
                    if snippet:
                        break
        if not title or not link:
            continue
        display_link = _plain_text(derived.get("displayLink"))
        results.append(
            WebSearchResult(
                title=title,
                link=link,
                snippet=snippet,
                domain=display_link or extract_domain_from_url(link),
            )
        )
        if len(results) >= num_results:
            break
    return results


async def search_google_discovery_engine(
    query: str,
    *,
    num_results: int,
    search_options: SearchOptions | None = None,
    http_client: httpx.AsyncClient | None = None,
    serving_config: str | None = None,
    **kwargs: Any,
) -> list[WebSearchResult]:
    """POST ``servingConfigs/*:search`` and return parsed web results."""
    del kwargs
    config = (serving_config or "").strip()
    if not config:
        raise DiscoveryEngineConfigError("Discovery Engine call missing serving_config.")
    url = f"{_API_ROOT}/{config}:search"
    page_size = max(1, min(int(num_results), 20))
    payload: dict[str, Any] = {
        "query": query,
        "pageSize": page_size,
        "queryExpansionSpec": {"condition": "AUTO"},
        "spellCorrectionSpec": {"mode": "AUTO"},
        "contentSearchSpec": {"snippetSpec": {"returnSnippet": True}},
    }
    if search_options is not None:
        if search_options.language:
            payload["languageCode"] = search_options.language
        if search_options.region:
            payload["regionCode"] = search_options.region.upper()
    quota_project = (
        settings.discovery_engine_quota_project or ""
    ).strip() or "magdalenka-ecosystem"

    async def _do_request(client: httpx.AsyncClient) -> dict[str, Any]:
        token, auth_mode = await _get_access_token()
        metadata = get_provider_request_metadata() or ProviderRequestMetadata(
            provider=PROVIDER_NAME
        )
        set_provider_request_metadata(_with_metadata(metadata, endpoint=url, auth_mode=auth_mode))
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "x-goog-user-project": quota_project,
        }
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code == 401:
            reset_token_cache()
            token, auth_mode = await _get_access_token(force=True)
            set_provider_request_metadata(
                _with_metadata(
                    get_provider_request_metadata() or metadata,
                    endpoint=url,
                    auth_mode=auth_mode,
                )
            )
            headers["Authorization"] = f"Bearer {token}"
            response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise DiscoveryEngineError("Discovery Engine response was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise DiscoveryEngineError("Discovery Engine response was not a JSON object.")
        return data

    return await run_provider(
        PROVIDER_NAME,
        query,
        num_results,
        request=_do_request,
        parse_response=lambda data: _parse_results(data, num_results=num_results),
        http_client=http_client,
        max_retries=provider_retry_max_retries(PROVIDER_NAME),
    )
