# AGENTS.md - Telemetry

OpenTelemetry instrumentation with Phoenix-first LLM-only span filtering.

## Key Files

| File | Role |
|---|---|
| `init.py` | Phoenix OTel lifecycle (init, shutdown) |
| `attributes.py` | Semantic convention constants |
| `constants.py` | Platform feature flags |
| `metrics.py` | Meter/counter registrations |
| `spans.py` | Span creation helpers |
| `span_enhancements.py` | Span enrichment helpers |
| `records_ai.py` | AI/YouTube tool metrics recorders |
| `records_circuit.py` | Circuit breaker metrics recorders |
| `records_content.py` | Content resolution metrics recorders |
| `records_core.py` | Core search operation recorders |
| `records_rerank.py` | Rerank stage metrics recorders |
| `_internal.py` | OTLP resolution, headers |

## Rules

- `init_telemetry` wraps Phoenix OTel registration with `_redirect_stdout_to_stderr()`
  to keep JSON CLI output clean on stdout.
- Only LLM and RERANKER spans reach Phoenix via `_OpenInferenceFilteringSpanExporter`.
  Generic CHAIN, RETRIEVER, TOOL, and raw HTTP spans are dropped.
- Telemetry enabled only when `OTEL_ENABLED=true`.
- Shutdown drains the batch span processor with configurable timeout.

## Testing

```bash
uv run pytest tests/cli/test_runtime.py
```