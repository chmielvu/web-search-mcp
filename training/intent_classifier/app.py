"""FastAPI intent classification service using ONNX INT8 TinyBERT."""

from __future__ import annotations

import time
from pathlib import Path

import torch
from fastapi import FastAPI
from pydantic import BaseModel

from optimum.onnxruntime import ORTModelForSequenceClassification
from transformers import AutoTokenizer

MODEL_DIR = Path(__file__).parent / "model"

app = FastAPI(title="Intent Classifier", version="1.0.0")

_classifier = None
_tokenizer = None
_model = None
_model_info = {"model": "tinybert-4l-intent-classifier", "loaded": False}


def _load():
    global _model, _tokenizer
    if _model is None:
        _model = ORTModelForSequenceClassification.from_pretrained(
            str(MODEL_DIR), file_name="model_quantized.onnx"
        )
        _tokenizer = AutoTokenizer.from_pretrained(str(MODEL_DIR))
        _model_info["loaded"] = True


class ClassifyRequest(BaseModel):
    text: str
    labels: list[str] | None = None  # ignored — model has fixed labels


class ScoreItem(BaseModel):
    label: str
    score: float


class ClassifyResponse(BaseModel):
    intent: str
    scores: list[ScoreItem]
    latency_ms: float


@app.get("/health")
async def health():
    return {"status": "ok", **_model_info}


@app.post("/classify", response_model=ClassifyResponse)
async def classify(req: ClassifyRequest):
    start = time.perf_counter()
    _load()

    inputs = _tokenizer(req.text, return_tensors="pt", truncation=True, max_length=64)
    outputs = _model(**inputs)
    probs = torch.softmax(outputs.logits, dim=-1)[0]

    id2label = _model.config.id2label
    scores = [
        ScoreItem(label=id2label[i], score=probs[i].item())
        for i in range(len(id2label))
    ]
    scores.sort(key=lambda s: s.score, reverse=True)

    elapsed_ms = (time.perf_counter() - start) * 1000
    return ClassifyResponse(
        intent=scores[0].label,
        scores=scores,
        latency_ms=round(elapsed_ms, 2),
    )
