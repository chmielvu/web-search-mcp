from __future__ import annotations

from typing import Annotated

import typer


server_app = typer.Typer(no_args_is_help=True)


@server_app.command("start")
def start_cmd(
    http: Annotated[bool, typer.Option("--http/--no-http")] = False,
    sse: Annotated[bool, typer.Option("--sse/--no-sse")] = False,
    stdio: Annotated[bool, typer.Option("--stdio/--no-stdio")] = True,
    host: Annotated[str | None, typer.Option("--host")] = None,
    port: Annotated[int | None, typer.Option("--port")] = None,
) -> None:
    from ...server import main as server_main  # lazy: init_telemetry ~70s

    args: list[str] = []
    if http:
        args.append("--http")
    elif sse:
        args.append("--sse")
    elif stdio:
        args.append("--stdio")
    if host is not None:
        args.extend(["--host", host])
    if port is not None:
        args.extend(["--port", str(port)])
    server_main(args)


def register(app: typer.Typer) -> None:
    app.add_typer(server_app, name="server")

