"""Lightweight text repair for agent queries and LLM-bound content."""

from __future__ import annotations

import re
from typing import Literal

# Fancy punctuation → ASCII (agent/query ingress)
_FANCY_QUOTES = str.maketrans(
    {
        "\u2018": "'",  # ‘
        "\u2019": "'",  # ’
        "\u201a": "'",  # ‚
        "\u201b": "'",  # ‛
        "\u201c": '"',  # “
        "\u201d": '"',  # ”
        "\u201e": '"',  # „
        "\u201f": '"',  # ‟
        "\u2032": "'",  # ′
        "\u2033": '"',  # ″
        "\u00ab": '"',  # «
        "\u00bb": '"',  # »
        "\u2013": "-",  # –
        "\u2014": "-",  # —
        "\u2212": "-",  # −
        "\u2026": "...",  # …
    }
)

_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
_MULTI_WS = re.compile(r"[ \t]+")
_MULTI_BLANK_LINES = re.compile(r"\n{3,}")


def repair_unicode(text: str) -> str:
    """Fix mojibake when ftfy is installed; otherwise return text unchanged."""
    if not text:
        return text
    try:
        import ftfy

        return ftfy.fix_text(text)
    except Exception:
        return text


def clean_query(text: str) -> str:
    """Agent/query ingress: unicode repair, strip, collapse ws, ASCII punctuation."""
    if not text:
        return ""
    cleaned = repair_unicode(text)
    cleaned = cleaned.translate(_FANCY_QUOTES)
    cleaned = _ZERO_WIDTH.sub("", cleaned)
    return " ".join(cleaned.strip().split())


def clean_text_for_llm(
    text: str,
    *,
    role: Literal["snippet", "page", "transcript"] = "page",
) -> str:
    """Post-fetch / pre-LLM light cleanup. Does not re-extract HTML."""
    if not text:
        return ""
    cleaned = repair_unicode(text)
    cleaned = _ZERO_WIDTH.sub("", cleaned)
    if role == "transcript":
        cleaned = _MULTI_BLANK_LINES.sub("\n\n", cleaned)
        cleaned = _MULTI_WS.sub(" ", cleaned)
        return cleaned.strip()
    # snippet + page: unicode + whitespace collapse (snippet chrome strips stay upstream)
    cleaned = _MULTI_WS.sub(" ", cleaned)
    cleaned = _MULTI_BLANK_LINES.sub("\n\n", cleaned)
    return cleaned.strip()
