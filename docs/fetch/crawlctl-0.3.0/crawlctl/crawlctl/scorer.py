"""Type-calibrated quality scoring for cleaned markdown -> publish/review/reject.

Composite 0-100 from normalized signals with per-content-type weights, extended
with FineWeb/Gopher/C4 page-level heuristics (see RESEARCH.md):

* terminal-punctuation line ratio (FineWeb: < 0.12 = junk; best single cheap signal)
* short-line ratio (FineWeb: >= 0.67 reject)
* duplicate-line char fraction (Gopher/FineWeb: 0.1-0.2 = leftover nav/footer)
* symbol-to-word ratio (Gopher: <= 0.1; soft signal on code-heavy pages)
* stopword ratio + sentence-length sanity (kills menu junk)
* avg word length 3-10 (Gopher)

Bands (default, calibrated on the test fixtures): publish >= 65, review >= 40,
reject below. No published RAG band standard exists; tune via env
CRAWLCTL_PUBLISH_BAND / CRAWLCTL_REVIEW_BAND if your corpus differs.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

from .siteprof import TypeProfile, profile_for

_STOPWORDS = frozenset("""a an the and or but if then than that this these those of in on at to
for with from by as is are was were be been being it its it's their they he she we you i not no
do does did have has had will would can could should may might must about into over under more
most other some such only own same so too very s t don now when where why how all any both each
few nor""".split())

_TAG_RE = re.compile(r"<[a-zA-Z/][^>]*>")
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_TERMINAL_PUNCT_RE = re.compile(r"[.!?…:;\"')\]]\s*$")
_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def _publish_band() -> int:
    return int(os.getenv("CRAWLCTL_PUBLISH_BAND", "65"))


def _review_band() -> int:
    return int(os.getenv("CRAWLCTL_REVIEW_BAND", "40"))


def _ramp(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 1.0
    v = (x - lo) / (hi - lo)
    return max(0.0, min(1.0, v))


def _fence_mask(text: str) -> Tuple[set, bool]:
    masked, open_f = set(), False
    for i, ln in enumerate(text.split("\n")):
        if _FENCE_RE.match(ln):
            masked.add(i)
            open_f = not open_f
            continue
        if open_f:
            masked.add(i)
    return masked, open_f


def score_markdown(text: str, content_type: str = "generic",
                   cleaning_report: Optional[dict] = None) -> dict:
    prof: TypeProfile = profile_for(content_type)
    masked, open_fence = _fence_mask(text)
    lines = text.split("\n")
    body = "\n".join(ln for i, ln in enumerate(lines) if i not in masked)

    words_list = _WORD_RE.findall(text)
    words = len(words_list)
    prose_chars = sum(len(ln) for i, ln in enumerate(lines) if i not in masked)

    links = re.findall(r"\]\(\s*<?([^)\s>]+)>?", text)
    link_chars = sum(len(h) for h in links)
    linkd = link_chars / max(1, prose_chars)

    # ---- prose: stopword ratio, sentence sanity, terminal punct, word length
    low = body.lower()
    toks = re.findall(r"[a-z']+", low)
    stop_ratio = sum(1 for t in toks if t in _STOPWORDS) / max(1, len(toks))
    sentences = [s for s in re.split(r"[.!?]+\s", body) if len(s.split()) >= 3]
    avg_sent = (sum(len(s.split()) for s in sentences) / len(sentences)) if sentences else 0.0
    nonempty = [ln.strip() for ln in body.split("\n") if ln.strip()]
    punct_ratio = (sum(1 for ln in nonempty if _TERMINAL_PUNCT_RE.search(ln))
                   / max(1, len(nonempty)))
    avg_wlen = (sum(len(w) for w in words_list) / max(1, words))
    wordlen_ok = 1.0 if 3.0 <= avg_wlen <= 10.0 else max(0.0, 1.0 - abs(avg_wlen - 6.5) / 6.5)
    prose = (0.35 * _ramp(stop_ratio, 0.15, 0.32)
             + 0.30 * (1.0 - min(1.0, abs(avg_sent - 18) / 18))
             + 0.25 * _ramp(punct_ratio, 0.12, 0.35)
             + 0.10 * wordlen_ok)

    # ---- structure: single H1, no skipped levels, heading count
    h1 = len(re.findall(r"^#\s", body, re.MULTILINE))
    headings = len(re.findall(r"^#{1,6}\s", body, re.MULTILINE))
    levels = [len(m) for m in re.findall(r"^(#{1,6})\s", body, re.MULTILINE)]
    gaps = sum(1 for a, b in zip(levels, levels[1:]) if b > a + 1)
    if headings == 0:
        structure = 0.4        # neutral: no headings is normal for news, poor for docs
    else:
        structure = (0.4 * (1.0 if h1 <= 1 else 0.3)
                     + 0.3 * _ramp(headings, 1, 6)
                     + 0.3 * (1.0 - min(1.0, gaps / 3)))

    # ---- code expectation
    code_blocks = text.count("```") // 2
    code = _ramp(code_blocks, 0, 3) if prof.code_expect == "required" else 1.0

    # ---- boilerplate removed during cleaning (lower removal = cleaner source)
    boiler = 1.0
    if cleaning_report and cleaning_report.get("chars_before"):
        removed = cleaning_report.get("chars_removed", 0) / max(1, cleaning_report["chars_before"])
        boiler = max(0.0, 1.0 - _ramp(removed, 0.35, 0.75))

    # ---- hygiene: leftover tags, fences, dup lines, caps, symbol & newline ratios
    leftover_tags = len(_TAG_RE.findall(body))
    seen: set = set()
    dup = 0
    dup_chars = 0
    total_chars = 0
    for ln in body.split("\n"):
        k = ln.strip().lower()
        if not k:
            continue
        total_chars += len(k)
        if k in seen:
            dup += 1
            dup_chars += len(k)
        seen.add(k)
    dup_ratio = dup / max(1, sum(1 for ln in body.split("\n") if ln.strip()))
    dup_char_frac = dup_chars / max(1, total_chars)
    caps = sum(1 for c in text if c.isupper()) / max(1, len(text))
    symbols = sum(1 for c in body if not c.isalnum() and not c.isspace())
    symbol_ratio = symbols / max(1, words)
    newline_ratio = sum(1 for ln in body.split("\n") if ln.strip()) / max(1, words)
    short_lines = sum(1 for ln in nonempty if len(ln) < 30) / max(1, len(nonempty))

    hygiene = ((1.0 - _ramp(leftover_tags, 1, 12)) * 0.25
               + (0.0 if open_fence else 1.0) * 0.25
               + (1.0 - _ramp(max(dup_ratio, dup_char_frac * 2), 0.05, 0.3)) * 0.25
               + (1.0 - _ramp(caps, 0.15, 0.4)) * 0.10
               + (1.0 - _ramp(symbol_ratio, 0.10, 0.30)) * 0.10
               + (1.0 - _ramp(newline_ratio, 0.30, 0.60)) * 0.05)

    # short-line flood (FineWeb): lists/FAQ junk; docs get a pass (menus already cleaned)
    if short_lines >= 0.67 and prof.name != "docs":
        prose *= 0.6

    # ---- weights per content type
    if prof.name == "docs":
        w = dict(words=0.18, prose=0.15, linkd=0.12, structure=0.18, boilerplate=0.12, code=0.13, hygiene=0.12)
    elif prof.name == "news":
        w = dict(words=0.22, prose=0.22, linkd=0.18, structure=0.12, boilerplate=0.10, code=0.00, hygiene=0.16)
    elif prof.name == "wiki":
        w = dict(words=0.20, prose=0.18, linkd=0.14, structure=0.16, boilerplate=0.14, code=0.02, hygiene=0.16)
    else:
        w = dict(words=0.20, prose=0.20, linkd=0.15, structure=0.15, boilerplate=0.10, code=0.05, hygiene=0.15)

    subscore = {
        "words": _ramp(words, prof.min_words, prof.target_words * 2),
        "prose": prose,
        "linkd": 1.0 - _ramp(linkd, prof.linkd_tolerance * 0.6, prof.linkd_tolerance * 1.5),
        "structure": structure,
        "boilerplate": boiler,
        "code": code,
        "hygiene": hygiene,
    }
    value = round(100 * sum(w[k] * subscore[k] for k in w), 1)

    # FineWeb-style hard floors: a sub-40-word "document" has no RAG value
    # regardless of how clean it is (hard rule, not a weighted signal).
    hard_notes = []
    if words < 40:
        value = min(value, 35.0)
        hard_notes.append(f"hard floor: {words} words < 40 after cleaning -> reject")

    band = "publish" if value >= _publish_band() else (
        "review" if value >= _review_band() else "reject")

    notes = list(hard_notes)
    if words < prof.min_words:
        notes.append(f"words {words} < {prof.min_words} floor for {prof.name}")
    if linkd > prof.linkd_tolerance:
        notes.append(f"link density {linkd:.2f} > {prof.linkd_tolerance} tolerance")
    if leftover_tags:
        notes.append(f"{leftover_tags} residual HTML tags")
    if open_fence:
        notes.append("unbalanced code fence")
    if stop_ratio < 0.12 and words > 100:
        notes.append("low stopword ratio - content may be link/menu junk")
    if punct_ratio < 0.12 and words > 80 and prof.name not in ("docs",):
        notes.append(f"terminal-punct line ratio {punct_ratio:.2f} < 0.12 (FineWeb junk signal)")
    if short_lines >= 0.67 and prof.name != "docs":
        notes.append(f"short-line ratio {short_lines:.2f} >= 0.67 (FineWeb junk signal)")
    if dup_char_frac >= 0.10:
        notes.append(f"duplicate-line char fraction {dup_char_frac:.2f} >= 0.10")

    return {"value": value, "band": band, "content_type": prof.name,
            "signals": {"words": words, "link_density": round(linkd, 3),
                        "stopword_ratio": round(stop_ratio, 3),
                        "avg_sentence_words": round(avg_sent, 1),
                        "terminal_punct_ratio": round(punct_ratio, 3),
                        "short_line_ratio": round(short_lines, 3),
                        "avg_word_length": round(avg_wlen, 2),
                        "headings": headings, "h1_count": h1, "heading_gaps": gaps,
                        "code_blocks": code_blocks, "leftover_html_tags": leftover_tags,
                        "duplicate_line_ratio": round(dup_ratio, 3),
                        "duplicate_char_fraction": round(dup_char_frac, 3),
                        "symbol_to_word_ratio": round(symbol_ratio, 3),
                        "newline_to_word_ratio": round(newline_ratio, 3),
                        "subscores": {k: round(v, 3) for k, v in subscore.items()}},
            "notes": notes}


def compare_candidates(cands: List[Tuple[str, str, Optional[dict], str]]) -> Tuple[str, str, dict]:
    """Pick best of [(engine, text, cleaning_report, content_type), ...].

    Type-aware (the prototype re-scored everything as 'generic', losing type
    calibration). Tie-breaks: higher score -> crawl4ai preferred (source of
    truth for fit_markdown) -> longer text.
    """
    best = None
    for engine, text, report, ctype in cands:
        s = score_markdown(text, content_type=ctype or "generic", cleaning_report=report)
        engine_pref = 1 if engine == "crawl4ai" else 0
        key = (s["value"], engine_pref, len(text))
        if best is None or key > best[0]:
            best = (key, engine, text, s)
    _, engine, text, s = best
    return engine, text, s
