"""Shared prompt-building helpers."""

from __future__ import annotations

from datetime import date


def anchor_today() -> str:
    return date.today().isoformat()


def provider_style(provider_name: str) -> str:
    return provider_name.strip().casefold() or "worker"


def join_terms(terms: list[str]) -> str:
    if not terms:
        return "- none"
    return "\n".join(f"- {term}" for term in terms)
