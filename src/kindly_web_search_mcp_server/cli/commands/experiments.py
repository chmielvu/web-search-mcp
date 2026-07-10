from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from ..errors import CliError
from ..exit_codes import ExitCode
from ..output import emit_json
from ..runtime import get_runtime
from ...ab_testing.models import ABExperiment, ABVariant
from ...ab_testing.yaml_loader import load_experiments, save_experiments
from ...settings import settings
from ...utils.paths import DEFAULT_EXPERIMENTS_YAML


experiments_app = typer.Typer(no_args_is_help=True)


def _resolve_config_path() -> Path:
    """Resolve the A/B config path from settings or default."""
    raw = settings.ab_config_path
    if raw:
        return Path(raw)
    return Path(DEFAULT_EXPERIMENTS_YAML)


def _find_experiment(experiments: list[ABExperiment], experiment_id: str) -> ABExperiment:
    for exp in experiments:
        if exp.experiment_id == experiment_id:
            return exp
    raise CliError(
        kind="not_found",
        message=f"Experiment '{experiment_id}' not found.",
        hint="Run `web-search-cli experiments list` to see available experiments.",
        exit_code=ExitCode.NOT_FOUND,
        context={"command": "experiments", "experiment_id": experiment_id},
    )


@experiments_app.command("list")
def list_cmd() -> None:
    """List all experiments from the A/B config YAML."""
    config_path = _resolve_config_path()
    try:
        experiments = load_experiments(config_path)
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=f"Failed to load experiments: {exc}",
            hint="Check that the YAML config file exists and is valid.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={"command": "experiments list", "config_path": str(config_path)},
        ) from exc

    payload = {
        "experiments": [
            {
                "experiment_id": exp.experiment_id,
                "layer": exp.layer,
                "status": exp.status,
                "traffic_pct": exp.traffic_pct,
                "variants": [v.variant_key for v in exp.variants],
                "primary_metric": exp.primary_metric,
            }
            for exp in experiments
        ]
    }
    emit_json(payload, command="experiments list")


@experiments_app.command("enable")
def enable_cmd(
    experiment_id: Annotated[str, typer.Argument(help="Experiment ID to enable.")],
) -> None:
    """Set an experiment status to 'running' and save."""
    config_path = _resolve_config_path()
    try:
        experiments = load_experiments(config_path)
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=f"Failed to load experiments: {exc}",
            hint="Check that the YAML config file exists and is valid.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={"command": "experiments enable", "config_path": str(config_path)},
        ) from exc

    exp = _find_experiment(experiments, experiment_id)

    if exp.status == "running":
        raise CliError(
            kind="conflict",
            message=f"Experiment '{experiment_id}' is already running.",
            hint="Use `web-search-cli experiments disable` to pause it first.",
            exit_code=ExitCode.CONFLICT,
            context={"command": "experiments enable", "experiment_id": experiment_id},
        )

    if exp.status == "concluded":
        raise CliError(
            kind="conflict",
            message=f"Experiment '{experiment_id}' is already concluded and cannot be re-enabled.",
            hint="Create a new experiment instead.",
            exit_code=ExitCode.CONFLICT,
            context={"command": "experiments enable", "experiment_id": experiment_id},
        )

    exp.status = "running"
    try:
        save_experiments(experiments, config_path)
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=f"Failed to save experiments: {exc}",
            hint="Check file permissions and disk space.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={"command": "experiments enable", "config_path": str(config_path)},
        ) from exc

    emit_json(
        {
            "experiment_id": experiment_id,
            "status": "running",
            "message": f"Experiment '{experiment_id}' is now running.",
        },
        command="experiments enable",
    )


