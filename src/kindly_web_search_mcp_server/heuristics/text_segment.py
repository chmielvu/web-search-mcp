"""Glued-token segmentation for search queries (wordninja, always-on).

Splits single lowercase alphabetic runs (e.g. ``toplawyersinnewyork``) into
space-separated words using wordninja's English-Wikipedia unigram DP model.
Identifiers, operators, camelCase, snake_case, dotted paths, domains and
digit-bearing tokens are structurally excluded so downstream feature
extraction (REPO_HINT/_DOTTED_IDENT/_CAMEL/_SNAKE) is never disturbed.

Breaking/non-gated design: no feature flag, unconditional import, hardcoded
threshold. Callers receive the segmented form as an *additive variant*
(``QueryFeatures.segmented_variants``); ``cleaned`` stays untouched.
"""
from __future__ import annotations

import wordninja

# Hardcoded per non-gated mandate; promote to settings only if a real need emerges.
MIN_TOKEN_LEN = 10
MAX_TOKEN_LEN = 40  # bounds the DP cost of pathological whitespace-free runs

_OPERATOR_PREFIXES = (
    "repo:",
    "org:",
    "user:",
    "lang:",
    "language:",
    "path:",
    "file:",
    "site:",
    "patternType:",
)


def is_eligible_token(token: str) -> bool:
    """True when a whitespace token may be run through wordninja.

    Eligible: pure alphabetic, all-lowercase, between MIN_TOKEN_LEN and
    MAX_TOKEN_LEN chars, no underscores/dots, not an operator token. Quotes
    are tolerated around the core but quotes alone never make a token eligible.
    """
    core = token.strip("\"'")
    if len(core) < MIN_TOKEN_LEN or len(core) > MAX_TOKEN_LEN:
        return False
    if not core.isalpha() or core != core.lower():
        return False
    if "_" in core or "." in core:
        return False
    lower = token.lower()
    if lower.startswith(_OPERATOR_PREFIXES):
        return False
    return True


def segment_query(text: str) -> str | None:
    """Split eligible glued tokens in-place; return None when nothing changed."""
    if not text:
        return None
    out: list[str] = []
    changed = False
    for token in text.split():
        if is_eligible_token(token):
            parts = wordninja.split(token.strip("\"'"))
            if len(parts) > 1 and " ".join(parts) != token.strip("\"'"):
                out.append(" ".join(parts))
                changed = True
                continue
        out.append(token)
    if not changed:
        return None
    return " ".join(out)
