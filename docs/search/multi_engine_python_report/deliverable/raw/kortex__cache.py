"""Redis-backed cache (keyed by query + sources + category)."""

from __future__ import annotations

import json
import logging
from typing import Any

import redis

from .config import CACHE_TTL, NEGATIVE_CACHE_TTL, REDIS_URL, SOURCE_CACHE_TTL

logger = logging.getLogger("kortex_search.cache")

_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            REDIS_URL, decode_responses=True,
            socket_connect_timeout=1.0, socket_timeout=2.0,
        )
    return _client


def _key(query: str, sources: list[str], category: str, limit: int, filters: str = "") -> str:
    suffix = f":{filters}" if filters else ""
    return f"ks:{category}:{','.join(sorted(sources))}:{limit}:{_norm_key_part(query)}{suffix}"


def _valid_payload(value) -> list[dict] | None:
    """Validate a cached result list — malformed/poisoned payloads are treated
    as a miss (schema validation, B6 hardening #10)."""
    if not isinstance(value, list):
        return None
    for item in value:
        if not isinstance(item, dict):
            return None
        if not isinstance(item.get("title"), str) or not isinstance(item.get("url"), str):
            return None
    return value


def get(query: str, sources: list[str], category: str, limit: int,
        filters: str = "") -> list[dict] | None:
    try:
        raw = _get_client().get(_key(query, sources, category, limit, filters))
    except redis.RedisError as exc:
        logger.debug("cache get error: %s", exc)
        return None
    if not raw:
        return None
    try:
        return _valid_payload(json.loads(raw))
    except json.JSONDecodeError:
        return None


def set(query: str, sources: list[str], category: str, limit: int, value: list[dict],
        filters: str = "") -> None:
    try:
        _get_client().set(
            _key(query, sources, category, limit, filters),
            json.dumps(value, ensure_ascii=False),
            ex=CACHE_TTL,
        )
    except redis.RedisError as exc:
        logger.debug("cache set error: %s", exc)


def ping() -> dict[str, Any]:
    """Health check for the doctor tool."""
    try:
        info = _get_client().info("server")
        return {"ok": True, "version": info.get("redis_version")}
    except redis.RedisError as exc:
        return {"ok": False, "error": str(exc)}


def _source_key(source: str, query: str, category: str, limit: int,
                filters: str = "") -> str:
    suffix = f":{filters}" if filters else ""
    return f"ks:s:{source}:{category}:{limit}:{_norm_key_part(query)}{suffix}"


def get_source(source: str, query: str, category: str, limit: int = 10,
               filters: str = "") -> list[dict] | None:
    """Per-source cache (granular — a query reusing any source hits it)."""
    key = _source_key(source, query, category, limit, filters)
    try:
        raw = _get_client().get(key)
    except redis.RedisError as exc:
        logger.debug("source cache get error: %s", exc)
        return None
    if not raw:
        return None
    try:
        return _valid_payload(json.loads(raw))
    except json.JSONDecodeError:
        return None


def set_source(source: str, query: str, category: str, value: list[dict],
               limit: int = 10, filters: str = "") -> None:
    key = _source_key(source, query, category, limit, filters)
    try:
        _get_client().set(key, json.dumps(value, ensure_ascii=False), ex=SOURCE_CACHE_TTL)
    except redis.RedisError as exc:
        logger.debug("source cache set error: %s", exc)


def _norm_key_part(s: str) -> str:
    """Normalize a cache-key part: strip control chars, lowercase, collapse
    whitespace — prevents near-identical queries from colliding on
    attacker-controlled formatting (semantic cache-poisoning defense)."""
    scrubbed = "".join(ch for ch in (s or "") if ch >= " " or ch in "\n\t")
    return " ".join(scrubbed.lower().split())


def mark_source_failed(source: str, query: str, category: str,
                       ttl: int = NEGATIVE_CACHE_TTL) -> None:
    """Record a source-level failure with a short TTL so the fan-out skips a
    source that just failed instead of re-hammering it (negative caching)."""
    try:
        _get_client().set(
            f"ks:sf:{source}:{category}:{_norm_key_part(query)}",
            "1", ex=ttl,
        )
    except redis.RedisError as exc:
        logger.debug("negative cache set error: %s", exc)


def source_recently_failed(source: str, query: str, category: str) -> bool:
    try:
        raw = _get_client().get(
            f"ks:sf:{source}:{category}:{_norm_key_part(query)}")
        return bool(raw)
    except redis.RedisError as exc:
        logger.debug("negative cache get error: %s", exc)
        return False
