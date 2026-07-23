from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
USER_SKILL_PATH = REPO_ROOT / "skills" / "web-search-cli" / "SKILL.md"
DEV_SKILL_PATH = REPO_ROOT / "skills" / "web-search-cli-dev" / "SKILL.md"
AGENT_DIR = REPO_ROOT / "agent"
AGENT_BRIEF_PATH = AGENT_DIR / "brief.md"
AGENT_RULES_DIR = AGENT_DIR / "rules"
AGENT_SKILLS_DIR = AGENT_DIR / "skills"


def skill_path(*, dev: bool) -> Path:
    return DEV_SKILL_PATH if dev else USER_SKILL_PATH
