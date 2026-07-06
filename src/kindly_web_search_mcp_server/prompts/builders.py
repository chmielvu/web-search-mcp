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


# ---------------------------------------------------------------------------
# GPT-OSS system-message header
#
# The GPT-OSS Harmony format uses the system role for identity, meta-dates,
# and reasoning-effort control.  Provider jinja templates look for the
# ``Reasoning:`` keyword in the system message content to configure the
# reasoning level.
#
# LiteLLM also accepts ``reasoning_effort`` as a top-level kwarg which is
# forwarded to Groq / Cerebras / Vercel AI Gateway.  We set both as
# belt-and-suspenders.
#
# See: https://console.groq.com/docs/reasoning
# See: https://inference-docs.cerebras.ai/api-reference/chat-completions
# See: https://docs.litellm.ai/docs/reasoning_content
# ---------------------------------------------------------------------------

#: Canonical reasoning-effort levels for GPT-OSS tasks.
REASONING_EFFORT_LOW = "low"
REASONING_EFFORT_MEDIUM = "medium"
REASONING_EFFORT_HIGH = "high"


def system_header(reasoning: str = REASONING_EFFORT_LOW) -> str:
    """Return a GPT-OSS system-message prefix with reasoning-effort control.

    The ``Reasoning:`` directive is placed first so provider jinja templates
    can detect it reliably.
    """
    return f"Reasoning: {reasoning}\nKnowledge cutoff: 2024-06\nCurrent date: {anchor_today()}"
