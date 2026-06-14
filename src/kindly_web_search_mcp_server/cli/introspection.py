from __future__ import annotations

from typing import Any

import click
from typer.main import get_command


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def _param_envvar(param: click.Parameter) -> str | list[str] | None:
    envvar = getattr(param, "envvar", None)
    if envvar:
        return _jsonable(envvar)
    envvars = getattr(param, "envvars", None)
    if envvars:
        return _jsonable(envvars)
    return None


def _param_type_name(param: click.Parameter) -> str:
    if getattr(param, "is_flag", False):
        return "bool"
    param_type = getattr(param, "type", None)
    if isinstance(param_type, click.Choice):
        return "choice"
    name = getattr(param_type, "name", None)
    if name:
        return str(name)
    return type(param_type).__name__.lower() if param_type is not None else "unknown"


def _param_choices(param: click.Parameter) -> list[str] | None:
    param_type = getattr(param, "type", None)
    if isinstance(param_type, click.Choice):
        return [str(choice) for choice in param_type.choices]
    return None


def _describe_param(param: click.Parameter) -> dict[str, Any]:
    param_kind = "option" if isinstance(param, click.Option) else "argument"
    description = {
        "name": param.name,
        "kind": param_kind,
        "type": _param_type_name(param),
        "required": bool(getattr(param, "required", False)),
        "default": _jsonable(getattr(param, "default", None)),
        "help": getattr(param, "help", "") or "",
        "envvar": _param_envvar(param),
    }
    choices = _param_choices(param)
    if choices:
        description["choices"] = choices
    if getattr(param, "multiple", False):
        description["multiple"] = True
    if getattr(param, "is_flag", False):
        description["is_flag"] = True
    return description


def _walk(
    command: click.Command,
    *,
    path: str,
    root_name: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    node: dict[str, Any] = {
        "name": command.name or "",
        "path": path,
        "help": command.help or "",
        "kind": "group" if isinstance(command, click.Group) else "command",
        "params": [_describe_param(param) for param in getattr(command, "params", [])],
    }
    commands: list[dict[str, Any]] = []
    children = getattr(command, "commands", None)
    if children:
        child_nodes: list[dict[str, Any]] = []
        for child_name, child in children.items():
            child_path = child_name if path == root_name else f"{path} {child_name}"
            child_node, child_commands = _walk(child, path=child_path, root_name=root_name)
            child_nodes.append(child_node)
            commands.extend(child_commands)
        node["commands"] = child_nodes
    else:
        commands.append(node)
    return node, commands


def build_schema_payload(app: Any) -> dict[str, Any]:
    click_app = get_command(app)
    root_name = click_app.name or "web-search-cli"
    tree, commands = _walk(click_app, path=root_name, root_name=root_name)
    return {
        "command": root_name,
        "help": click_app.help or "",
        "command_tree": tree,
        "commands": commands,
    }


def find_command_node(
    command_tree: dict[str, Any],
    path_tokens: list[str],
) -> dict[str, Any]:
    node = command_tree
    if not path_tokens:
        return node
    children = {child["name"]: child for child in node.get("commands", [])}
    for token in path_tokens:
        child = children.get(token)
        if child is None:
            return node
        node = child
        children = {grandchild["name"]: grandchild for grandchild in node.get("commands", [])}
    return node
