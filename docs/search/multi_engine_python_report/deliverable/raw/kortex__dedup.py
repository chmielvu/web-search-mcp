"""Cross-source de-duplication.

Three layers, cheap → expensive:
  1. Exact identity — canonical URL key (scheme/www/tracking stripped).
  2. Near-duplicate — normalized-title similarity (difflib ratio).
  3. Embedding — cosine similarity (bi-encoder), ASCII-dominant docs only
     (the embed model is English-oriented; difflib still covers CJK).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import numpy as np

from .config import EMBEDDING_DEDUP
from .models import Result

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "ref_src", "fbclid", "gclid", "igshid",
}
_EMBED_THRESHOLD = 0.93


def canonical_url(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urlparse(url)
        host = (parts.netloc or "").lower()
        host = re.sub(r"^www\.", "", host)
        scheme = parts.scheme.lower() or "http"
        path = parts.path.rstrip("/") or "/"
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if k.lower() not in _TRACKING_PARAMS]
        return urlunparse((scheme, host, path, "", urlencode(query, doseq=True), ""))
    except (ValueError, TypeError):
        return url.lower().strip()


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def _is_ascii_dominant(text: str) -> bool:
    if not text:
        return False
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    return ascii_chars / len(text) >= 0.8


def dedup(results: list[Result], embeddings=None,
          near_dup_threshold: float = 0.92) -> list[Result]:
    """Return results with exact + near-duplicates collapsed (first wins).

    `embeddings` is an optional normalized (n x d) ndarray aligned with
    `results` for embedding-based dedup (English docs only).
    """
    seen: dict[str, Result] = {}
    order: list[str] = []
    norm_titles: dict[str, str] = {}
    emb_vec: dict[str, object] = {}
    emb_doc: dict[str, str] = {}

    use_emb = EMBEDDING_DEDUP and embeddings is not None and len(embeddings) == len(results)
    # precompute docs + ascii flags once (the old inner loop rebuilt the doc
    # string for every (result, existing) pair — O(n²) string builds)
    docs = [r.title + " " + r.snippet[:200] for r in results]
    ascii_flags = [_is_ascii_dominant(d) for d in docs]

    for idx, r in enumerate(results):
        ckey = canonical_url(r.identity())
        if not ckey:
            continue
        if ckey in seen:
            _merge(seen[ckey], r)
            continue

        # near-duplicate check against accepted results
        nt = _norm_title(r.title)
        dup = False
        for existing_key in order:
            existing_nt = norm_titles.get(existing_key, "")
            if existing_nt and _similar(existing_nt, nt, near_dup_threshold):
                _merge(seen[existing_key], r)
                dup = True
                break
            if (use_emb and existing_key in emb_vec and ascii_flags[idx]
                    and _is_ascii_dominant(emb_doc[existing_key])):
                    sim = float(np.dot(
                        np.asarray(emb_vec[existing_key]), np.asarray(embeddings[idx])))
                    if sim >= _EMBED_THRESHOLD:
                        _merge(seen[existing_key], r)
                        dup = True
                        break
        if dup:
            continue

        seen[ckey] = r
        norm_titles[ckey] = nt
        if use_emb:
            emb_vec[ckey] = embeddings[idx]
            emb_doc[ckey] = docs[idx]
        order.append(ckey)

    return [seen[k] for k in order]


def _similar(a: str, b: str, threshold: float) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    if min(len(a), len(b)) < 8:
        return False
    return SequenceMatcher(None, a, b).ratio() >= threshold


def _merge(origin: Result, other: Result) -> None:
    """Fold a duplicate into the kept result, preserving the richest fields."""
    if len(other.snippet) > len(origin.snippet):
        origin.snippet = other.snippet
    if len(other.title) > len(origin.title):
        origin.title = other.title
    if not origin.published and other.published:
        origin.published = other.published
    if other.meta:
        for k, v in other.meta.items():
            origin.meta.setdefault(k, v)
    origin.meta.setdefault("_also_found_by", []).append(other.source)
