from __future__ import annotations

import importlib.metadata
import tomllib
from pathlib import Path
from typing import Any

from .introspection import build_schema_payload, find_command_node
from .skill_paths import DEV_SKILL_PATH, USER_SKILL_PATH

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BRIEF_PATH = _REPO_ROOT / "agent" / "brief.md"
_PYPROJECT_PATH = _REPO_ROOT / "pyproject.toml"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def cli_version() -> str:
    try:
        return importlib.metadata.version("web-search-mcp")
    except importlib.metadata.PackageNotFoundError:
        with _PYPROJECT_PATH.open("rb") as handle:
            project = tomllib.load(handle).get("project", {})
        version = project.get("version")
        if isinstance(version, str) and version:
            return version
        return "0.0.0"


def cli_brief() -> str:
    if _BRIEF_PATH.exists():
        return _read_text(_BRIEF_PATH)
    return _first_paragraph(USER_SKILL_PATH)


def _first_paragraph(path: Path) -> str:
    lines = _read_text(path).splitlines()
    paragraphs: list[list[str]] = []
    current: list[str] = []
    in_frontmatter = False
    for line in lines:
        stripped = line.strip()
        if stripped == "---" and not paragraphs and not current:
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue
        if not stripped:
            if current:
                paragraphs.append(current)
                current = []
            continue
        if stripped.startswith("# ") and not current and not paragraphs:
            continue
        current.append(stripped)
    if current:
        paragraphs.append(current)
    for paragraph in paragraphs:
        text = " ".join(paragraph).strip()
        if text:
            return text
    return _read_text(path)


def skill_catalog() -> list[dict[str, Any]]:
    return [
        {
            "name": "web-search-cli",
            "path": str(USER_SKILL_PATH.relative_to(_REPO_ROOT)),
            "description": _frontmatter_description(USER_SKILL_PATH),
        },
        {
            "name": "web-search-cli-dev",
            "path": str(DEV_SKILL_PATH.relative_to(_REPO_ROOT)),
            "description": _frontmatter_description(DEV_SKILL_PATH),
        },
    ]


def _frontmatter_description(path: Path) -> str:
    text = _read_text(path)
    in_frontmatter = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "---":
            if in_frontmatter:
                break
            in_frontmatter = True
            continue
        if in_frontmatter and stripped.startswith("description:"):
            return stripped.split(":", 1)[1].strip().strip('"')
    return _first_paragraph(path)


def _global_option_tokens() -> set[str]:
    return {
        "--agent",
        "--human",
        "--quiet",
        "-q",
        "--profile",
        "--log-level",
        "--non-interactive",
    }


def _command_path_tokens(args: list[str]) -> list[str]:
    if not args:
        return []
    help_tokens = {"--help", "-h"}
    stop_index = len(args)
    for index, token in enumerate(args):
        if token in help_tokens:
            stop_index = index
            break
    prefix = args[:stop_index]
    path: list[str] = []
    index = 0
    root_options = _global_option_tokens()
    while index < len(prefix):
        token = prefix[index]
        if token in root_options:
            if token in {"--profile", "--log-level"} and index + 1 < len(prefix):
                index += 2
                continue
            index += 1
            continue
        if token.startswith("-"):
            break
        path.append(token)
        index += 1
        while index < len(prefix) and not prefix[index].startswith("-"):
            path.append(prefix[index])
            index += 1
        break
    return path


def build_help_payload(app: Any, args: list[str] | None = None) -> dict[str, Any]:
    schema = build_schema_payload(app)
    path_tokens = _command_path_tokens(args or [])
    node = find_command_node(schema["command_tree"], path_tokens)
    return {
        "command": node["path"],
        "version": cli_version(),
        "brief": cli_brief(),
        "help": node.get("help", ""),
        "params": node.get("params", []),
        "commands": node.get("commands", []),
        "command_tree": node,
        "skills": skill_catalog(),
    }
