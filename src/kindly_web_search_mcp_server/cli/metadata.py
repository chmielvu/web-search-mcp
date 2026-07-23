from __future__ import annotations

import functools
import importlib.metadata
import tomllib
from pathlib import Path
from typing import Any
from .introspection import build_schema_payload, find_command_node
from .skill_paths import (
    AGENT_BRIEF_PATH,
    AGENT_RULES_DIR,
    AGENT_SKILLS_DIR,
    DEV_SKILL_PATH,
    USER_SKILL_PATH,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
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
    if AGENT_BRIEF_PATH.exists():
        return _read_text(AGENT_BRIEF_PATH)
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


@functools.lru_cache(maxsize=None)
def rules_catalog() -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    if AGENT_RULES_DIR.exists():
        for path in sorted(AGENT_RULES_DIR.glob("*.md")):
            name = path.stem
            desc = _frontmatter_description(path)
            rules.append(
                {
                    "name": name,
                    "description": desc,
                    "file": str(path.relative_to(_REPO_ROOT)),
                }
            )
    return rules


@functools.lru_cache(maxsize=None)
def rules_full() -> list[dict[str, Any]]:
    """Return full .md content for every agent rule, per v0.2.0 R1."""
    rules: list[dict[str, Any]] = []
    if AGENT_RULES_DIR.exists():
        for path in sorted(AGENT_RULES_DIR.glob("*.md")):
            name = path.stem
            raw = path.read_text(encoding="utf-8")
            desc = _frontmatter_description(path)
            rules.append({"name": name, "description": desc, "content": raw})
    return rules


@functools.lru_cache(maxsize=None)
def skill_catalog() -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = [
        {
            "name": "web-search-cli",
            "path": str(USER_SKILL_PATH.relative_to(_REPO_ROOT)),
            "description": _frontmatter_description(USER_SKILL_PATH),
            "command": "web-search-cli getskill",
        },
        {
            "name": "web-search-cli-dev",
            "path": str(DEV_SKILL_PATH.relative_to(_REPO_ROOT)),
            "description": _frontmatter_description(DEV_SKILL_PATH),
            "command": "web-search-cli getskill --dev",
        },
    ]
    if AGENT_SKILLS_DIR.exists():
        for path in sorted(AGENT_SKILLS_DIR.glob("*.md")):
            name = path.stem
            desc = _frontmatter_description(path)
            skills.append(
                {
                    "name": name,
                    "path": str(path.relative_to(_REPO_ROOT)),
                    "description": desc,
                    "command": f"web-search-cli skills {name}",
                }
            )
    return skills


def feedback_guidance() -> str:
    return (
        "Any problem, bad output, or confusion — run: "
        "web-search-cli feedback create --type <bug|requirement|suggestion|bad-output> --message '...'"
    )


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
        "--quiet",
        "-q",
        "--profile",
        "--log-level",
        "--raw",
        "--fields",
        "--yes",
        "-y",
        "--dry-run",
        "--non-interactive",
    }


def command_path_tokens(args: list[str]) -> list[str]:
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
            if token in {
                "--profile",
                "--log-level",
                "--log-format",
                "--fields",
            } and index + 1 < len(prefix):
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
    path_tokens = command_path_tokens(args or [])
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


def build_full_help_payload(app: Any, args: list[str] | None = None) -> dict[str, Any]:
    payload = build_help_payload(app, args)
    payload["rules"] = rules_catalog()
    payload["skills"] = skill_catalog()
    payload["feedback"] = feedback_guidance()
    return payload
