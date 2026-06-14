# AGENTS.md - Observability

This directory contains observability and telemetry for the search pipeline.

## Structure

observability/
|-- __init__.py              # Observability exports
|-- metrics.py               # Prometheus/OpenTelemetry metrics
|-- tracing.py               # Distributed tracing (OpenTelemetry)
-- logging.py               # Structured logging configuration

## Purpose
- Metrics collection for search quality and performance
- Distributed tracing for request flows
- Structured logging for debugging

## Key Metrics
- Search latency (per provider, per stage)
- Result quality scores
- Cache hit rates
- Error rates by type

## Testing
pytest tests/test_observability*.py -v (if exists)
