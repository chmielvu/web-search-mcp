from __future__ import annotations

import re

_LITERAL_PATTERN = re.compile(
    r"""
    (?:
        "[^"]+"
        | site:\S+
        | filetype:\S+
        | intitle:\S+
        | inbody:\S+
        | ext:\S+
        | \bAND\b
        | \bOR\b
        | \bNOT\b
        | \+[A-Za-z0-9]
        | -[A-Za-z0-9]
    )
    """,
    re.VERBOSE,
)


def detect_literal_passthrough(query: str) -> bool:
    """Return whether expert syntax must bypass semantic query rewriting."""
    return bool(_LITERAL_PATTERN.search(query))
