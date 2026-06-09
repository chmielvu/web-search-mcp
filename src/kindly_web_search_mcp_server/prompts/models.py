"""Prompt rendering models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PromptBundle:
    task: str
    provider_name: str
    system: str
    user: str
