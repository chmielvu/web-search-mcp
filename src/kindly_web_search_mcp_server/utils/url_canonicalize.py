"""URL canonicalization utilities.

Removes tracking query parameters (utm_*, fbclid, gclid, etc.),
strips fragments, normalizes trailing slashes, and lowercases
scheme + host.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS: frozenset[str] = frozenset(
    {"fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "mkt_tok", "ref", "ref_src"}
)


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path or ""
    query_items = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=False)
        if not key.startswith("utm_") and key not in _TRACKING_PARAMS
    ]
    query = urlencode(query_items, doseq=True)
    fragment = ""
    if path not in ("", "/") and path.endswith("/"):
        path = path[:-1]
    if not scheme or not netloc:
        return url.strip()
    return urlunsplit((scheme, netloc, path, query, fragment))


def extract_domain_from_url(url: str) -> str | None:
    """Extract and normalize domain from URL.

    Returns the hostname in lowercase with 'www.' prefix removed.
    Returns None for invalid or empty URLs.
    """
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if host:
            return host.lower().removeprefix("www.")
    except Exception:
        pass
    return None
