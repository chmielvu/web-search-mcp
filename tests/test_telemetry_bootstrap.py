from __future__ import annotations

import threading


def test_init_telemetry_gracefully_skips_without_runtime_packages(monkeypatch) -> None:
    from kindly_web_search_mcp_server import telemetry

    monkeypatch.setattr(telemetry, "_initialized", False)
    monkeypatch.setattr(telemetry, "_OTEL_SDK_AVAILABLE", False)
    monkeypatch.setattr(telemetry, "LOGS_AVAILABLE", False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://example.invalid/otlp")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "Authorization=Basic test")

    telemetry.init_telemetry(service_name="web-search-mcp-test")

    assert telemetry._initialized is False


def test_init_telemetry_respects_otel_enabled_flag(monkeypatch) -> None:
    from kindly_web_search_mcp_server import telemetry

    monkeypatch.setattr(telemetry, "_initialized", False)
    monkeypatch.setenv("OTEL_ENABLED", "false")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://example.invalid/otlp")

    telemetry.init_telemetry(service_name="web-search-mcp-test")

    assert telemetry._initialized is False


def test_init_telemetry_background_returns_daemon_thread(monkeypatch) -> None:
    from kindly_web_search_mcp_server import telemetry

    monkeypatch.setattr(telemetry, "_initialized", False)
    monkeypatch.setenv("OTEL_ENABLED", "false")

    thread = telemetry.init_telemetry_background(service_name="test-bg")
    assert isinstance(thread, threading.Thread)
    assert thread.daemon is True
    assert thread.name == "otel-init"
    thread.join(timeout=2)


def test_otlp_endpoint_resolution_and_header_parsing() -> None:
    from kindly_web_search_mcp_server import telemetry

    assert telemetry._resolve_otlp_signal_endpoint(
        "traces", base_endpoint="https://example.com/otlp"
    ) == "https://example.com/otlp/v1/traces"
    assert telemetry._parse_otlp_headers(
        "Authorization=Basic%20abc, x-trace-id: 123"
    ) == {"Authorization": "Basic abc", "x-trace-id": "123"}


def test_otlp_endpoint_probe_rejects_html(monkeypatch, caplog) -> None:
    from kindly_web_search_mcp_server import telemetry

    class _FakeResponse:
        status_code = 200
        headers = {"content-type": "text/html"}
        text = "<html><body>ok</body></html>"

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def get(self, endpoint, headers):
            return _FakeResponse()

    monkeypatch.setattr(telemetry.httpx, "Client", _FakeClient)
    with caplog.at_level("WARNING"):
        assert (
            telemetry._probe_otlp_endpoint(
                "https://example.invalid/otlp/v1/traces", {}, signal="traces"
            )
            is False
        )
    assert "looks like HTML" in caplog.text


def test_logging_exporter_proxy_surfaces_failures(caplog) -> None:
    from kindly_web_search_mcp_server import telemetry

    class _BoomExporter:
        def export(self, *args, **kwargs):
            raise RuntimeError("boom")

        def shutdown(self):
            return None

        def force_flush(self, *args, **kwargs):
            return True

    proxy = telemetry._LoggingExporterProxy(_BoomExporter(), signal_name="traces")

    with caplog.at_level("WARNING"):
        try:
            proxy.export("payload")
        except RuntimeError:
            pass

    assert "traces OTLP export failed" in caplog.text
