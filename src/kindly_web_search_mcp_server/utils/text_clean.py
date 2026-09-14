"""Text cleaning for agent queries and LLM-bound light cleanup.

- Query ingress: ``repair_unicode`` / ``clean_query`` — ftfy repair, fancy
  punctuation folding, zero-width stripping, whitespace collapse.
- LLM-bound light cleanup: ``clean_text_for_llm`` — same repairs plus
  whitespace collapse (no HTML re-extraction).
"""

from __future__ import annotations

import re

try:  # imported once at module load; per-call re-import cost 0.18ms/call
    import ftfy as _ftfy
except ImportError:  # pragma: no cover - ftfy is a hard dep in practice
    _ftfy = None

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
        "\u2010": "-",  # ‑ (hyphen)
        "\u2011": "-",  # ‑ non-breaking hyphen
        "\u2012": "-",  # ‒ figure dash
        "\u2015": "-",  # ― horizontal bar
        "\u00ad": "",  # soft hyphen (drop)
        "\u2026": "...",  # …
    }
)

ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
_MULTI_WS = re.compile(r"[ \t]+")
_MULTI_BLANK_LINES = re.compile(r"\n{3,}")


def repair_unicode(text: str) -> str:
    """Fix mojibake when ftfy is installed; otherwise return text unchanged."""
    if not text or _ftfy is None:
        return text
    return _ftfy.fix_text(text)


def clean_query(text: str) -> str:
    """Agent/query ingress: unicode repair, strip, collapse ws, ASCII punctuation."""
    if not text:
        return ""
    cleaned = repair_unicode(text)
    cleaned = cleaned.translate(_FANCY_QUOTES)
    cleaned = ZERO_WIDTH.sub("", cleaned)
    return " ".join(cleaned.strip().split())


def clean_text_for_llm(text: str, role: str = "page") -> str:
    """Post-fetch / pre-LLM light cleanup. Does not re-extract HTML."""
    if not text:
        return ""
    cleaned = repair_unicode(text)
    cleaned = ZERO_WIDTH.sub("", cleaned)
    cleaned = _MULTI_WS.sub(" ", cleaned)
    cleaned = _MULTI_BLANK_LINES.sub("\n\n", cleaned)
    return cleaned.strip()
