# AGENTS.md - Telemetry

OpenTelemetry instrumentation with Phoenix-first LLM-only span filtering.

## Current Structure

telemetry/
|-- __init__.py              # Public re-exports (star-export pattern)
|-- init.py                  # Phoenix OTel lifecycle (init, shutdown)
|-- attributes.py            # Semantic convention constants
|-- constants.py             # Platform feature flags
|-- metrics.py               # Meter/counter registrations
|-- spans.py                 # Span creation helpers (create_*_span)
|-- span_enhancements.py     # Span enrichment (add_*_to_span, set_span_*)
|-- records_ai.py            # AI/YouTube tool metrics recorders
|-- records_circuit.py       # Circuit breaker metrics recorders
|-- records_content.py       # Content resolution metrics recorders
|-- records_core.py          # Core search operation recorders
|-- records_rerank.py        # Rerank stage metrics recorders
|-- _internal.py             # Internal helpers (OTLP resolution, headers)

## Key Behaviors

- `init_telemetry` wraps Phoenix OTel registration with `_redirect_stdout_to_stderr()`
  to keep JSON CLI output clean on stdout while Phoenix initialization diagnostics
  go to stderr.
- Only LLM and RERANKER spans reach Phoenix via `_OpenInferenceFilteringSpanExporter`.
  Generic CHAIN, RETRIEVER, TOOL, and raw HTTP instrumentation spans are dropped.
- Telemetry is enabled only when `OTEL_ENABLED=true` (default from settings).
- Shutdown drains the batch span processor with a configurable timeout.

## Testing

- `python -m pytest tests/cli/test_runtime.py` — coverage for telemetry init/shutdown
  in the CLI lifecycle, including Phoenix startup and graceful shutdown with
  DuckDB executor drain.
