"""URL canonicalization utilities.

Removes tracking query parameters (utm_*, fbclid, gclid, etc.),
strips fragments, normalizes trailing slashes, and lowercases
scheme + host. Slug-style path variants (date-as-path vs date-in-slug,
underscore vs hyphen word separators) fold to one comparable form so
RRF dedup collapses duplicate citations of the same article.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS: frozenset[str] = frozenset(
    {"fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "mkt_tok", "ref", "ref_src"}
)

_DATE_PATH_RE = re.compile(r"/(\d{4})/(\d{1,2})/(\d{1,2})(?=/|$)")
_DATE_SLUG_RE = re.compile(r"/(\d{4})-(\d{1,2})-(\d{1,2})-")


def _fold_slug(path: str) -> str:
    """Fold CMS slug variants into one comparable form.

    Content platforms expose the same article under different slugs —
    ``/blog/2026/04/16/title`` (path-style date) vs ``/blog/2026-04-16-title``
    (slug-style date), underscore vs hyphen word separators. Both date forms
    fold to ``-YYYY-MM-DD-`` and separator runs collapse to a single hyphen
    so dedup keys collapse the variants.
    """
    m = _DATE_PATH_RE.search(path)
    if m:
        prefix = path[: m.start()].rstrip("/")
        rest = path[m.end() :]
        path = (
            f"{prefix}/{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}-{rest.lstrip('/')}"
        )
    m = _DATE_SLUG_RE.search(path)
    if m:
        path = f"{path[: m.start()]}/{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}-{path[m.end() :]}"
    folded = path.replace("_", "-").replace("+", "-")
    while "--" in folded:
        folded = folded.replace("--", "-")
    return folded


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower().removeprefix("www.")
    path = parts.path or "/"
    query_items = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=False)
        if not key.startswith("utm_") and key not in _TRACKING_PARAMS
    ]
    query = urlencode(query_items, doseq=True)
    fragment = ""
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    if not scheme or not netloc:
        return url.strip()
    path = _fold_slug(path)
    return urlunsplit((scheme, netloc, path, query, fragment))


def extract_domain_from_url(url: str) -> str | None:
    """Extract and normalize domain from URL.

    Returns the hostname in lowercase with 'www.' prefix removed.
    Returns None for invalid or empty URLs.
    Accepts bare hosts (e.g. "docs.python.org") as well as full URLs.
    """
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if not host and not parsed.scheme and not parsed.netloc:
            # Bare-domain form (e.g. "docs.python.org"): urlsplit puts it in path.
            candidate = parsed.path.split("/", 1)[0].strip()
            if candidate and "." in candidate:
                return candidate.lower().removeprefix("www.")
        if host:
            return host.lower().removeprefix("www.")
    except Exception:
        pass
    return None
