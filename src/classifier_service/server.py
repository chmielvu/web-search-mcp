"""GLiNER2 inference service for Cloud Run."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from runtime import GLiNER2Runtime

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gliner2-inference")

runtime = GLiNER2Runtime()
_loaded_at = 0.0


class Message(BaseModel):
    role: str = Field(..., pattern="^(system|user|assistant)$")
    content: str = Field(..., min_length=1, max_length=4000)


class InferenceRequest(BaseModel):
    text: str | None = Field(default=None, max_length=4000)
    task: str | None = Field(default=None, pattern="^(classify|entities|json|relations|combined)$")
    labels: dict[str, list[str] | dict[str, str]] | None = None
    entity_types: list[str] | dict[str, str] | None = None
    structures: dict[str, list[str]] | None = None
    classification: dict[str, dict[str, Any]] | None = None
    relations: list[str] | None = None
    include_confidence: bool = False
    include_spans: bool = False
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    messages: list[Message] | None = None
    json_schema: dict[str, Any] | None = None


class InferenceResponse(BaseModel):
    result: dict[str, Any]
    latency_ms: float
    model: str


class HealthResponse(BaseModel):
    status: str
    model: str
    uptime_seconds: float


def _derive_text(req: InferenceRequest) -> str:
    if req.text and req.text.strip():
        return req.text.strip()
    if req.messages:
        joined = "\n\n".join(message.content for message in req.messages if message.content.strip())
        if joined.strip():
            return joined.strip()
    raise HTTPException(400, "text is required")


def _reject_legacy_prompt_payload(req: InferenceRequest) -> None:
    if req.json_schema is None and req.messages is None:
        return
    raise HTTPException(
        400,
        "Legacy prompt-style /generate payloads are not supported by the GLiNER2 service. "
        "Send explicit task fields instead.",
    )


def warmup_model() -> None:
    global _loaded_at
    logger.info("Warming GLiNER2 model...")
    runtime.load()
    _loaded_at = time.monotonic()
    logger.info("GLiNER2 service ready")


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(lifespan=lifespan, title="GLiNER2 Universal Inference")


@app.get("/health", response_model=HealthResponse)
async def health():
    status = runtime.health()
    if status["status"] != "ok":
        return JSONResponse(status_code=503, content=status)
    return HealthResponse(
        status=status["status"],
        model=status["model"],
        uptime_seconds=round(time.monotonic() - _loaded_at, 3),
    )


@app.get("/help")
async def help_docs():
    return {
        "service": "GLiNER2 Universal Inference Service",
        "version": "1.0.0",
        "description": "Inference-only GLiNER2 base service. The client owns fanout, query rewrite, and all orchestration.",
        "model": runtime.model_id,
        "supported_tasks": ["classify", "entities", "json", "relations", "combined"],
        "endpoints": {
            "/infer": {"method": "POST", "description": "Universal GLiNER2 task runner."},
            "/generate": {
                "method": "POST",
                "description": "Compatibility alias for /infer; legacy prompt payloads are rejected.",
            },
            "/classify": {"method": "POST", "description": "Text classification using GLiNER2."},
            "/extract": {"method": "POST", "description": "Entity extraction using GLiNER2."},
            "/extract_json": {"method": "POST", "description": "Structured extraction using GLiNER2."},
            "/extract_relations": {"method": "POST", "description": "Relation extraction using GLiNER2."},
            "/extract_combined": {"method": "POST", "description": "Multi-task schema composition using GLiNER2."},
            "/health": {"method": "GET", "description": "Health check."},
        },
    }


async def _run_blocking(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


def _infer_task(req: InferenceRequest) -> str:
    if req.task:
        return req.task
    if req.classification:
        return "combined"
    if req.entity_types is not None:
        return "entities"
    if req.structures is not None:
        return "json"
    if req.relations is not None:
        return "relations"
    if req.labels is not None:
        return "classify"
    raise HTTPException(400, "task or task-specific fields are required")


async def _infer(req: InferenceRequest) -> dict[str, Any]:
    text = _derive_text(req)
    task = _infer_task(req)
    if task == "classify":
        if req.labels is None:
            raise HTTPException(400, "labels are required for classify tasks")
        return await _run_blocking(runtime.classify_text, text, req.labels)
    if task == "entities":
        if req.entity_types is None:
            raise HTTPException(400, "entity_types are required for entities tasks")
        return await _run_blocking(
            runtime.extract_entities,
            text,
            req.entity_types,
            include_confidence=req.include_confidence,
            include_spans=req.include_spans,
        )
    if task == "json":
        if req.structures is None:
            raise HTTPException(400, "structures are required for json tasks")
        return await _run_blocking(
            runtime.extract_json,
            text,
            req.structures,
            threshold=req.threshold,
            include_confidence=req.include_confidence,
            include_spans=req.include_spans,
        )
    if task == "relations":
        if req.relations is None:
            raise HTTPException(400, "relations are required for relations tasks")
        return await _run_blocking(
            runtime.extract_relations,
            text,
            req.relations,
            include_confidence=req.include_confidence,
            include_spans=req.include_spans,
        )
    if req.classification is None and req.entity_types is None and req.structures is None:
        raise HTTPException(
            400,
            "combined tasks require classification, entity_types, or structures fields",
        )
    return await _run_blocking(
        runtime.extract_combined,
        text,
        entities=req.entity_types,
        classification=req.classification,
        structures=req.structures,
    )


@app.post("/infer", response_model=InferenceResponse)
async def infer(req: InferenceRequest):
    t0 = time.monotonic()
    try:
        result = await _infer(req)
        return InferenceResponse(
            result=result,
            latency_ms=round((time.monotonic() - t0) * 1000, 1),
            model=runtime.model_id,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Inference failed")
        raise HTTPException(500, f"Inference failed: {exc}") from exc


@app.post("/generate", response_model=InferenceResponse)
async def generate(req: InferenceRequest):
    _reject_legacy_prompt_payload(req)
    return await infer(req)


@app.post("/classify")
async def classify(payload: dict[str, Any]):
    req = InferenceRequest.model_validate({**payload, "task": "classify"})
    return await infer(req)


@app.post("/extract")
async def extract(payload: dict[str, Any]):
    req = InferenceRequest.model_validate({**payload, "task": "entities"})
    return await infer(req)


@app.post("/extract_json")
async def extract_json(payload: dict[str, Any]):
    req = InferenceRequest.model_validate({**payload, "task": "json"})
    return await infer(req)


@app.post("/extract_relations")
async def extract_relations(payload: dict[str, Any]):
    req = InferenceRequest.model_validate({**payload, "task": "relations"})
    return await infer(req)


@app.post("/extract_combined")
async def extract_combined(payload: dict[str, Any]):
    req = InferenceRequest.model_validate({**payload, "task": "combined"})
    return await infer(req)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
