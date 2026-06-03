"""Qdrant-backed result memory store (Phase 7.1).

Stores individual search results keyed by (query embedding + result URL) for
future candidate injection into RRF merge. Uses local Qdrant only.

Features per plan:
- QdrantClient(":memory:") or path= from KINDLY_RESULT_MEMORY_PATH
- Collection name encodes embedding model + dim (avoids vector dim conflicts)
- Deterministic point IDs (sha256(query+url) as uuid str) -> no dups on upsert
- Payload roundtrip for title/url/snippet + query_text + entities_json + created_at + provider_key
- Age decay on lookup rescoring
- Entity-overlap boost on lookup (accepts list[dict] even if full entity pipeline not yet wired)
- Emits result_memory.lookup and result_memory.store via observability

Not in scope for 7.1/7.2: entity extraction, full server wiring, dashboards.
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from ..settings import settings
from ..utils.observability import emit_observability_event

logger = logging.getLogger(__name__)

# Global singleton for get_result_memory_store
_result_memory_store: ResultMemoryStore | None = None


def _sanitize_for_collection(model: str) -> str:
    """Make model name safe for Qdrant collection identifier."""
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in model)


def _deterministic_point_id(query_text: str, url: str) -> str:
    """Stable str ID (valid UUID) for a (query, result_url) pair. Enables upsert=no-dup.

    Qdrant local mode validates str ids as UUIDs in some code paths.
    """
    composite = f"{query_text.strip()}\n{url.strip()}"
    digest = sha256(composite.encode("utf-8")).hexdigest()
    # 32 hex chars -> valid UUID str
    return str(uuid.UUID(digest[:32]))


def _compute_entity_overlap(
    query_entities: list[dict[str, Any]] | None,
    candidate_entities: list[dict[str, Any]] | None,
) -> float:
    """Simple overlap in [0,1] for boost. Works with partial dicts {label, text}.

    Even if full GLiNER/entity package not wired yet, supports the boost logic.
    """
    if not query_entities or not candidate_entities:
        return 0.0
    q_keys = {(e.get("label"), e.get("text")) for e in query_entities if e.get("label") and e.get("text")}
    c_keys = {(e.get("label"), e.get("text")) for e in candidate_entities if e.get("label") and e.get("text")}
    if not q_keys:
        return 0.0
    inter = len(q_keys & c_keys)
    # Jaccard-like on query side
    return inter / max(1, len(q_keys))


def _parse_age_hours(created_at: str | None) -> float:
    if not created_at:
        return 0.0
    try:
        # support Z or +00:00
        ts = created_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return max(0.0, delta.total_seconds() / 3600.0)
    except Exception:
        return 0.0


class ResultMemoryStore:
    """Qdrant local result memory for historical result candidates."""

    def __init__(
        self,
        *,
        path: str | None = None,
        embedding_model: str = "default",
        dim: int = 384,
    ) -> None:
        if path and path.strip():
            self.client = QdrantClient(path=path.strip())
        else:
            self.client = QdrantClient(":memory:")
        self.embedding_model = embedding_model
        self.dim = dim
        safe_model = _sanitize_for_collection(embedding_model)
        self.collection_name = f"result_memory_{safe_model}_{dim}"
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        try:
            self.client.get_collection(self.collection_name)
        except Exception:
            # create fresh
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=self.dim, distance=Distance.COSINE),
            )

    def lookup_candidates(
        self,
        query_embedding: list[float],
        *,
        limit: int = 5,
        min_similarity: float = 0.65,
        query_entities: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Vector search + rescoring with age decay + entity overlap boost.

        Returns list of candidate dicts (url, title, snippet, similarity, entity_overlap, adjusted_score, ...).
        """
        emit_observability_event(
            logger,
            "result_memory.lookup",
            embedding_model=self.embedding_model,
            dim=self.dim,
            limit=limit,
            min_similarity=min_similarity,
            has_entities=bool(query_entities),
        )

        if not query_embedding or len(query_embedding) != self.dim:
            logger.debug("result memory lookup: embedding dim mismatch or empty")
            return []

        try:
            res = self.client.query_points(
                collection_name=self.collection_name,
                query=query_embedding,
                limit=max(1, limit * 3),  # overfetch for rescoring
                score_threshold=min_similarity,
                with_payload=True,
            )
            hits = res.points
        except Exception as exc:
            logger.debug("result memory query_points failed: %s", exc)
            emit_observability_event(
                logger, "result_memory.lookup", status="error", error=str(exc)[:200]
            )
            return []

        time.time()
        scored: list[tuple[float, int, dict[str, Any]]] = []
        for rank, hit in enumerate(hits):
            payload: dict[str, Any] = hit.payload or {}
            created_at = payload.get("created_at")
            age_hours = _parse_age_hours(created_at)

            ents: list[dict[str, Any]] = []
            ej = payload.get("entities_json")
            if ej:
                try:
                    ents = json.loads(ej) if isinstance(ej, str) else ej
                except Exception:
                    ents = []

            overlap = _compute_entity_overlap(query_entities, ents)
            # decay factor: e^(-k * age) with k chosen so 100h ~ 0.6x
            decay = math.exp(-0.005 * age_hours)
            adjusted = float(hit.score) * (1.0 + 0.2 * overlap) * decay

            cand = {
                "url": payload.get("result_url", ""),
                "title": payload.get("result_title", ""),
                "snippet": payload.get("result_snippet", ""),
                "similarity": float(hit.score),
                "entity_overlap": float(overlap),
                "adjusted_score": float(adjusted),
                "cached_at": created_at,
                "source_query": payload.get("query_text"),
                "provider_key": payload.get("provider_key"),
            }
            scored.append((adjusted, rank, cand))

        # primary: adjusted desc; tie-break: earlier rank from qdrant (smaller rank first)
        scored.sort(key=lambda t: (t[0], -t[1]), reverse=True)
        selected = [c for _, _, c in scored[:limit]]

        emit_observability_event(
            logger,
            "result_memory.lookup",
            status="completed",
            returned=len(selected),
            collection=self.collection_name,
        )
        return selected

    def store_results(
        self,
        query_text: str,
        query_embedding: list[float],
        results: list[dict[str, Any] | Any],
        *,
        entities: list[dict[str, Any]] | None = None,
    ) -> None:
        """Upsert results for the query. Uses deterministic IDs to avoid dups."""
        emit_observability_event(
            logger,
            "result_memory.store",
            query_preview=(query_text or "")[:120],
            num_input_results=len(results or []),
            embedding_model=self.embedding_model,
        )

        if not query_text or not results or not query_embedding:
            return
        if len(query_embedding) != self.dim:
            logger.debug("result memory store: dim mismatch, skipping")
            return

        points: list[PointStruct] = []
        ents_json = json.dumps(entities or [], ensure_ascii=True)
        created = datetime.now(timezone.utc).isoformat()

        for raw in results:
            if hasattr(raw, "model_dump"):
                r = raw.model_dump()
            elif isinstance(raw, dict):
                r = raw
            else:
                r = {
                    "title": getattr(raw, "title", ""),
                    "link": getattr(raw, "link", getattr(raw, "url", "")),
                    "snippet": getattr(raw, "snippet", ""),
                    "providers": getattr(raw, "providers", None),
                }
            url = (r.get("link") or r.get("url") or "").strip()
            if not url:
                continue
            title = str(r.get("title") or "")
            snippet = str(r.get("snippet") or "")
            provs = r.get("providers") or []
            if isinstance(provs, (list, tuple, set)):
                provider_key = ",".join(str(p) for p in provs if p)
            else:
                provider_key = str(provs or "")

            pid = _deterministic_point_id(query_text, url)
            payload = {
                "query_text": query_text,
                "result_url": url,
                "result_title": title,
                "result_snippet": snippet,
                "entities_json": ents_json,
                "content_type": r.get("category") or r.get("resource_type") or "general",
                "provider_key": provider_key,
                "created_at": created,
            }
            points.append(
                PointStruct(
                    id=pid,
                    vector=list(query_embedding),
                    payload=payload,
                )
            )

        if points:
            try:
                self.client.upsert(collection_name=self.collection_name, points=points)
            except Exception as exc:
                logger.debug("result memory upsert failed: %s", exc)
                emit_observability_event(
                    logger, "result_memory.store", status="error", error=str(exc)[:200]
                )
                return

        emit_observability_event(
            logger,
            "result_memory.store",
            status="completed",
            stored=len(points),
            collection=self.collection_name,
        )


def get_result_memory_store() -> ResultMemoryStore:
    """Singleton accessor using current settings for path/model/dim."""
    global _result_memory_store
    if _result_memory_store is None:
        p = (settings.result_memory_path or "").strip()
        path = p if p else None
        _result_memory_store = ResultMemoryStore(
            path=path,
            embedding_model=settings.hf_embedding_model,
            dim=settings.embedding_dim,
        )
    return _result_memory_store
