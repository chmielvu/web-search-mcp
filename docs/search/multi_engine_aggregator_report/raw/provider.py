"""multi-web-provider — meta web provider (search + extract) with internal cascading.

Subclasses :class:`agent.web_search_provider.WebSearchProvider` and registers
as provider name ``multi-web-provider``. It does NOT talk to any API itself;
instead it *chains* the real Hermes providers internally, trying each in turn
and returning the first success — absorbing per-provider failures so agents
never see a "broken backend" error as long as one link in the chain answers.

Chains (per capability, ordered by preference / credit pools):

    search : tavily -> exa -> brave-free -> searxng -> ddgs
    extract: firecrawl -> exa

Each link is a real registered provider (``plugins.web.*.provider`` classes),
instantiated on demand. A keyed provider (tavily/exa/brave-free/firecrawl)
whose key is missing is treated as *unavailable* and skipped — it is NOT
routed to Hermes' keyless ring at the link level (the user's explicit
``web.keyless_fallback`` tier is honored globally instead). The Hermes
keyless vendor ring (``search_with_failover`` / ``extract_with_failover``)
is this provider's own last-resort net: it is invoked only after every link
in the chain has failed, and only when ``web.keyless_fallback`` is enabled.

Config to activate::

    web:
      backend: multi-web-provider     # shared (or search/extract_backend)

No runtime config mutation: all routing state lives in this plugin's
``state.json`` (per-capability position, cooldowns after failures).
Fails open: any internal error is logged and the chain continues; only when
every link fails does the error surface to the agent.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(PLUGIN_DIR, "state.json")

# Search chain: ordered by preference. Each entry maps to a real Hermes
# provider class (module path under plugins/web/<name>/provider.py).
SEARCH_CHAIN = ["tavily", "exa", "brave_free", "searxng", "ddgs"]
# Extract chain: firecrawl first (best extractor), exa second (separate
# credits). SearXNG / DDGS / Brave are search-only (supports_extract False).
EXTRACT_CHAIN = ["firecrawl", "exa"]

# Cooldown (seconds) applied to a link after a failure, so a dead/quota-exhausted
# provider is skipped for a while instead of retried on every call.
COOLDOWN_SECONDS = 300  # 5 min
# How long a successful link stays preferred before we allow rotating to
# spread credit usage across providers (0 = never rotate on success).
ROTATE_AFTER_SUCCESS_SECONDS = 600  # 10 min


class _ProviderLink:
    """A single chain link: a lazily-instantiated real Hermes provider."""

    def __init__(self, module_path: str, class_name: str) -> None:
        self.module_path = module_path
        self.class_name = class_name
        self._provider: Optional[WebSearchProvider] = None

    def get(self) -> Optional[WebSearchProvider]:
        if self._provider is not None:
            return self._provider
        try:
            import importlib

            module = importlib.import_module(self.module_path)
            cls = getattr(module, self.class_name)
            self._provider = cls()
            return self._provider
        except Exception as exc:  # noqa: BLE001 — plugin may be absent
            logger.warning("multi-web-provider: cannot load %s: %s", self.module_path, exc)
            return None


def _link_spec(name: str) -> tuple:
    """Map a chain name to (module_path, class_name) of the real provider."""
    return {
        "tavily": ("plugins.web.tavily.provider", "TavilyWebSearchProvider"),
        "exa": ("plugins.web.exa.provider", "ExaWebSearchProvider"),
        "brave_free": ("plugins.web.brave_free.provider", "BraveFreeWebSearchProvider"),
        "searxng": ("plugins.web.searxng.provider", "SearXNGWebSearchProvider"),
        "ddgs": ("plugins.web.ddgs.provider", "DDGSWebSearchProvider"),
        "firecrawl": ("plugins.web.firecrawl.provider", "FirecrawlWebSearchProvider"),
    }[name]


# Providers whose primary access requires an API key. A keyed provider with no
# key is *unavailable* to the cascade (never routed to the keyless ring at the
# link level) — the ring is reached only after the whole keyed chain fails.
# NOTE: these are the providers' own ``name`` values (provider.name), NOT
# module/chain names — brave_free's module is ``brave_free`` but its name is
# ``brave-free`` (hyphen). Using the wrong form silently breaks exclusion.
_KEYED_PROVIDERS = {"tavily", "exa", "brave-free", "firecrawl"}


def _requires_key(provider: WebSearchProvider) -> bool:
    """Return True when *provider* is a keyed provider (needs its own API key).

    Keyed providers (tavily/exa/brave/firecrawl) are excluded from the cascade
    when their key is missing — they are 'unavailable', not keyless-routed.
    Keyless-native providers (ddgs; searxng with a URL) never require a key and
    are always tryable.
    """
    name = getattr(provider, "name", "") or ""
    if name in _KEYED_PROVIDERS:
        return True
    # Fallback heuristic for unknown providers: a provider is considered
    # keyless-native only when it does NOT consult an API key to decide
    # availability. Providers with an is_keyless_available() method that
    # reports True *while is_available() is False* are keyed providers whose
    # key is missing — treat them as requiring a key.
    try:
        if not provider.is_available() and provider.is_keyless_available():
            return True
    except Exception:
        pass
    return False


def _is_grave(msg: str) -> bool:
    """True when *msg* is a GRAVE provider failure — one that will keep
    failing until the operator intervenes (missing/invalid API key, account
    out of credits/quota). Grave failures get a cooldown so the provider is
    not retried pointlessly on every call.

    Everything else — 404s, per-URL crawl failures, rate limits, timeouts,
    5xx, transient network errors — is NOT grave: the plugin must move to
    the next provider in the chain (and eventually the core keyless ring)
    WITHOUT cooling the provider down. A temporary blip or a dead URL must
    never take a provider out of rotation.
    """
    if not msg:
        return False
    low = msg.lower()
    # Missing / invalid key or auth failure.
    if any(k in low for k in (
        "api key", "apikey", "key missing", "missing key", "no key",
        "set exa_api_key", "set firecrawl_api_key", "set tavily_api_key",
        "unauthorized", "invalid key", "auth", "401", "403", "forbidden",
    )):
        # An auth-looking message that is really about a page (e.g. a 403
        # from the target website itself) is NOT a provider-key problem.
        if any(k in low for k in ("page", "url", "crawl", "website", "site")):
            return False
        return True
    # Out of credits / quota / billing.
    if any(k in low for k in (
        "credit", "quota", "payment", "billing", "402", "insufficient",
        "no credits", "out of credits", "exceeded", "upgrade your plan",
    )):
        return True
    # Explicitly NOT grave: transient / per-URL conditions.
    return False


class _State:
    """Per-capability routing state, persisted to state.json."""

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {"search": {}, "extract": {}}
        self._load()

    def _load(self) -> None:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self._data = {
                    "search": data.get("search") or {},
                    "extract": data.get("extract") or {},
                }
        except Exception:
            pass

    def _save(self) -> None:
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
        except Exception:
            pass

    def link_failed(self, capability: str, link_name: str) -> None:
        cap = self._data.setdefault(capability, {})
        cap[link_name] = {"cooldown_until": time.time() + COOLDOWN_SECONDS}
        self._save()

    def link_succeeded(self, capability: str, link_name: str) -> None:
        cap = self._data.setdefault(capability, {})
        cap[link_name] = {"last_success": time.time()}
        self._save()

    def is_cooling_down(self, capability: str, link_name: str) -> bool:
        cap = self._data.setdefault(capability, {})
        entry = cap.get(link_name) or {}
        return bool(entry.get("cooldown_until", 0) > time.time())

    def should_rotate(self, capability: str, link_name: str) -> bool:
        """Return True when *link_name* succeeded long ago enough that we may
        rotate to spread credit usage (only when ROTATE_AFTER_SUCCESS_SECONDS>0)."""
        if not ROTATE_AFTER_SUCCESS_SECONDS:
            return False
        cap = self._data.setdefault(capability, {})
        entry = cap.get(link_name) or {}
        last = entry.get("last_success", 0)
        return bool(last and time.time() - last > ROTATE_AFTER_SUCCESS_SECONDS)


class MultiWebProvider(WebSearchProvider):
    """Meta provider chaining the real Hermes web providers internally."""

    def __init__(self) -> None:
        self._state = _State()

    @property
    def name(self) -> str:
        return "multi-web-provider"

    @property
    def display_name(self) -> str:
        return "Multi Web Provider (cascade)"

    def is_available(self) -> bool:
        """Always available: at least one link (searxng/ddgs) needs no key."""
        return True

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def _links(self, capability: str) -> List[_ProviderLink]:
        chain = SEARCH_CHAIN if capability == "search" else EXTRACT_CHAIN
        return [_ProviderLink(*_link_spec(name)) for name in chain]

    def _chain_names(self, capability: str) -> List[str]:
        return SEARCH_CHAIN if capability == "search" else EXTRACT_CHAIN

    # -- internal cascade ----------------------------------------------------

    def _ordered_links(self, capability: str) -> List[_ProviderLink]:
        """Return chain links for this call: fixed chain order, skipping links
        currently in cooldown (recent failure). No stickiness — a link that
        failed gets a 5-min cooldown, then the fixed order resumes (premium
        providers are always tried first again)."""
        names = self._chain_names(capability)
        links = {n: _ProviderLink(*_link_spec(n)) for n in names}

        ordered = []
        for name in names:
            if self._state.is_cooling_down(capability, name):
                logger.debug("multi-web-provider: %s cooling down; skip", name)
                continue
            ordered.append(links[name])
        return ordered

    def _state_data(self, capability: str) -> Dict[str, Any]:
        return self._state._data.setdefault(capability, {})

    # -- WebSearchProvider API ------------------------------------------------
    #
    # NOTE: search() is SYNC by contract (Hermes' web_search_tool dispatcher
    # calls provider.search() synchronously — no await, no coroutine
    # detection — so an async search() would crash it). extract() is async,
    # which the extract dispatcher supports. Internally both cascade cores
    # are async and each link is invoked through _call(), which awaits
    # coroutines transparently (firecrawl's extract is async; tavily/exa
    # search are sync).

    @staticmethod
    async def _call(fn, *args, **kwargs):
        """Invoke a provider method whether it is sync or async."""
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    @staticmethod
    def _run(coro) -> Any:
        """Run an async helper to completion from a sync context.

        Uses asyncio.run() when no loop is running (the normal path: Hermes'
        web_search_tool dispatcher is sync and calls provider.search() from a
        plain function). When a loop IS already running (sync method invoked
        from async code), run the coroutine in a worker thread with its own
        loop rather than returning a coroutine the sync caller cannot await.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        # A loop is running — execute in a fresh thread + loop so the sync
        # contract still holds for the caller.
        import threading

        result_holder: Dict[str, Any] = {}

        def _runner() -> None:
            result_holder["result"] = asyncio.run(coro)

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        thread.join()
        return result_holder.get("result")

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Execute a search through the cascade.

        SYNC by contract: Hermes' ``web_search_tool`` dispatcher calls
        ``provider.search()`` synchronously (no await, no coroutine
        detection) — an async search() would crash the dispatcher with
        "'coroutine' object has no attribute 'get'". The cascade core is
        async internally (some links are async); _run() bridges it.
        """
        return self._run(self._search_async(query, limit))

    async def _search_async(self, query: str, limit: int = 5) -> Dict[str, Any]:
        errors: List[str] = []
        for link in self._ordered_links("search"):
            provider = link.get()
            if provider is None:
                continue
            if not provider.supports_search():
                continue
            if not self._can_try("search", link):
                continue
            try:
                result = await self._call(provider.search, query, limit=limit)
            except Exception as exc:  # noqa: BLE001 — never let a link raise
                logger.warning("multi-web-provider: %s.search raised: %s", link.class_name, exc)
                if _is_grave(str(exc)):
                    self._state.link_failed("search", self._link_name(link))
                errors.append(f"{link.class_name}: {exc}")
                continue
            if isinstance(result, dict) and result.get("success"):
                # success — mark and return (absorb any prior failures)
                self._state.link_succeeded("search", self._link_name(link))
                data = result.get("data") or {}
                data["served_by"] = self._link_name(link)
                return {"success": True, "data": data}
            # failure — move to next link; cooldown only on GRAVE errors
            # (missing/invalid key, out of credits) so a transient blip or a
            # bad query never takes a provider out of rotation.
            err = result.get("error") if isinstance(result, dict) else str(result)
            if _is_grave(str(err)):
                self._state.link_failed("search", self._link_name(link))
            errors.append(f"{link.class_name}: {err}")
            logger.info("multi-web-provider: %s failed (%s); trying next", link.class_name, err)
        # Every keyed/keyless-native link failed. Last-resort net: the Hermes
        # keyless ring, reached ONLY now — never as a first resort for a keyed
        # provider that merely lacks its key (see _can_try). If the ring
        # answers, we absorb the chain failures transparently.
        ring = await self._ring_search(query, limit)
        if ring.get("success"):
            return ring
        # every link AND the ring failed — surface an honest, actionable error
        errors.append(f"keyless-ring: {ring.get('error')}")
        return {
            "success": False,
            "error": (
                "multi-web-provider: all search providers failed "
                f"({'; '.join(errors) or 'no providers available'})"
            ),
        }

    async def _ring_search(self, query: str, limit: int) -> Dict[str, Any]:
        """Last-resort keyless search across the Hermes vendor ring.

        Honors ``web.keyless_fallback`` (via keyless_mcp.keyless_enabled):
        when the user disabled the keyless tier, the ring is NOT consulted
        and the chain's honest error surfaces instead — the meta-provider
        never overrides an explicit configuration decision.
        """
        try:
            from plugins.web.keyless_mcp import keyless_enabled, search_with_failover

            if not keyless_enabled():
                logger.info("multi-web-provider: keyless fallback disabled; skipping ring")
                return {"success": False, "error": "keyless fallback disabled (web.keyless_fallback: false)"}
            return await self._call(search_with_failover, "multi-web-provider", query, limit=limit)
        except Exception as exc:  # noqa: BLE001 — ring must never raise
            logger.warning("multi-web-provider: keyless ring search failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        errors: List[str] = []
        for link in self._ordered_links("extract"):
            provider = link.get()
            if provider is None:
                continue
            if not provider.supports_extract():
                continue
            if not self._can_try("extract", link):
                continue
            try:
                result = await self._call(provider.extract, urls, **kwargs)
            except Exception as exc:  # noqa: BLE001
                logger.warning("multi-web-provider: %s.extract raised: %s", link.class_name, exc)
                if _is_grave(str(exc)):
                    self._state.link_failed("extract", self._link_name(link))
                errors.append(f"{link.class_name}: {exc}")
                continue
            # extract returns a list of per-URL docs (one per input URL).
            # Verdicts:
            #   * any doc WITHOUT an error  -> link succeeded, return its
            #     verdicts as-is (partial failures are per-URL page problems,
            #     NOT link failures — the caller sees per-URL errors).
            #   * docs present, ALL with error -> distinguish per-URL page
            #     problems from backend outages. Per-URL errors (404,
            #     CRAWL_NOT_FOUND) are honest verdicts from a healthy link:
            #     return them as-is, NO cooldown. Backend failures (402
            #     credits exhausted, 429 rate limit, 5xx...) mean the whole
            #     link is dead: cooldown it and try the next provider.
            #   * [] or non-list -> broken link (a healthy extractor never
            #     returns zero docs for a non-empty URL list): cooldown,
            #     next link.
            if isinstance(result, list):
                if result:
                    ok_docs = [d for d in result if isinstance(d, dict) and not d.get("error")]
                    if ok_docs:
                        self._state.link_succeeded("extract", self._link_name(link))
                        return result
                    errs = [d.get("error") or "" for d in result if isinstance(d, dict)]
                    if errs and all(_is_grave(e) for e in errs):
                        # GRAVE failure (missing/invalid key, out of credits):
                        # cooldown the link and fall through to the next one.
                        self._state.link_failed("extract", self._link_name(link))
                        errors.append(f"{link.class_name}: {'; '.join(errs)}")
                        logger.info(
                            "multi-web-provider: %s extract GRAVE failure (%s); cooling down",
                            link.class_name, "; ".join(errs)[:160],
                        )
                        continue
                    # Non-grave all-error result (e.g. every URL is a 404):
                    # healthy link, per-URL verdicts — return them as-is,
                    # NO cooldown (a dead URL must not take the provider
                    # out of rotation).
                    return result
                err = f"unexpected extract return: {result!r}"
            else:
                err = f"unexpected extract return: {result!r}"
            # [] / non-list = BROKEN CONTRACT (a healthy extractor always
            # returns one doc per URL). This is not a transient blip — the
            # link is misbehaving and must be cooled down regardless of
            # _is_grave, else it would be retried on every single call.
            self._state.link_failed("extract", self._link_name(link))
            errors.append(f"{link.class_name}: {err}")
            logger.info("multi-web-provider: %s extract failed (%s); trying next", link.class_name, err)
        # Last-resort net: the keyless ring, only after every keyed extractor
        # (firecrawl, exa) failed. Absorbed transparently when it answers.
        ring = await self._ring_extract(urls, **kwargs)
        if ring and any(isinstance(d, dict) and not d.get("error") for d in ring):
            return ring
        ring_err = ring[0].get("error") if ring and isinstance(ring[0], dict) else "no result"
        errors.append(f"keyless-ring: {ring_err}")
        return [
            {
                "url": u,
                "title": "",
                "content": "",
                "error": (
                    "multi-web-provider: all extract providers failed "
                    f"({'; '.join(errors) or 'no providers available'})"
                ),
            }
            for u in urls
        ]

    async def _ring_extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        """Last-resort keyless extract across the Hermes vendor ring.

        Honors ``web.keyless_fallback`` (via keyless_mcp.keyless_enabled):
        when the user disabled the keyless tier, the ring is NOT consulted
        and the chain's honest error surfaces instead.
        """
        try:
            from plugins.web.keyless_mcp import extract_with_failover, keyless_enabled

            if not keyless_enabled():
                logger.info("multi-web-provider: keyless fallback disabled; skipping ring")
                err = "keyless fallback disabled (web.keyless_fallback: false)"
                return [{"url": u, "title": "", "content": "", "error": err} for u in urls]
            return await self._call(extract_with_failover, "multi-web-provider", urls)
        except Exception as exc:  # noqa: BLE001 — ring must never raise
            logger.warning("multi-web-provider: keyless ring extract failed: %s", exc)
            return [
                {"url": u, "title": "", "content": "", "error": str(exc)} for u in urls
            ]

    def _link_name(self, link: _ProviderLink) -> str:
        """Return the stable provider name for a link (robust to any module path)."""
        provider = link.get()
        if provider is not None:
            try:
                return provider.name
            except Exception:
                pass
        parts = link.module_path.split(".")
        # plugins.web.<vendor>.provider -> vendor ; FAILING -> last segment
        if len(parts) >= 3 and parts[0] == "plugins" and parts[1] == "web":
            return parts[2]
        return parts[-1] if parts else link.class_name

    def _can_try(self, capability: str, link: _ProviderLink) -> bool:
        name = self._link_name(link)
        if self._state.is_cooling_down(capability, name):
            logger.debug("multi-web-provider: %s cooling down; skip", name)
            return False
        provider = link.get()
        if provider is None:
            return False
        # A link is tryable only when it can serve on its OWN credentials
        # (keyed provider with key set, or a genuinely keyless provider such
        # as searxng/ddgs). A keyed provider whose key is missing is simply
        # *unavailable* here — we do NOT route it to the keyless ring at this
        # stage. The ring is the last-resort net: it is reached only after
        # every keyed link in the chain has failed (see search()/extract()).
        try:
            if provider.is_available():
                return True
            # Keyless-native providers (ddgs, searxng with URL) report
            # is_available() True already; but be tolerant of providers that
            # only express keyless availability when the capability truly
            # needs no key — never a keyed provider missing its key.
            if not _requires_key(provider):
                return True
            return False
        except Exception:
            # If availability cannot be determined, prefer trying the link
            # over dropping it — a real call failure is handled downstream.
            return True

    # -- setup schema for hermes tools picker ---------------------------------

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Multi Web Provider (cascade)",
            "badge": "cascade",
            "tag": (
                "Chains Tavily→Exa→Brave→SearXNG→DDGS (search) and "
                "Firecrawl→Exa (extract), absorbing failures internally. "
                "Uses the real providers' own credentials."
            ),
            "env_vars": [],
        }


# Module-level instance (kept for import-time inspection; the plugin registers
# a fresh one via register()).
_instance: Optional[MultiWebProvider] = None


def get_provider() -> MultiWebProvider:
    global _instance
    if _instance is None:
        _instance = MultiWebProvider()
    return _instance