@experiments_app.command("disable")
def disable_cmd(
    experiment_id: Annotated[str, typer.Argument(help="Experiment ID to disable.")],
) -> None:
    """Set an experiment status to 'paused' and save."""
    config_path = _resolve_config_path()
    try:
        experiments = load_experiments(config_path)
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=f"Failed to load experiments: {exc}",
            hint="Check that the YAML config file exists and is valid.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={"command": "experiments disable", "config_path": str(config_path)},
        ) from exc

    exp = _find_experiment(experiments, experiment_id)

    if exp.status == "paused":
        raise CliError(
            kind="conflict",
            message=f"Experiment '{experiment_id}' is already paused.",
            hint="Use `web-search-cli experiments enable` to resume it.",
            exit_code=ExitCode.CONFLICT,
            context={"command": "experiments disable", "experiment_id": experiment_id},
        )

    if exp.status == "concluded":
        raise CliError(
            kind="conflict",
            message=f"Experiment '{experiment_id}' is already concluded and cannot be paused.",
            hint="Create a new experiment instead.",
            exit_code=ExitCode.CONFLICT,
            context={"command": "experiments disable", "experiment_id": experiment_id},
        )

    exp.status = "paused"
    try:
        save_experiments(experiments, config_path)
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=f"Failed to save experiments: {exc}",
            hint="Check file permissions and disk space.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={"command": "experiments disable", "config_path": str(config_path)},
        ) from exc

    emit_json(
        {
            "experiment_id": experiment_id,
            "status": "paused",
            "message": f"Experiment '{experiment_id}' is now paused.",
        },
        command="experiments disable",
    )


@experiments_app.command("conclude")
def conclude_cmd(
    experiment_id: Annotated[str, typer.Argument(help="Experiment ID to conclude.")],
    winner: Annotated[str, typer.Option("--winner", help="Winning variant key.")],
) -> None:
    """Set an experiment status to 'concluded' with a winning variant."""
    config_path = _resolve_config_path()
    try:
        experiments = load_experiments(config_path)
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=f"Failed to load experiments: {exc}",
            hint="Check that the YAML config file exists and is valid.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={
                "command": "experiments conclude",
                "config_path": str(config_path),
            },
        ) from exc

    exp = _find_experiment(experiments, experiment_id)

    if exp.status == "concluded":
        raise CliError(
            kind="conflict",
            message=f"Experiment '{experiment_id}' is already concluded.",
            hint="Create a new experiment instead.",
            exit_code=ExitCode.CONFLICT,
            context={"command": "experiments conclude", "experiment_id": experiment_id},
        )

    # Validate winner is a valid variant
    variant_keys = [v.variant_key for v in exp.variants]
    if winner not in variant_keys:
        raise CliError(
            kind="usage_error",
            message=f"Winner '{winner}' is not a valid variant for experiment '{experiment_id}'. "
            f"Valid variants: {', '.join(variant_keys)}",
            hint="Use `web-search-cli experiments stats` to see available variants.",
            exit_code=ExitCode.USAGE_ERROR,
            context={
                "command": "experiments conclude",
                "experiment_id": experiment_id,
                "winner": winner,
                "valid_variants": variant_keys,
            },
        )

    exp.status = "concluded"
    exp.winning_variant = winner
    try:
        save_experiments(experiments, config_path)
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=f"Failed to save experiments: {exc}",
            hint="Check file permissions and disk space.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={
                "command": "experiments conclude",
                "config_path": str(config_path),
            },
        ) from exc

    emit_json(
        {
            "experiment_id": experiment_id,
            "status": "concluded",
            "winning_variant": winner,
            "message": f"Experiment '{experiment_id}' concluded with winner '{winner}'.",
        },
        command="experiments conclude",
    )


@experiments_app.command("stats")
def stats_cmd(
    experiment_id: Annotated[str, typer.Argument(help="Experiment ID to show stats for.")],
) -> None:
    """Show basic stats for an experiment."""
    config_path = _resolve_config_path()
    try:
        experiments = load_experiments(config_path)
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=f"Failed to load experiments: {exc}",
            hint="Check that the YAML config file exists and is valid.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={"command": "experiments stats", "config_path": str(config_path)},
        ) from exc

    exp = _find_experiment(experiments, experiment_id)

    payload = {
        "experiment_id": exp.experiment_id,
        "layer": exp.layer,
        "status": exp.status,
        "traffic_pct": exp.traffic_pct,
        "hypothesis": exp.hypothesis,
        "primary_metric": exp.primary_metric,
        "guardrail_metrics": exp.guardrail_metrics,
        "started_at": exp.started_at,
        "ended_at": exp.ended_at,
        "winning_variant": exp.winning_variant,
        "variants": [
            {
                "variant_key": v.variant_key,
                "weight": v.weight,
                "description": v.description,
            }
            for v in exp.variants
        ],
    }
    emit_json(payload, command="experiments stats")


