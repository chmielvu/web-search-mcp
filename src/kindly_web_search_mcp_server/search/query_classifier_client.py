from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import OrderedDict
from typing import Any

import httpx
from pybreaker import CircuitBreaker

from ..settings import settings
from .normalize import normalize_query
from .query_decomposition import (
    CLASSIFIER_JSON_SCHEMA,
    DECOMPOSITION_JSON_SCHEMA,
    build_classifier_messages,
    build_decomposition_messages,
    normalize_sub_questions,
)
from .query_rewrite_models import (
    ClassifierOutput,
    QueryDecompositionOutput,
    ProviderRouting,
    RewriteIntent,
    SubQuestion,
)

logger = logging.getLogger(__name__)

_CLASSIFICATION_CACHE: OrderedDict[tuple[str, str], ClassifierOutput] = OrderedDict()
_DECOMPOSITION_CACHE: OrderedDict[tuple[str, str, str], QueryDecompositionOutput] = (
    OrderedDict()
)
_CACHE_MAXSIZE = 256


def _hash_key(*parts: str) -> tuple[str, ...]:
    return tuple(
        hashlib.sha256(normalize_query(part).encode("utf-8")).hexdigest()
        for part in parts
    )


def _store_cache(
    cache: OrderedDict[Any, Any],
    key: Any,
    value: Any,
) -> None:
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > _CACHE_MAXSIZE:
        cache.popitem(last=False)


def _get_cache(cache: OrderedDict[Any, Any], key: Any) -> Any | None:
    value = cache.get(key)
    if value is not None:
        cache.move_to_end(key)
    return value


def _default_routing_for_intent(intent: RewriteIntent, query: str) -> ProviderRouting:
    lowered = normalize_query(query).casefold()
    if intent == "comparison":
        return ProviderRouting(keyword=True, neural=True, community=True)
    if intent == "general_research":
        return ProviderRouting(keyword=True, neural=True, community=False)
    community = any(
        token in lowered
        for token in ("github", "reddit", "hackernews", "forum", "issue", "bug")
    )
    return ProviderRouting(keyword=True, neural=True, community=community)


def _fallback_classifier_output(
    query: str, research_goal: str | None
) -> ClassifierOutput:
    lowered = normalize_query(research_goal or query).casefold()
    if any(
        token in lowered
        for token in (" vs ", " compare ", "comparison", "difference", "versus")
    ):
        intent: RewriteIntent = "comparison"
        should_decompose = True
    elif any(
        token in lowered
        for token in ("docs", "api", "error", "bug", "install", "how to", "debug")
    ):
        intent = "code"
        should_decompose = False
    else:
        intent = "general_research"
        should_decompose = " and " in lowered or ";" in lowered
    return ClassifierOutput(
        intent=intent,
        should_decompose=should_decompose,
        confidence=0.55 if should_decompose else 0.35,
        routing=_default_routing_for_intent(intent, query),
    )


def _fallback_decomposition_output(
    query: str,
    classifier: ClassifierOutput,
    *,
    max_subquestions: int,
) -> QueryDecompositionOutput:
    normalized = normalize_query(query)
    candidate_segments = [
        segment.strip(" ,.;:()[]{}")
        for segment in normalized.replace(" vs ", " and ")
        .replace(" versus ", " and ")
        .split(" and ")
        if segment.strip()
    ]
    if len(candidate_segments) < 2:
        return QueryDecompositionOutput(should_decompose=False, sub_questions=[])

    sub_questions: list[SubQuestion] = []
    for segment in candidate_segments[:max_subquestions]:
        lowered = segment.casefold()
        if any(
            token in lowered for token in ("reddit", "hackernews", "forum", "opinion")
        ):
            target = "community"
        elif any(
            token in lowered
            for token in ("docs", "api", "error", "bug", "install", "debug")
        ):
            target = "keyword"
        elif classifier.routing.neural:
            target = "neural"
        else:
            target = "keyword"
        sub_questions.append(
            SubQuestion(
                question=segment,
                target=target,
                why="Heuristic decomposition fallback.",
                weight=1.0,
            )
        )

    return normalize_sub_questions(
        QueryDecompositionOutput(should_decompose=True, sub_questions=sub_questions),
        max_subquestions=max_subquestions,
    )


