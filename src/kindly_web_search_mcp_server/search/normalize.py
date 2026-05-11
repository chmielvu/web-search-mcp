from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "ref",
    "ref_src",
}


def normalize_query(query: str) -> str:
    return " ".join(query.strip().split())


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