@experiments_app.command("create")
def create_cmd(
    config: Annotated[
        str | None,
        typer.Option(
            "--config",
            help="JSON string with experiment config. If omitted, interactive prompts are used when non-interactive mode is disabled.",
        ),
    ] = None,
) -> None:
    """Scaffold a new experiment interactively or from a JSON config."""
    config_path = _resolve_config_path()
    runtime = get_runtime()

    if config:
        try:
            data = json.loads(config)
        except json.JSONDecodeError as exc:
            raise CliError(
                kind="usage_error",
                message=f"Invalid JSON config: {exc}",
                hint="Provide a valid JSON string with --config.",
                exit_code=ExitCode.USAGE_ERROR,
                context={"command": "experiments create"},
            ) from exc

        variants = [
            ABVariant(
                variant_key=v["variant_key"],
                weight=v["weight"],
                config=v.get("config", {}),
                description=v.get("description", ""),
            )
            for v in data.get("variants", [])
        ]
        exp = ABExperiment(
            experiment_id=data.get("experiment_id", ""),
            layer=data.get("layer", ""),
            status=data.get("status", "draft"),
            hypothesis=data.get("hypothesis", ""),
            primary_metric=data.get("primary_metric", ""),
            traffic_pct=data.get("traffic_pct", 10.0),
            guardrail_metrics=data.get("guardrail_metrics", []),
            variants=variants,
        )
    else:
        if runtime.non_interactive:
            raise CliError(
                kind="usage_error",
                message="Interactive experiment creation is disabled in non-interactive mode.",
                hint="Pass --config with a JSON payload, or rerun with --non-interactive=false.",
                exit_code=ExitCode.USAGE_ERROR,
                context={"command": "experiments create"},
            )
        # Interactive mode
        experiment_id = typer.prompt("Experiment ID")
        layer = typer.prompt("Layer (e.g. query_understanding, reranking)")
        hypothesis = typer.prompt("Hypothesis", default="")
        primary_metric = typer.prompt("Primary metric", default="")
        traffic_pct = float(typer.prompt("Traffic percentage (0-100)", default="10"))

        variants = []
        add_variants = typer.confirm("Add variants?", default=True)
        while add_variants:
            variant_key = typer.prompt("Variant key")
            weight = int(typer.prompt("Weight", default="1"))
            description = typer.prompt("Description", default="")
            variants.append(
                ABVariant(
                    variant_key=variant_key,
                    weight=weight,
                    description=description,
                )
            )
            add_variants = typer.confirm("Add another variant?", default=False)

        exp = ABExperiment(
            experiment_id=experiment_id,
            layer=layer,
            status="draft",
            hypothesis=hypothesis,
            primary_metric=primary_metric,
            traffic_pct=traffic_pct,
            variants=variants,
        )

    errors = exp.validate()
    if errors:
        raise CliError(
            kind="validation_error",
            message=f"Experiment validation failed: {'; '.join(errors)}",
            hint="Check the experiment configuration and retry.",
            exit_code=ExitCode.VALIDATION_ERROR,
            context={
                "command": "experiments create",
                "experiment_id": exp.experiment_id,
                "errors": errors,
            },
        )

    try:
        existing = load_experiments(config_path)
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=f"Failed to load existing experiments: {exc}",
            hint="Check that the YAML config file is valid.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={"command": "experiments create", "config_path": str(config_path)},
        ) from exc

    # Check for duplicate experiment_id
    for existing_exp in existing:
        if existing_exp.experiment_id == exp.experiment_id:
            raise CliError(
                kind="conflict",
                message=f"Experiment '{exp.experiment_id}' already exists.",
                hint="Use a different experiment_id or edit the existing one.",
                exit_code=ExitCode.CONFLICT,
                context={
                    "command": "experiments create",
                    "experiment_id": exp.experiment_id,
                },
            )

    existing.append(exp)
    try:
        save_experiments(existing, config_path)
    except Exception as exc:
        raise CliError(
            kind="tool_error",
            message=f"Failed to save experiments: {exc}",
            hint="Check file permissions and disk space.",
            exit_code=ExitCode.INTERNAL_ERROR,
            context={"command": "experiments create", "config_path": str(config_path)},
        ) from exc

    emit_json(
        {
            "experiment_id": exp.experiment_id,
            "status": exp.status,
            "message": f"Experiment '{exp.experiment_id}' created successfully.",
        },
        command="experiments create",
    )


def register(app: typer.Typer) -> None:
    app.add_typer(experiments_app, name="experiments")
