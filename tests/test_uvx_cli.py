from __future__ import annotations

import os


def test_start_mcp_server_injects_stdio_and_context(monkeypatch) -> None:
    from kindly_web_search_mcp_server import cli
    import kindly_web_search_mcp_server.server as server

    captured: dict[str, object] = {}

    def fake_server_main(argv: list[str] | None = None) -> None:
        captured["argv"] = argv
        captured["context"] = os.environ.get("KINDLY_MCP_CONTEXT")

    monkeypatch.setattr(server, "main", fake_server_main)

    assert os.environ.get("KINDLY_MCP_CONTEXT") is None
    cli.main(["start-mcp-server", "--context", "codex"])

    assert captured["argv"] == ["--stdio"]
    assert captured["context"] == "codex"
    assert os.environ.get("KINDLY_MCP_CONTEXT") is None


def test_start_mcp_server_forwards_server_args(monkeypatch) -> None:
    from kindly_web_search_mcp_server import cli
    import kindly_web_search_mcp_server.server as server

    captured: dict[str, object] = {}

    def fake_server_main(argv: list[str] | None = None) -> None:
        captured["argv"] = argv
        captured["context"] = os.environ.get("KINDLY_MCP_CONTEXT")

    monkeypatch.setattr(server, "main", fake_server_main)

    cli.main(
        [
            "start-mcp-server",
            "--context",
            "codex",
            "--http",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ]
    )

    assert captured["argv"] == ["--http", "--host", "127.0.0.1", "--port", "8000"]
    assert captured["context"] == "codex"


def test_start_mcp_server_drops_double_dash_separator(monkeypatch) -> None:
    from kindly_web_search_mcp_server import cli
    import kindly_web_search_mcp_server.server as server

    captured: dict[str, object] = {}

    def fake_server_main(argv: list[str] | None = None) -> None:
        captured["argv"] = argv

    monkeypatch.setattr(server, "main", fake_server_main)

    cli.main(["start-mcp-server", "--context", "codex", "--", "--http"])
    assert captured["argv"] == ["--http"]


def test_start_mcp_server_restores_existing_context(monkeypatch) -> None:
    from kindly_web_search_mcp_server import cli
    import kindly_web_search_mcp_server.server as server

    captured: dict[str, object] = {}

    def fake_server_main(argv: list[str] | None = None) -> None:
        captured["argv"] = argv
        captured["context"] = os.environ.get("KINDLY_MCP_CONTEXT")

    monkeypatch.setattr(server, "main", fake_server_main)
    monkeypatch.setenv("KINDLY_MCP_CONTEXT", "existing")

    cli.main(["start-mcp-server", "--context", "codex"])

    assert captured["argv"] == ["--stdio"]
    assert captured["context"] == "codex"
    assert os.environ.get("KINDLY_MCP_CONTEXT") == "existing"