class FunctionGemmaClient:
    def __init__(
        self,
        *,
        base_url: str,
        enabled: bool,
        timeout_seconds: float,
        max_tokens: int,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self._breaker = CircuitBreaker(
            fail_max=3,
            reset_timeout=30,
            name="functiongemma-classifier",
        )

    def _generate_sync(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        payload = {
            "messages": messages,
            "json_schema": json_schema,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(f"{self.base_url}/generate", json=payload)
            response.raise_for_status()
            body = response.json()

        if not isinstance(body, dict):
            raise ValueError("FunctionGemma response must be a JSON object")
        result = body.get("result")
        if not isinstance(result, dict):
            raise ValueError("FunctionGemma response missing result object")
        return result

    async def classify_query(
        self,
        query: str,
        *,
        research_goal: str | None = None,
        must_keep_terms: list[str] | None = None,
    ) -> ClassifierOutput:
        normalized_query = normalize_query(query)
        goal = normalize_query(research_goal or query)
        cache_key = _hash_key(normalized_query, goal)
        cached = _get_cache(_CLASSIFICATION_CACHE, cache_key)
        if cached is not None:
            return cached

        if not self.enabled or not self.base_url:
            result = _fallback_classifier_output(query, research_goal)
            _store_cache(_CLASSIFICATION_CACHE, cache_key, result)
            return result

        messages = build_classifier_messages(
            query=normalized_query,
            research_goal=research_goal,
            must_keep_terms=must_keep_terms or [],
        )

        try:
            raw_result = await asyncio.to_thread(
                self._breaker.call,
                self._generate_sync,
                messages=messages,
                json_schema=CLASSIFIER_JSON_SCHEMA,
                temperature=0.1,
                max_tokens=self.max_tokens,
            )
            result = ClassifierOutput.model_validate(raw_result)
        except Exception as exc:
            logger.warning("FunctionGemma classification failed: %s", exc)
            result = _fallback_classifier_output(query, research_goal)

        _store_cache(_CLASSIFICATION_CACHE, cache_key, result)
        return result

    async def decompose_query(
        self,
        query: str,
        *,
        research_goal: str | None = None,
        classifier: ClassifierOutput | None = None,
        must_keep_terms: list[str] | None = None,
        max_subquestions: int | None = None,
    ) -> QueryDecompositionOutput:
        normalized_query = normalize_query(query)
        goal = normalize_query(research_goal or query)
        classifier_key = classifier.intent if classifier else "unknown"
        cache_key = _hash_key(normalized_query, goal, classifier_key)
        cached = _get_cache(_DECOMPOSITION_CACHE, cache_key)
        if cached is not None:
            return cached

        if not self.enabled or not self.base_url:
            result = _fallback_decomposition_output(
                query,
                classifier or _fallback_classifier_output(query, research_goal),
                max_subquestions=max_subquestions
                or settings.query_decomposition_max_subquestions,
            )
            _store_cache(_DECOMPOSITION_CACHE, cache_key, result)
            return result

        if classifier is None:
            classifier = await self.classify_query(
                query,
                research_goal=research_goal,
                must_keep_terms=must_keep_terms,
            )

        messages = build_decomposition_messages(
            query=normalized_query,
            research_goal=research_goal,
            must_keep_terms=must_keep_terms or [],
            intent=classifier.intent,
            routing={
                "keyword": classifier.routing.keyword,
                "neural": classifier.routing.neural,
                "community": classifier.routing.community,
            },
        )

        try:
            raw_result = await asyncio.to_thread(
                self._breaker.call,
                self._generate_sync,
                messages=messages,
                json_schema=DECOMPOSITION_JSON_SCHEMA,
                temperature=0.1,
                max_tokens=self.max_tokens,
            )
            result = QueryDecompositionOutput.model_validate(raw_result)
            result = normalize_sub_questions(
                result,
                max_subquestions=max_subquestions
                or settings.query_decomposition_max_subquestions,
            )
        except Exception as exc:
            logger.warning("FunctionGemma decomposition failed: %s", exc)
            result = _fallback_decomposition_output(
                query,
                classifier,
                max_subquestions=max_subquestions
                or settings.query_decomposition_max_subquestions,
            )

        _store_cache(_DECOMPOSITION_CACHE, cache_key, result)
        return result


_CLIENT_CACHE: dict[tuple[str, bool, float, int], FunctionGemmaClient] = {}


def get_functiongemma_client() -> FunctionGemmaClient:
    cache_key = (
        settings.query_classifier_url,
        settings.query_classifier_enabled,
        settings.query_classifier_timeout_seconds,
        settings.query_classifier_max_tokens,
    )
    client = _CLIENT_CACHE.get(cache_key)
    if client is None:
        client = FunctionGemmaClient(
            base_url=settings.query_classifier_url,
            enabled=settings.query_classifier_enabled,
            timeout_seconds=settings.query_classifier_timeout_seconds,
            max_tokens=settings.query_classifier_max_tokens,
        )
        _CLIENT_CACHE[cache_key] = client
    return client
