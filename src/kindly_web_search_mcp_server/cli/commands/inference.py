"""CLI commands for inspecting and validating the inference subsystem."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from ...inference import describe_catalog, validate_catalog
from ..output import emit_json


inference_app = typer.Typer(no_args_is_help=True)


@inference_app.command("describe")
def describe_cmd() -> None:
    """Describe registered models, providers, and chains without secrets."""
    report = describe_catalog()
    emit_json(report, command="inference describe")


@inference_app.command("validate")
def validate_cmd(
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Exit with non-zero status on validation errors"),
    ] = False,
) -> None:
    """Validate that all chain references resolve to registered models and adapters."""
    report = validate_catalog()
    emit_json(report.to_dict(), command="inference validate")
    if strict and not report.ok:
        raise typer.Exit(code=1)


@inference_app.command("chain")
def chain_cmd(
    name: Annotated[
        str,
        typer.Argument(help="Chain name to inspect"),
    ],
) -> None:
    """Inspect a single chain's configuration and provider details."""
    from ...inference.chain import get_chain
    from ...inference.registry import get_model

    try:
        chain = get_chain(name)
    except KeyError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    steps: list[dict[str, Any]] = []
    details: dict[str, Any] = {
        "name": chain.name,
        "steps": steps,
    }

    for index, spec_id in enumerate(chain.model_spec_ids, start=1):
        try:
            spec = get_model(spec_id)
            steps.append(
                {
                    "position": index,
                    "spec_id": spec_id,
                    "model_id": spec.model_id,
                    "provider": spec.provider,
                    "api_key_env": spec.api_key_env,
                    "base_url": spec.base_url,
                    "default_timeout": spec.default_timeout,
                }
            )
        except Exception as exc:  # noqa: BLE001
            steps.append(
                {
                    "position": index,
                    "spec_id": spec_id,
                    "error": str(exc),
                }
            )

    emit_json(details, command=f"inference chain {name}")


def register(app: typer.Typer) -> None:
    """Register inference commands with the CLI app."""
    app.add_typer(inference_app, name="inference")
