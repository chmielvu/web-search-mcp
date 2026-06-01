"""Universal structured generation service using FunctionGemma 270M and llama-cpp-python."""

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Determine if running locally or in container
MODEL_PATH = os.environ.get("MODEL_PATH", "/model.gguf")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("functiongemma-classifier")


class Message(BaseModel):
    role: str = Field(..., pattern="^(system|user|assistant)$")
    content: str = Field(..., min_length=1, max_length=4000)


class InferenceRequest(BaseModel):
    messages: list[Message] = Field(..., min_length=1, max_length=10)
    json_schema: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        }
    )
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=200, ge=1, le=1000)
    seed: int | None = None


class InferenceResponse(BaseModel):
    result: dict[str, Any]
    latency_ms: float
    tokens_generated: int


class HealthResponse(BaseModel):
    status: str
    model: str
    uptime_seconds: float


# Global model state
_llm: Any = None
_start_time: float = 0.0
_warmed_up: bool = False


def load_model() -> None:
    global _llm
    logger.info("Loading model from %s", MODEL_PATH)
    t0 = time.monotonic()
    try:
        from llama_cpp import Llama

        # Load model using CPU (n_gpu_layers=0)
        _llm = Llama(
            model_path=MODEL_PATH,
            n_gpu_layers=0,
            n_ctx=2048,
            verbose=False,
        )
        logger.info("Loaded in %.1fs", time.monotonic() - t0)
    except Exception as exc:
        logger.error("Failed to load model: %s", exc)
        raise


def warmup_model() -> None:
    global _warmed_up, _start_time
    logger.info("Warmup inference...")
    try:
        if _llm:
            _llm.create_chat_completion(
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=2,
            )
    except Exception as e:
        logger.warning("Warmup issue: %s", e)
    _warmed_up, _start_time = True, time.monotonic()
    logger.info("Ready")


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_model()
    warmup_model()
    yield


app = FastAPI(lifespan=lifespan, title="Universal Structured Generation")


@app.get("/health", response_model=HealthResponse)
async def health():
    if not _warmed_up:
        return JSONResponse(503, {"status": "warming_up"})
    return HealthResponse(
        status="ok",
        model=os.path.basename(MODEL_PATH),
        uptime_seconds=time.monotonic() - _start_time,
    )


@app.get("/help")
async def help_docs():
    return {
        "service": "Universal Structured Generation Service",
        "version": "1.0.0",
        "description": "Accepts prompts + JSON schemas and returns guaranteed-valid structured JSON via constrained decoding. Stateless.",
        "model": "FunctionGemma-270M-it (GGUF, 8-bit quantized)",
        "endpoints": {
            "/generate": {
                "method": "POST",
                "description": "Generate structured JSON using constrained decoding.",
                "request_body": {
                    "messages": "Array of {role, content}",
                    "json_schema": "JSON Schema definition",
                    "temperature": "0.0-2.0",
                    "max_tokens": "1-1000",
                    "seed": "Optional integer",
                },
                "response": {
                    "result": "Valid JSON matching the schema",
                    "latency_ms": "Time taken",
                    "tokens_generated": "Number of tokens",
                },
            },
            "/health": {"method": "GET", "description": "Health check"},
            "/help": {"method": "GET", "description": "This documentation"},
        },
    }


@app.post("/generate", response_model=InferenceResponse)
async def generate(req: InferenceRequest):
    if not _warmed_up or not _llm:
        raise HTTPException(503, "Model still warming up or failed to load")

    t0 = time.monotonic()

    try:
        messages = [{"role": m.role, "content": m.content} for m in req.messages]

        response = _llm.create_chat_completion(
            messages=messages,
            response_format={"type": "json_object", "schema": req.json_schema},
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            seed=req.seed,
        )

        raw_content = response["choices"][0]["message"]["content"]
        tokens_generated = response["usage"]["completion_tokens"]

        result_dict = json.loads(raw_content)

        return InferenceResponse(
            result=result_dict,
            latency_ms=round((time.monotonic() - t0) * 1000, 1),
            tokens_generated=tokens_generated,
        )
    except Exception as exc:
        logger.error("Generation failed: %s", exc)
        raise HTTPException(500, f"Generation failed: {str(exc)}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
