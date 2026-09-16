from __future__ import annotations

import re
from urllib.parse import urlparse

_GITHUB_HOSTS = {"github.com", "www.github.com"}
# H16: segment validation — the old ^[^/\s]+/[^/\s]+$ accepted "owner/.."
# (traversal-looking identity later used verbatim in API paths) and
# "owner/name#L10" (fragment suffix not stripped on the bare form; identity
# is owner/name, so a ref suffix is stripped, matching URL-form handling).
_REPOSITORY = re.compile(r"^(?P<owner>[\w.-]+)/(?P<name>[\w.-]+?)(?:#(?P<ref>[\w./-]+))?$")


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
    match = _REPOSITORY.fullmatch(normalized)
    if match is None:
        raise ValueError("repository must use the owner/name form")
    owner, name = match.group("owner"), match.group("name")
    if owner == ".." or name == ".." or ".." in (owner, name):
        raise ValueError("repository must use the owner/name form")
    return f"{owner}/{name}"


__all__ = ["normalize_github_repository"]
