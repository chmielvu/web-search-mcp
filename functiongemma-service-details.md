# FunctionGemma Service Details

The `FunctionGemma-270M-it` classifier has been successfully containerized, built, and deployed to Google Cloud Run as a stateless, universal structured generation endpoint.

## Infrastructure
- **Model:** `unsloth/functiongemma-270m-it-GGUF` (8-bit quantized, ~280MB)
- **Engine:** `llama-cpp-python` (CPU compiled, native grammar enforcement)
- **Framework:** FastAPI / Uvicorn
- **Cloud Run Spec:** 2 vCPU, 1GiB Memory (Free-tier friendly)
- **URL:** `https://functiongemma-classifier-373347358125.us-central1.run.app`

## Endpoints

### 1. `GET /health`
Returns service readiness and uptime.
```json
{
  "status": "ok",
  "model": "model.gguf",
  "uptime_seconds": 12.3
}
```

### 2. `GET /help`
Returns the universal API contract and supported parameters.

### 3. `POST /generate`
The core generation endpoint. 

**Request Format:**
```json
{
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."}
  ],
  "json_schema": {
    "type": "object",
    "properties": { "intent": {"type": "string"} },
    "required": ["intent"]
  },
  "temperature": 0.1,
  "max_tokens": 500
}
```

**Response Format:**
```json
{
  "result": {
    "intent": "feature_request"
  },
  "latency_ms": 3342.9,
  "tokens_generated": 26
}
```

## Performance Metrics (CPU Only)
- **Cold Start (Model Load into RAM):** ~5.1 seconds
- **Warmup Time:** < 0.2 seconds
- **Inference Speed:** ~8 tokens/sec on 2 vCPUs
