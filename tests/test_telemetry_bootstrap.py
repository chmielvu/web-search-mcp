from __future__ import annotations

def test_init_telemetry_gracefully_skips_without_runtime_packages(monkeypatch) -> None:
    from kindly_web_search_mcp_server import telemetry

    monkeypatch.setattr(telemetry, "_initialized", False)
    monkeypatch.setattr(telemetry, "_OTEL_SDK_AVAILABLE", False)
    monkeypatch.setattr(telemetry, "LOGS_AVAILABLE", False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://example.invalid/otlp")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "Authorization=Basic test")

    telemetry.init_telemetry(service_name="web-search-mcp-test")

    assert telemetry._initialized is False
