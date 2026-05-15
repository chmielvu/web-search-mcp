"""Search providers: SearXNG (primary) + DDG (free fallback) → Paid providers (conditional).

Uses Reciprocal Rank Fusion (RRF) for multi-provider result merging.
Includes circuit breaker and budget tracking for provider health.

Provider modes control when providers fire:
- ALWAYS: Free providers (SearXNG, DDG) always fire
- CONDITIONAL: Paid providers only fire when caller requests via providers param
- NEVER: Disabled providers never fire
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..models import WebSearchResult
from ..settings import settings
from ..telemetry import record_circuit_breaker_event, record_circuit_breaker_state
from ..utils.diagnostics import Diagnostics
from .brave import search_brave
from .composio_llm_search import search_composio_llm_search
from .ddg import search_ddg
from .gemini_pollinations import search_gemini_pollinations
from .github_graphql import search_github_graphql
from .hackernews import search_hackernews
from .jina import search_jina
from .reddit import search_reddit
from .stackexchange import search_stackexchange
from .merge import merge_search_results
from .provider_config import ProviderConfig, ProviderMode, parse_provider_mode, register_provider, resolve_providers_for_search
from .searxng import search_searxng
from .tavily import search_tavily

LOGGER = logging.getLogger(__name__)


class WebSearchProviderError(RuntimeError):
    pass




# =============================================================================
# Circuit Breaker
# =============================================================================

@dataclass
class CircuitBreaker:
    """Per-provider circuit breaker. Opens after N consecutive failures."""
    failure_threshold: int = 3
    reset_timeout_seconds: float = 60.0
    _failures: dict[str, int] = field(default_factory=dict)
    _opened_at: dict[str, float] = field(default_factory=dict)

    def is_open(self, provider: str) -> bool:
        if provider not in self._opened_at:
            # Record closed state
            record_circuit_breaker_state(provider, "closed", self._failures.get(provider, 0))
            return False
        if time.time() - self._opened_at[provider] > self.reset_timeout_seconds:
            # Circuit is transitioning from open to half-open (resetting)
            del self._opened_at[provider]
            self._failures[provider] = 0
            record_circuit_breaker_state(provider, "half_open", 0)
            record_circuit_breaker_event(provider, "half_open", self.failure_threshold)
            LOGGER.info(f"Circuit breaker HALF_OPEN for {provider} after reset timeout")
            return False
        # Circuit is open
        record_circuit_breaker_state(provider, "open", self._failures.get(provider, 0))
        return True

    def record_success(self, provider: str) -> None:
        prev_failures = self._failures.get(provider, 0)
        was_open = provider in self._opened_at
        self._failures[provider] = 0
        self._opened_at.pop(provider, None)
        # Record reset event if circuit was previously open
        if was_open:
            record_circuit_breaker_event(provider, "reset", self.failure_threshold)
            LOGGER.info(f"Circuit breaker RESET for {provider} after success")
        record_circuit_breaker_state(provider, "closed", 0)

    def record_failure(self, provider: str) -> None:
        self._failures[provider] = self._failures.get(provider, 0) + 1
        failure_count = self._failures[provider]

        if failure_count >= self.failure_threshold:
            # Circuit trips from closed to open
            self._opened_at[provider] = time.time()
            record_circuit_breaker_state(provider, "open", failure_count)
            record_circuit_breaker_event(provider, "trip", self.failure_threshold)
            LOGGER.warning(f"Circuit breaker OPEN for {provider} after {failure_count} failures")
        else:
            # Still closed but accumulating failures
            record_circuit_breaker_state(provider, "closed", failure_count)


# =============================================================================
# Provider Budget
# =============================================================================

@dataclass
class ProviderBudget:
    """Tracks per-provider calls and auto-demotion on poor performance."""
    max_calls_per_query: int = 3
    stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    _demoted: set[str] = field(default_factory=set)

    def can_spend(self, provider: str) -> bool:
        if provider in self._demoted:
            return False
        s = self.stats.get(provider)
        if s is None:
            return True
        if s["calls"] >= self.max_calls_per_query:
            return False
        # Auto-demotion: >50% failure rate after 2+ calls
        if s["calls"] >= 2 and s["failures"] / s["calls"] > 0.5:
            self._demoted.add(provider)
            return False
        return True

    def record_call(self, provider: str, success: bool) -> None:
        if provider not in self.stats:
            self.stats[provider] = {"calls": 0, "failures": 0}
        self.stats[provider]["calls"] += 1
        if not success:
            self.stats[provider]["failures"] += 1

    def reset(self) -> None:
        self.stats.clear()
        self._demoted.clear()


# =============================================================================
# Provider Registry
# =============================================================================

def _parse_mode(mode_str: str) -> ProviderMode:
    """Parse mode string to ProviderMode. Defaults to ALWAYS if invalid."""
    parsed = parse_provider_mode(mode_str)
    return parsed if parsed else ProviderMode.ALWAYS


def _init_provider_registry() -> None:
    """Initialize provider registry with configured modes."""
    # Tier 1: Free providers (default always, configurable via env)
    register_provider(ProviderConfig(
        name="searxng",
        mode=ProviderMode.ALWAYS,
        env_key="SEARXNG_BASE_URL",
        search_fn=search_searxng,
        is_free=True,
        requires_key=False,
    ))
    register_provider(ProviderConfig(
        name="ddg",
        mode=_parse_mode(settings.ddg_mode),  # default "always" in settings.py
        env_key="",  # No env key needed
        search_fn=search_ddg,
        is_free=True,
        requires_key=False,
    ))

    # Tier 2: Paid providers (mode from settings.py defaults)
    register_provider(ProviderConfig(
        name="tavily",
        mode=_parse_mode(settings.tavily_mode),  # default "never" in settings.py
        env_key="TAVILY_API_KEY",
        search_fn=search_tavily,
        is_free=False,
        requires_key=True,
    ))
    register_provider(ProviderConfig(
        name="brave",
        mode=_parse_mode(settings.brave_mode),  # default "never" in settings.py
        env_key="BRAVE_API_KEY",
        search_fn=search_brave,
        is_free=False,
        requires_key=True,
    ))
    register_provider(ProviderConfig(
        name="jina",
        mode=_parse_mode(settings.jina_mode),  # default "conditional" in settings.py
        env_key="JINA_API_KEY",
        search_fn=search_jina,
        is_free=False,
        requires_key=True,
    ))
    register_provider(ProviderConfig(
        name="gemini",
        mode=_parse_mode(settings.gemini_mode),  # default "always" in settings.py
        env_key="POLLINATIONS_API_KEY",
        search_fn=search_gemini_pollinations,
        is_free=False,
        requires_key=True,
    ))
    register_provider(ProviderConfig(
        name="composio_llm_search",
        mode=_parse_mode(settings.composio_llm_search_mode),  # default "always" in settings.py
        env_key="COMPOSIO_API_KEY",
        search_fn=search_composio_llm_search,
        is_free=False,
        requires_key=True,
        extra_env_keys=("KINDLY_COMPOSIO_USER_ID",),
    ))

    # Tier 3: Community providers (CONDITIONAL — only fire when explicitly requested)
    register_provider(ProviderConfig(
        name="hackernews",
        mode=ProviderMode.CONDITIONAL,
        env_key="",
        search_fn=search_hackernews,
        is_free=True,
        requires_key=False,
    ))
    register_provider(ProviderConfig(
        name="reddit",
        mode=ProviderMode.CONDITIONAL,
        env_key="",
        search_fn=search_reddit,
        is_free=True,
        requires_key=False,
    ))
    register_provider(ProviderConfig(
        name="github_graphql",
        mode=ProviderMode.CONDITIONAL,
        env_key="GITHUB_TOKEN",
        search_fn=search_github_graphql,
        is_free=True,
        requires_key=True,
    ))
    register_provider(ProviderConfig(
        name="stackexchange",
        mode=ProviderMode.CONDITIONAL,
        env_key="STACKEXCHANGE_APP_KEY",
        search_fn=search_stackexchange,
        is_free=True,
        requires_key=False,
    ))


# Initialize registry at module load
_init_provider_registry()


# =============================================================================
# Provider Detection (kept for backwards compat with existing detection)
# =============================================================================

def _has_searxng_config() -> bool:
    return bool(os.environ.get("SEARXNG_BASE_URL", "").strip())


def _has_tavily_key() -> bool:
    return bool(os.environ.get("TAVILY_API_KEY", "").strip())


def _has_brave_key() -> bool:
    return bool(os.environ.get("BRAVE_API_KEY", "").strip())


def _has_jina_key() -> bool:
    return bool(os.environ.get("JINA_API_KEY", "").strip())


# =============================================================================
# Main Search Function
# =============================================================================

_circuit_breaker = CircuitBreaker()


async def _search_single_provider(
    provider_name: str,
    provider_fn: Any,
    query: str,
    num_results: int,
    http_client: httpx.AsyncClient,
    budget: ProviderBudget | None = None,
) -> list[WebSearchResult]:
    """Search a single provider with circuit breaker and budget tracking."""
    if _circuit_breaker.is_open(provider_name):
        LOGGER.debug(f"Circuit breaker open for {provider_name}, skipping")
        return []

    if budget is not None and not budget.can_spend(provider_name):
        LOGGER.debug(f"Budget exhausted for {provider_name}, skipping")
        return []

    try:
        results = await provider_fn(query, num_results=num_results, http_client=http_client)
        results = [
            result.model_copy(
                update={
                    "providers": sorted({*(result.providers or []), provider_name}),
                }
            )
            for result in results
        ]
        _circuit_breaker.record_success(provider_name)
        if budget is not None:
            budget.record_call(provider_name, success=True)
        return results
    except Exception as e:
        _circuit_breaker.record_failure(provider_name)
        if budget is not None:
            budget.record_call(provider_name, success=False)
        LOGGER.warning(f"Provider {provider_name} failed: {e}")
        return []


async def search_single_query(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
    diagnostics: Diagnostics | None = None,
    providers: list[str] | None = None,
) -> list[WebSearchResult]:
    """
    Search using multi-provider RRF merge with mode-based selection.

    Provider priority:
    - Tier 1: SearXNG + DDG (always, free)
    - Tier 2: Paid providers (conditional or caller-requested)

    Args:
        query: Search query
        num_results: Target result count
        http_client: Optional HTTP client
        diagnostics: Optional diagnostics tracker
        providers: Optional list of provider names to explicitly include
            (e.g., ["tavily", "gemini"] to include conditional providers)
    """
    budget = ProviderBudget()  # Request-scoped, avoids race with concurrent requests

    # Resolve active providers based on mode + caller request
    active_configs = resolve_providers_for_search(providers)

    if not active_configs:
        raise WebSearchProviderError(
            "No search providers available. Configure SEARXNG_BASE_URL, "
            "or specify providers explicitly (e.g., providers=['tavily'])."
        )

    if diagnostics:
        diagnostics.emit(
            "search.provider_select",
            "Active providers for search",
            {
                "query": query,
                "num_results": num_results,
                "active_providers": [c.name for c in active_configs],
                "caller_providers": providers,
            },
        )

    async def _run(client: httpx.AsyncClient) -> list[WebSearchResult]:
        all_results: list[list[WebSearchResult]] = []

        # Separate free vs paid providers
        free_providers = [c for c in active_configs if c.is_free]
        paid_providers = [c for c in active_configs if not c.is_free]

        # Tier 1: Free providers always fire concurrently
        if free_providers:
            free_tasks = [
                _search_single_provider(
                    c.name, c.search_fn, query, num_results, client, budget
                )
                for c in free_providers
            ]
            free_results = await asyncio.gather(*free_tasks, return_exceptions=True)
            for r in free_results:
                if isinstance(r, list):
                    all_results.append(r)

        # Tier 2: Paid providers with semaphore (if any)
        if paid_providers:
            semaphore = asyncio.Semaphore(max(2, len(paid_providers)))

            async def _search_with_semaphore(config: ProviderConfig) -> list[WebSearchResult]:
                async with semaphore:
                    return await _search_single_provider(
                        config.name, config.search_fn, query, num_results, client, budget
                    )

            paid_results = await asyncio.gather(
                *[_search_with_semaphore(c) for c in paid_providers],
                return_exceptions=True,
            )
            for r in paid_results:
                if isinstance(r, list):
                    all_results.append(r)

        # Weighted RRF merge: always run through merge_search_results so that
        # host-cap deduplication is applied even for a single-provider result set.
        merged = merge_search_results(all_results) if all_results else []

        if diagnostics:
            diagnostics.emit(
                "search.rrf_merge",
                "RRF merge completed",
                {
                    "input_lists": len(all_results),
                    "output_count": len(merged),
                },
            )

        return merged[:num_results]

    if http_client is not None:
        return await _run(http_client)

    async with httpx.AsyncClient(timeout=30) as client:
        return await _run(client)


async def search_web(
    query: str,
    *,
    num_results: int,
    http_client: httpx.AsyncClient | None = None,
    diagnostics: Diagnostics | None = None,
    providers: list[str] | None = None,
) -> list[WebSearchResult]:
    """Backward-compatible alias for the single-query executor."""
    return await search_single_query(
        query,
        num_results=num_results,
        http_client=http_client,
        diagnostics=diagnostics,
        providers=providers,
    )
