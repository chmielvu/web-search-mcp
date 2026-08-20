"""Code-search cache tiers built on the server's existing cache backends."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any

from ..settings import settings
from ..telemetry import record_cache_lookup
from .exact_lru import ExactLRUCache
from .observability import emit_cache_lookup_event, emit_cache_store_event
from .page_cache import get_page_cache

LOGGER = logging.getLogger(__name__)

CODE_SEARCH_CACHE_VERSION = "code-search-v2"
_IMMUTABLE_REVISION = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def build_search_cache_key(request: Any, plan: Any) -> str:
    """Build a stable key from all request and compiled-plan inputs."""

    payload = {
        "version": CODE_SEARCH_CACHE_VERSION,
        "query": request.query,
        "research_goal": request.research_goal,
        "repositories": list(request.repositories),
        "language": request.language,
        "path": request.path,
        "filename": request.filename,
        "extension": request.extension,
        "regexp": request.regexp,
        "deep": request.deep,
        "repo_name": request.repo_name,
        "library_name": request.library_name,
        "topic": request.topic,
        "mode": request.mode,
        "huggingface_type": getattr(request, "huggingface_type", "both"),
        "huggingface_sort_by": getattr(request, "huggingface_sort_by", "similarity"),
        "huggingface_hybrid": getattr(request, "huggingface_hybrid", False),
        "huggingface_min_likes": getattr(request, "huggingface_min_likes", 0),
        "huggingface_min_downloads": getattr(request, "huggingface_min_downloads", 0),
        "huggingface_task": getattr(request, "huggingface_task", None),
        "huggingface_license": getattr(request, "huggingface_license", None),
        "huggingface_language": getattr(request, "huggingface_language", None),
        "huggingface_modified_after": getattr(request, "huggingface_modified_after", None),
        "huggingface_min_param_count": getattr(request, "huggingface_min_param_count", 0),
        "huggingface_max_param_count": getattr(request, "huggingface_max_param_count", None),
        "variants": list(getattr(plan, "variants", ())),
        "variant_pairs": list(getattr(plan, "variant_pairs", ())),
        "qualifiers": list(getattr(plan, "qualifiers", ())),
        "library_hint": getattr(plan, "library_hint", None),
        "repository_hint": getattr(plan, "repository_hint", None),
        "resolution_source": getattr(plan, "resolution_source", None),
    }
    return f"{CODE_SEARCH_CACHE_VERSION}:{_digest(payload)}"


def _blob_cache_key(repository: str, path: str, revision: str) -> str:
    payload = {
        "version": CODE_SEARCH_CACHE_VERSION,
        "repository": repository.strip().casefold(),
        "path": path.strip().replace("\\", "/"),
        "revision": revision.strip().casefold(),
    }
    return f"github://{payload['repository']}/{payload['path']}@{payload['revision']}"


def is_immutable_revision(revision: str | None) -> bool:
    """Only cache content permanently when its revision cannot move."""

    return bool(revision and _IMMUTABLE_REVISION.fullmatch(revision.strip()))


class CodeSearchCache:
    """Search-result LRU plus immutable GitHub hydration cache."""

    def __init__(
        self,
        *,
        page_cache: Any | None = None,
        search_ttl_seconds: int | None = None,
        search_max_entries: int | None = None,
        hydration_ttl_seconds: int | None = None,
    ) -> None:
        self.search_ttl_seconds = max(
            1,
            search_ttl_seconds
            if search_ttl_seconds is not None
            else settings.code_search_cache_ttl_seconds,
        )
        self.hydration_ttl_seconds = max(
            1,
            hydration_ttl_seconds
            if hydration_ttl_seconds is not None
            else settings.code_search_hydration_cache_ttl_seconds,
        )
        self._search = ExactLRUCache(
            max_entries=max(
                1,
                search_max_entries
                if search_max_entries is not None
                else settings.code_search_cache_max_entries,
            ),
            default_ttl_seconds=self.search_ttl_seconds,
        )
        self._page_cache = page_cache

    @property
    def page_cache(self) -> Any:
        if self._page_cache is None:
            self._page_cache = get_page_cache()
        return self._page_cache

    def lookup_search(self, key: str) -> dict[str, Any] | None:
        started = time.monotonic()
        try:
            value = self._search.lookup(key, 1, False, "code_search", "v1")
        except Exception as exc:  # pragma: no cover - defensive cache isolation
            LOGGER.warning("Code-search cache lookup failed: %s", exc)
            value = None
        duration = time.monotonic() - started
        hit = value is not None
        record_cache_lookup("code_search", hit, duration)
        emit_cache_lookup_event(
            LOGGER,
            "code_search",
            "hit" if hit else "miss",
            duration_ms=round(duration * 1000, 3),
            cache_key=key,
            tier="search",
        )
        return value

    def store_search(self, key: str, response: dict[str, Any]) -> None:
        try:
            self._search.store(
                key,
                1,
                False,
                "code_search",
                "v1",
                response,
                self.search_ttl_seconds,
            )
            emit_cache_store_event(
                LOGGER,
                "code_search",
                "ok",
                ttl_seconds=self.search_ttl_seconds,
                cache_key=key,
                tier="search",
            )
        except Exception as exc:  # pragma: no cover - defensive cache isolation
            LOGGER.warning("Code-search cache store failed: %s", exc)
            emit_cache_store_event(
                LOGGER,
                "code_search",
                "error",
                cache_key=key,
                tier="search",
                error_type=type(exc).__name__,
            )

    async def lookup_hydration(
        self,
        repository: str,
        path: str,
        revision: str | None,
    ) -> dict[str, Any] | None:
        if not is_immutable_revision(revision):
            return None
        key = _blob_cache_key(repository, path, revision or "")
        started = time.monotonic()
        try:
            cached = await self.page_cache.alookup(key)
        except Exception as exc:  # pragma: no cover - defensive cache isolation
            LOGGER.warning("Code-search hydration cache lookup failed: %s", exc)
            cached = None
        duration = time.monotonic() - started
        hit = bool(cached and isinstance(cached.get("page_content"), str))
        record_cache_lookup("code_search_hydration", hit, duration)
        emit_cache_lookup_event(
            LOGGER,
            "code_search_hydration",
            "hit" if hit else "miss",
            duration_ms=round(duration * 1000, 3),
            cache_key=key,
            tier="hydration",
        )
        if not hit:
            return None
        assert cached is not None
        return {
            "text": cached["page_content"],
            "metadata": cached.get("metadata") if isinstance(cached.get("metadata"), dict) else {},
        }

    async def store_hydration(
        self,
        repository: str,
        path: str,
        revision: str | None,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not is_immutable_revision(revision) or not text:
            return
        key = _blob_cache_key(repository, path, revision or "")
        try:
            await self.page_cache.astore(
                key,
                text,
                "github_blob",
                metadata={
                    "repository": repository,
                    "path": path,
                    "revision": revision,
                    **(metadata or {}),
                },
                ttl_seconds=self.hydration_ttl_seconds,
            )
            emit_cache_store_event(
                LOGGER,
                "code_search_hydration",
                "ok",
                ttl_seconds=self.hydration_ttl_seconds,
                cache_key=key,
                tier="hydration",
            )
        except Exception as exc:  # pragma: no cover - defensive cache isolation
            LOGGER.warning("Code-search hydration cache store failed: %s", exc)
            emit_cache_store_event(
                LOGGER,
                "code_search_hydration",
                "error",
                cache_key=key,
                tier="hydration",
                error_type=type(exc).__name__,
            )

    def search_entry_count(self) -> int:
        return self._search.entry_count()


_CODE_SEARCH_CACHE: CodeSearchCache | None = None


def get_code_search_cache() -> CodeSearchCache:
    """Return the process-local code-search cache facade."""

    global _CODE_SEARCH_CACHE
    if _CODE_SEARCH_CACHE is None:
        _CODE_SEARCH_CACHE = CodeSearchCache()
    return _CODE_SEARCH_CACHE


def reset_code_search_cache() -> None:
    """Reset the singleton for tests and controlled process reconfiguration."""

    global _CODE_SEARCH_CACHE
    _CODE_SEARCH_CACHE = None


__all__ = [
    "CODE_SEARCH_CACHE_VERSION",
    "CodeSearchCache",
    "build_search_cache_key",
    "get_code_search_cache",
    "is_immutable_revision",
    "reset_code_search_cache",
]
