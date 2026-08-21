from __future__ import annotations

import re
from urllib.parse import urlparse


_GITHUB_HOSTS = {"github.com", "www.github.com"}
_REPOSITORY = re.compile(r"^[^/\s]+/[^/\s]+$")


def normalize_github_repository(repository: str) -> str:
    """Return a canonical ``owner/name`` GitHub repository identity.

    Accepts canonical owner/name values, HTTP(S) GitHub URLs, and GitHub's
    scp-style SSH syntax. Repository URLs may include a trailing ``.git``;
    URL query strings and fragments are ignored because they are not part of
    repository identity.
    """
    value = repository.strip()
    if not value:
        raise ValueError("repository must use the owner/name form")

    lowered = value.casefold()
    if lowered.startswith(("https://", "http://")):
        parsed = urlparse(value)
        if (parsed.hostname or "").casefold() not in _GITHUB_HOSTS:
            raise ValueError("repository must use a GitHub owner/name form")
        normalized = parsed.path
    elif lowered.startswith("git@github.com:"):
        normalized = value.split(":", 1)[1]
    else:
        normalized = value

    normalized = normalized.strip().strip("/")
    if normalized.casefold().endswith(".git"):
        normalized = normalized[:-4].rstrip("/")
    if not _REPOSITORY.fullmatch(normalized):
        raise ValueError("repository must use the owner/name form")
    return normalized


__all__ = ["normalize_github_repository"]
