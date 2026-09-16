"""Tiered markdown cleaner producing RAG-safe markdown.

T0  unicode/encoding normalization (ftfy when available), HTML debris removal
T1  markdown-it AST-guided LINE SURGERY (line counts preserved): heading
    normalization, link canonicalization/policies (label-aware), image policy
T2  block-level statistical cleaning (lines deleted here): type junk patterns,
    breadcrumbs, link-density gates, per-site repeated-block DB, nav-run
    collapse, citation stripping (code-span protected), HTML->MD tables,
    FineWeb-inspired passes (dup-line collapse, JS debris blocks)
T3  polish: blank runs, fence balance, optional mdformat pass

Design invariant: Tier-1 edits never reflow unrelated content (no token-stream
re-serialization), so tables/code are preserved byte-for-byte; all deletions
are confined to Tier-2 line ranges. Tables identified by the Chonkie
MarkdownChef backend are hash-protected from Tier-2 junk drops.

Evidence base (see RESEARCH.md): Kohlschütter WSDM 2010 (text/link density),
jusText 2011 (short/near-good thresholds), trafilatura favor_precision/recall,
FineWeb/Gopher/C4 page-level heuristics (dup-line fraction, JS debris,
short-line junk), Firecrawl/Jina markdown hygiene conventions.
"""

from __future__ import annotations

import hashlib
import html as _html
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from markdown_it import MarkdownIt

from .siteprof import TypeProfile, detect_content_type, profile_for

# ------------------------------------------------------------------ regexes
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_JS_LINK_RE = re.compile(r"\[([^\]]*)\]\(\s*(?:javascript|vbscript):[^)]*\)", re.IGNORECASE)
_DATA_IMG_RE = re.compile(r"!\[[^\]]*\]\(\s*data:[^)]*\)", re.IGNORECASE)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]*)([^)]*)\)")
# full markdown link WITH label capture (fixes prototype's label-losing regex)
_FULL_LINK_RE = re.compile(r"\[([^\]]*)\]\(\s*<?([^)\s>]+)>?([^)]*)\)")
_ANCHOR_ONLY_LINK_RE = re.compile(r"\[\s*\]\(#[^)]*\)")
_CITATION_RE = re.compile(
    r"\[\s*(?:\d{1,3}(?:\s*[,–-]\s*\d{1,3})*|citation needed|edit|note \d+|w?\.?\s*&?\s*n\.?\s*\d+)\s*\]",
    re.IGNORECASE,
)
_BREADCRUMB_RE = re.compile(r"^[\w\s.'\"]+(\s*(»|›|>>|/|\\|—>)\s*[\w\s.'\"]+){1,6}$")
_COPY_LINE_RE = re.compile(r"^\s*(copy|copy code|copy to clipboard|copied!?)\s*$", re.IGNORECASE)
_TAG_STRAY_RE = re.compile(r"</?(?:span|div|font|b|i|u|small|sub|sup|abbr|mark)\b[^>]*>", re.IGNORECASE)
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")
_ENTITY_SET = ("amp", "lt", "gt", "quot", "#39", "nbsp", "mdash", "ndash", "rsquo", "lsquo",
               "rdquo", "ldquo", "hellip", "copy", "reg", "trade", "deg", "plusmn", "times",
               "middot", "laquo", "raquo", "bull", "dagger", "eacute", "egrave", "uuml",
               "ouml", "auml", "ntilde", "aacute", "oacute", "iacute", "uacute", "ccedil")
_ENTITY_RE = re.compile(r"&(" + "|".join(_ENTITY_SET) + r");")
_CODE_SPAN_SPLIT = re.compile(r"(`[^`\n]*`)")

_MOJIBAKE = {
    "â€™": "’", "â€˜": "‘", "â€œ": "“", "â€\x9d": "”", "â€“": "–", "â€”": "—",
    "â€¦": "…", "Â ": " ", "Â»": "»", "Â«": "«", "Ã©": "é", "Ã¨": "è", "Ã¼": "ü",
    "Ã¶": "ö", "Ã¤": "ä", "Ã±": "ñ", "Ã³": "ó", "Ã­": "í", "Ãº": "ú",
}
_LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl"}

_TRACKING_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "gclid", "fbclid", "mc_cid", "mc_eid", "igshid", "vero_id", "ref", "ref_src",
    "spm", "share_id",
}

_HTML_BLOCK_TAGS = ("script", "style", "iframe", "noscript")
_SAFE_CELL_RE = re.compile(r"<[^>]+>|\|")
_JAVASCRIPTY_RE = re.compile(r"[{};]")
_ALNUM_RE = re.compile(r"[A-Za-z0-9]")


def _use_ftfy() -> bool:
    return os.getenv("CRAWLCTL_DISABLE_FTFY", "0") != "1"


# ------------------------------------------------------------------ options
@dataclass
class CleaningOptions:
    source_url: Optional[str] = None
    content_type: str = "auto"              # auto|docs|wiki|blog|news|generic
    link_mode: str = "keep"                 # keep | text | drop
    image_policy: str = "keep"              # keep | alt_only | drop
    demote_extra_h1: bool = True
    fix_heading_gaps: bool = True
    strip_tracking_params: bool = True
    toc_links_to_text: bool = True          # [Title](#anchor) -> Title
    citation_mode: str = "auto"             # auto | strip | keep (auto: strip for wiki)
    site_boilerplate: Optional[dict] = None  # {block_hash: page_count} for this domain
    boilerplate_min_count: int = 3
    protected_hashes: Optional[Set[str]] = None  # block hashes never junk-dropped
    junk_extra: Tuple[re.Pattern, ...] = field(default_factory=tuple)


@dataclass
class CleanResult:
    text: str
    content_type: str = "generic"
    type_confidence: float = 0.0
    edits: dict = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    block_hashes: List[str] = field(default_factory=list)   # for BoilerplateDB.observe
    structure: dict = field(default_factory=dict)           # n_tables/n_code/n_images


# ------------------------------------------------------------------ helpers
def _parser() -> MarkdownIt:
    try:
        return MarkdownIt("gfm-like")
    except Exception:  # pragma: no cover - older markdown-it-py without gfm preset
        return MarkdownIt("commonmark").enable(["table", "strikethrough"])


def fence_mask(lines: List[str]) -> Tuple[Set[int], bool]:
    """Line indices inside/including fences; whether a fence was left open."""
    masked: Set[int] = set()
    open_fence = False
    for i, ln in enumerate(lines):
        m = _FENCE_RE.match(ln)
        if m:
            marker = m.group(1)
            # closing fence must use the same char as the opener
            if not open_fence or marker[0] in ("`", "~"):
                masked.add(i)
                open_fence = not open_fence
            continue
        if open_fence:
            masked.add(i)
    return masked, open_fence


def _canonicalize_url(href: str, base: Optional[str], strip_tracking: bool) -> Optional[str]:
    if not href:
        return href
    h = href.strip()
    low = h.lower()
    if low.startswith(("#", "mailto:", "tel:")):
        return h
    if low.startswith(("javascript:", "vbscript:", "data:")):
        return None
    if not _SCHEME_RE.match(h) and not h.startswith("//") and base:
        h = urljoin(base, h)
    if h.startswith("//"):
        h = "https:" + h
    if strip_tracking:
        s = urlsplit(h)
        if s.query:
            kept = [(k, v) for k, v in parse_qsl(s.query, keep_blank_values=True)
                    if k.lower() not in _TRACKING_KEYS]
            h = urlunsplit(s._replace(query=urlencode(kept)))
    return h


def block_hash(text: str) -> str:
    """Fingerprint of a block for the per-site boilerplate DB.

    Strips markdown link/image syntax, lowercases, keeps alphanumeric words
    (unicode-aware) so navigation repeats hash equal across pages.
    """
    t = _FULL_LINK_RE.sub(r"\1", text)
    t = _IMAGE_RE.sub(" ", t)
    words = re.findall(r"[^\W_]+", t.lower(), re.UNICODE)
    return hashlib.sha1(" ".join(words).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ Tier 0
def _tier0(text: str, stats: dict) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # ftfy first (purpose-built mojibake fixer, SOTA per RESEARCH.md); native
    # curated map as fast-path supplement + fallback when ftfy is disabled.
    # unescape_html=False: ftfy would otherwise decode HTML entities inside
    # markdown code spans; our own fence-aware decode (below) is code-aware.
    if _use_ftfy():
        try:
            import ftfy

            fixed = ftfy.fix_text(text, unescape_html=False)
            if fixed != text:
                stats["ftfy_fixes"] = 1
            text = fixed
        except Exception:
            pass
    for bad, good in _MOJIBAKE.items():
        if bad in text:
            text = text.replace(bad, good)
            stats["mojibake_fixed"] = stats.get("mojibake_fixed", 0) + 1
    text = unicodedata.normalize("NFC", text)
    text = _ZERO_WIDTH_RE.sub("", text)
    text = _CTRL_RE.sub("", text)
    for k, v in _LIGATURES.items():
        text = text.replace(k, v)
    text = text.replace("\u00a0", " ").replace("\u00ad", "")

    lines = text.split("\n")
    masked, _ = fence_mask(lines)
    out: List[str] = []
    skip_tag = None
    for i, ln in enumerate(lines):
        if i in masked:                       # never touch code fences
            out.append(ln)
            continue
        if skip_tag:
            if f"</{skip_tag}" in ln.lower():
                skip_tag = None
                ln = ln.split(">", 1)[-1] if ">" in ln else ""
            else:
                stats["html_blocks_removed"] = stats.get("html_blocks_removed", 0) + 1
                continue
        low = ln.lower()
        for tag in _HTML_BLOCK_TAGS:
            if f"<{tag}" in low and f"</{tag}" not in low:
                skip_tag = tag
                break
        # single-line complete blocks: <script>...</script> on one line
        for tag in _HTML_BLOCK_TAGS:
            pattern = re.compile(rf"<{tag}\b[^>]*>.*?</{tag}\s*>", re.IGNORECASE | re.DOTALL)
            if pattern.search(ln):
                ln = pattern.sub("", ln)
                stats["html_blocks_removed"] = stats.get("html_blocks_removed", 0) + 1
        if skip_tag:
            stats["html_blocks_removed"] = stats.get("html_blocks_removed", 0) + 1
            continue
        # single-line comment strip
        while "<!--" in ln and "-->" in ln:
            ln = re.sub(r"<!--.*?-->", "", ln, count=1)
            stats["comments_removed"] = stats.get("comments_removed", 0) + 1
        if "<!--" in ln:
            stats["comments_removed"] = stats.get("comments_removed", 0) + 1
            ln = ln.split("<!--", 1)[0]
        # fence-aware, code-span-protected entity decode
        parts = _CODE_SPAN_SPLIT.split(ln)
        for j, part in enumerate(parts):
            if j % 2 == 0:
                dec = _html.unescape(part)
                dec = _ENTITY_RE.sub(lambda m: _html.unescape(f"&{m.group(1)};"), dec)
                if dec != part:
                    stats["entities_decoded"] = stats.get("entities_decoded", 0) + 1
                parts[j] = dec
        ln = "".join(parts)
        ln = _TAG_STRAY_RE.sub("", ln)
        ln = re.sub(r"<br\s*/?>", "\n", ln, flags=re.IGNORECASE)
        for sub in ln.split("\n"):
            out.append(sub)
    return "\n".join(out)


# ------------------------------------------------------------------ Tier 1
def _heading_ops(tokens, lines: List[str], opts: CleaningOptions, stats: dict) -> None:
    prev, seen_h1 = 0, False
    i, n = 0, len(tokens)
    while i < n:
        t = tokens[i]
        i += 1
        if t.type != "heading_open" or not t.map:
            continue
        level = int(t.tag[1])
        title = ""
        if i < n and tokens[i].type == "inline":
            title = tokens[i].content.strip()
            i += 1
        new = level
        if level == 1:
            if seen_h1 and opts.demote_extra_h1:
                new = 2
            else:
                seen_h1 = True
        if opts.fix_heading_gaps and prev and new > prev + 1:
            new = prev + 1
        prev = new
        if new == level:
            # still normalize setext to ATX below via map span
            pass
        s, e = t.map
        # setext headings map to exactly 2 lines (title + underline); always
        # normalize to ATX for RAG consistency
        setext = (e - s) == 2 and s + 1 < len(lines) and bool(lines[s + 1].strip())
        lines[s] = "#" * new + ((" " + title) if title else "")
        if setext:
            lines[s + 1] = ""
        if setext or new != level:
            stats["headings_fixed"] = stats.get("headings_fixed", 0) + 1


def _link_ops(tokens, lines: List[str], opts: CleaningOptions, stats: dict) -> None:
    """Canonicalize links + apply link/image policies, preserving line counts.

    Label-aware: text/drop modes and TOC flattening consume the whole
    [label](target) construct (the prototype's regex left dangling brackets).
    Lines with flattened TOC anchors are recorded (internal _toc_line_set)
    so Tier-2 nav-run collapse can still recognize them as navigation.
    """
    toc_lines: set = stats.setdefault("_toc_line_set", set())
    def apply_line(line: str) -> str:
        if "](" not in line:
            return line
        # images first
        if opts.image_policy != "keep":
            def img_sub(m: re.Match) -> str:
                alt = m.group(1).strip()
                stats["images_stripped"] = stats.get("images_stripped", 0) + 1
                return alt if opts.image_policy == "alt_only" and alt else ""
            line = _IMAGE_RE.sub(img_sub, line)
        line = _DATA_IMG_RE.sub("", line)
        line = _ANCHOR_ONLY_LINK_RE.sub("", line)

        def link_sub(m: re.Match) -> str:
            label, href, title_part = m.group(1), m.group(2), m.group(3) or ""
            if href.startswith("#"):
                if opts.toc_links_to_text:
                    stats["toc_links_to_text"] = stats.get("toc_links_to_text", 0) + 1
                    return label
                return m.group(0)
            new = _canonicalize_url(href, opts.source_url, opts.strip_tracking_params)
            if new is None:
                stats["unsafe_links_removed"] = stats.get("unsafe_links_removed", 0) + 1
                return label
            if new != href:
                stats["links_canonicalized"] = stats.get("links_canonicalized", 0) + 1
            if opts.link_mode == "drop":
                stats["links_dropped"] = stats.get("links_dropped", 0) + 1
                return ""
            if opts.link_mode == "text":
                stats["links_to_text"] = stats.get("links_to_text", 0) + 1
                return label
            return f"[{label}]({new}{title_part})"

        return _FULL_LINK_RE.sub(link_sub, line)

    for t in tokens:
        if t.type != "inline" or not t.children or not t.map:
            continue
        for ln in range(t.map[0], min(t.map[1], len(lines))):
            if "](" not in lines[ln]:
                continue
            before = lines[ln]
            new_line = apply_line(lines[ln])
            if new_line != before:
                lines[ln] = new_line
                if "TOC" in before or (before != new_line and "](#" in before):
                    toc_lines.add(ln)
    # sweep leftover js: links on lines the AST didn't map
    lines[:] = [_JS_LINK_RE.sub(r"\1", ln) for ln in lines]


# ------------------------------------------------------------------ Tier 2
def _iter_blocks(lines: List[str]):
    """Yield (start, end, kind, line-idxs) for blank-line-separated blocks;
    fences atomic."""
    masked, _ = fence_mask(lines)
    buf: List[int] = []
    for i, ln in enumerate(lines):
        if i in masked:
            if buf:
                yield (buf[0], buf[-1] + 1, "text", buf)
                buf = []
            yield (i, i + 1, "code", [i])
            continue
        if not ln.strip():
            if buf:
                yield (buf[0], buf[-1] + 1, "text", buf)
                buf = []
            continue
        buf.append(i)
    if buf:
        yield (buf[0], buf[-1] + 1, "text", buf)


def _block_text(lines: List[str], idxs: List[int]) -> str:
    return " ".join(lines[i].strip() for i in idxs).strip()


def _link_stats(text: str) -> Tuple[int, float]:
    links = _FULL_LINK_RE.findall(text)
    link_chars = sum(len(h) for _, h, _ in links)
    total = max(1, len(text))
    return len(links), link_chars / total


def _html_table_to_md(block_lines: List[str]) -> Optional[List[str]]:
    """Convert a simple contiguous <table>...</table> HTML block to pipe rows."""
    joined = "\n".join(block_lines)
    if "<table" not in joined.lower():
        return None
    rows: List[List[str]] = []
    try:
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", joined, re.IGNORECASE | re.DOTALL):
            cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.IGNORECASE | re.DOTALL)
            cells = [_SAFE_CELL_RE.sub(" ", _html.unescape(c)).strip() for c in cells]
            cells = [re.sub(r"\s+", " ", c) for c in cells]
            if any(cells):
                rows.append(cells)
    except re.error:
        return None
    if not (1 <= len(rows) <= 300) or max(len(r) for r in rows) > 12:
        return None
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return out


def _collapse_dup_lines(lines: List[str], stats: dict) -> List[str]:
    """FineWeb char-dup rule, run-local: collapse runs of >=3 identical
    non-empty lines (fence-masked) to a single line."""
    masked, _ = fence_mask(lines)
    out: List[str] = []
    run_line, run_len = None, 0
    for i, ln in enumerate(lines):
        if i in masked:
            if run_len:
                stats["dup_lines_collapsed"] = stats.get("dup_lines_collapsed", 0) + run_len - 1
            run_line, run_len = None, 0
            out.append(ln)
            continue
        key = ln.strip()
        if key and key == run_line:
            run_len += 1
            if run_len > 3:            # keep a run of three, drop beyond
                continue
        else:
            if run_len > 3:
                stats["dup_lines_collapsed"] = stats.get("dup_lines_collapsed", 0) + run_len - 3
            run_line, run_len = key, 1
        out.append(ln)
    if run_len > 3:
        stats["dup_lines_collapsed"] = stats.get("dup_lines_collapsed", 0) + run_len - 3
    return out


def _is_site_boilerplate(h: str, words: int, boiler: dict, min_count: int) -> bool:
    """Kohlschütter-style template-block test.

    ``boiler`` is the per-domain lookup from ``storage.BoilerplateDB.lookup()``:
    ``{block_hash: distinct_page_count}`` plus a ``__pages_seen`` metadata key.
    A block is site boilerplate when it appeared on at least ``min_count``
    distinct pages AND on >= 50% of the domain's observed pages — a block seen
    on 3 of 40 pages is content, on 35 of 40 it is template chrome.
    """
    if words > 60 or h not in boiler:
        return False
    count = boiler.get(h, 0)
    if count < min_count:
        return False
    pages_seen = boiler.get("__pages_seen", 0)
    if pages_seen < min_count:          # not enough evidence about this domain yet
        return False
    return count >= 0.5 * pages_seen


def _tier2(lines: List[str], opts: CleaningOptions, prof: TypeProfile,
           stats: dict) -> Tuple[List[str], List[str]]:
    # --- pass A: HTML table conversion (per contiguous block) ---
    converted: List[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if "<table" in lines[i].lower():
            j = i
            while j < n and "</table>" not in lines[j].lower():
                j += 1
            block = lines[i:min(j + 1, n)]
            md_rows = _html_table_to_md(block)
            if md_rows:
                converted.extend(md_rows)
                stats["html_tables_converted"] = stats.get("html_tables_converted", 0) + 1
                i = j + 1
                continue
        converted.append(lines[i])
        i += 1
    lines = converted

    # --- pass B: JS/curly-bracket debris blocks (C4 rule, block-scoped) ---
    masked, _ = fence_mask(lines)
    drop: Set[int] = set()
    blocks = list(_iter_blocks(lines))
    for (s, e, kind, idxs) in blocks:
        if kind == "code":
            continue
        text = _block_text(lines, idxs)
        words = len(text.split())
        if words <= 30 and "`" not in text:
            braces = len(_JAVASCRIPTY_RE.findall(text))
            alnum = max(1, len(_ALNUM_RE.findall(text)))
            if braces >= 2 and braces / alnum > 0.08:
                drop.update(idxs)
                stats["dropped_js_debris"] = stats.get("dropped_js_debris", 0) + 1

    # --- pass C: citation stripping (code-span protected) ---
    mode = opts.citation_mode
    if mode == "auto":
        mode = "strip" if prof.name == "wiki" else "keep"
    if mode == "strip":
        for i, ln in enumerate(lines):
            if i in masked or not _CITATION_RE.search(ln):
                continue
            parts = _CODE_SPAN_SPLIT.split(ln)
            for j, part in enumerate(parts):
                if j % 2 == 0 and _CITATION_RE.search(part):
                    n_hits = len(_CITATION_RE.findall(part))
                    parts[j] = _CITATION_RE.sub("", part)
                    stats["citations_stripped"] = stats.get("citations_stripped", 0) + n_hits
            lines[i] = "".join(parts)

    # --- pass D: per-block decisions ---
    patterns = tuple(prof.patterns) + tuple(opts.junk_extra)
    boiler = opts.site_boilerplate or {}
    protected = opts.protected_hashes or set()
    kept_hashes: List[str] = []
    prev_hash = None
    for (s, e, kind, idxs) in blocks:
        if kind == "code":
            continue
        if any(i in drop for i in idxs):
            continue
        text = _block_text(lines, idxs)
        if not text:
            drop.update(idxs)
            continue
        words = len(text.split())
        links, linkd = _link_stats(text)
        # labels-only plain text: link targets must not feed the breadcrumb
        # matcher ("[Page 0](/p0)" -> "Page 0 /p0" falsely reads as a path)
        plain = _FULL_LINK_RE.sub(r"\1", text)
        plain = re.sub(r"[*_`~\[\]()>#-]", "", plain).strip()
        h = block_hash(text)

        reason = None
        if h in protected:
            kept_hashes.append(h)
            continue
        if words <= 40 and any(p.match(plain) or p.search(plain) for p in patterns):
            reason = "junk"
        elif words <= 25 and _BREADCRUMB_RE.match(plain):
            reason = "breadcrumb"
        elif words <= 30 and linkd > 0.6:
            reason = "link_dense"
        elif _is_site_boilerplate(h, words, boiler, opts.boilerplate_min_count):
            reason = "site_boilerplate"
        elif h == prev_hash and words <= 80:
            reason = "duplicate"
        if reason:
            drop.update(idxs)
            stats[f"dropped_{reason}"] = stats.get(f"dropped_{reason}", 0) + 1
        else:
            prev_hash = h
            if 2 <= words <= 80:
                kept_hashes.append(h)

    # --- pass E: nav-run collapse (>=3 consecutive short link-only blocks).
    # A block is "nav-like" when nearly all of its words sit inside link
    # labels (short-URL nav items evade char-density gates), or when its lines
    # were flattened from anchor TOC links in Tier-1.
    toc_lines = stats.get("_toc_line_set", set())

    def short_nav(idxs: List[int]) -> bool:
        t = _block_text(lines, idxs)
        w = len(t.split())
        # blocks fully made of flattened anchor-TOC lines are navigation
        # regardless of word count (they were links before Tier-1 flattening)
        if idxs and len(idxs) >= 3 and all(i in toc_lines for i in idxs) and w <= 40:
            return True
        if not 0 < w <= 8:
            return False
        _, ld = _link_stats(t)
        if ld >= 0.5:
            return True
        stripped = _FULL_LINK_RE.sub(" ", t)
        outside_words = len(stripped.split())
        links = len(_FULL_LINK_RE.findall(t))
        return links >= 1 and outside_words <= 2 and links >= outside_words

    run: List[int] = []
    for (s, e, kind, idxs) in blocks + [(len(lines), len(lines), "break", [])]:
        if kind != "code" and idxs and short_nav(idxs) and not (set(idxs) & drop):
            run.extend(idxs)
            continue
        if len(run) >= 3:
            drop.update(run)
            stats["dropped_nav_run"] = stats.get("dropped_nav_run", 0) + 1
        run = []

    # --- pass F: copy-button orphans near fences (one blank line tolerated) ---
    masked, _ = fence_mask(lines)
    for i, ln in enumerate(lines):
        if i in masked:
            continue
        if _COPY_LINE_RE.match(ln):
            near_fence = False
            for j in (i + 1, i + 2):
                if j < len(lines) and lines[j].strip() and _FENCE_RE.match(lines[j]):
                    near_fence = True
                    break
                if j < len(lines) and lines[j].strip():
                    break
            for j in (i - 1, i - 2):
                if j >= 0 and lines[j].strip() and _FENCE_RE.match(lines[j]):
                    near_fence = True
                    break
                if j >= 0 and lines[j].strip():
                    break
            if near_fence:
                drop.add(i)
                stats["copy_buttons_removed"] = stats.get("copy_buttons_removed", 0) + 1

    result = [ln for i, ln in enumerate(lines) if i not in drop]
    # --- pass G: FineWeb dup-line collapse ---
    result = _collapse_dup_lines(result, stats)
    return result, kept_hashes


# ------------------------------------------------------------------ Tier 3
def _tier3(lines: List[str], stats: dict) -> List[str]:
    out, blank = [], 0
    for ln in lines:
        if not ln.strip():
            blank += 1
            if blank > 2:
                continue
        else:
            blank = 0
        out.append(ln)
    _, open_fence = fence_mask(out)
    if open_fence:
        out.append("```")
        stats["fences_closed"] = stats.get("fences_closed", 0) + 1
    return out


def _mdformat_polish(text: str, stats: dict) -> str:
    """Optional idempotent formatter pass (off unless CRAWLCTL_MDFORMAT=1).

    mdformat(+gfm) is byte-changing but rendered-content-preserving
    (validate=True re-renders and refuses content-changing edits).
    """
    if os.getenv("CRAWLCTL_MDFORMAT", "0") != "1":
        return text
    try:
        import mdformat

        out = mdformat.text(text, options={"wrap": "keep"}, check=False)
        if out != text:
            stats["mdformat_applied"] = 1
        return out
    except Exception:
        stats["mdformat_failed"] = 1
        return text


# ------------------------------------------------------------------ entry
def clean_markdown(text: str, opts: Optional[CleaningOptions] = None) -> CleanResult:
    opts = opts or CleaningOptions()
    stats: Dict[str, int] = {}
    chars_before = len(text)

    detected, conf = detect_content_type(text)
    ctype = opts.content_type if opts.content_type != "auto" else detected
    prof = profile_for(ctype)
    stats["chars_before"] = chars_before

    text = _tier0(text, stats)
    lines = [ln.rstrip() for ln in text.split("\n")]

    try:
        tokens = _parser().parse("\n".join(lines))
        _heading_ops(tokens, lines, opts, stats)
        _link_ops(tokens, lines, opts, stats)
    except Exception:
        # never fail a crawl because of a pathological parse
        stats["parse_errors"] = 1

    lines, kept_hashes = _tier2(lines, opts, prof, stats)
    lines = _tier3(lines, stats)

    result = "\n".join(lines).strip() + "\n"
    stats["chars_after"] = len(result)
    stats["chars_removed"] = max(0, chars_before - len(result))
    stats.pop("_toc_line_set", None)   # internal bookkeeping, not an edit count

    # structure stats (used by the chef backend; cheap here too)
    _, open_fence = fence_mask(lines)
    structure = {
        "n_code_blocks": sum(1 for ln in lines if _FENCE_RE.match(ln)) // 2,
        "n_images": len(_IMAGE_RE.findall(result)),
        "n_links": len(_FULL_LINK_RE.findall(result)),
        "fence_open": open_fence,
    }

    result = _mdformat_polish(result, stats)
    return CleanResult(text=result, content_type=ctype, type_confidence=conf,
                       edits=stats, block_hashes=kept_hashes, structure=structure)


# ------------------------------------------------------------------ utils
def extract_title(text: str, fallback: str = "") -> str:
    masked, _ = fence_mask(text.split("\n"))
    for i, line in enumerate(text.splitlines()):
        if i in masked:
            continue
        m = re.match(r"^#\s+(.+?)\s*#*\s*$", line)
        if m:
            return m.group(1).strip()
    return fallback


def outline(text: str, limit: int = 15) -> List[dict]:
    out: List[dict] = []
    masked, _ = fence_mask(text.split("\n"))
    for i, line in enumerate(text.splitlines()):
        if i in masked:
            continue
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if m and len(out) < limit:
            out.append({"level": len(m.group(1)), "title": m.group(2).strip()})
    return out


def count_links_images(text: str) -> dict:
    internal = external = 0
    base_host = ""
    for _, href, _ in _FULL_LINK_RE.findall(text):
        host = urlsplit(href).netloc
        if not host:
            internal += 1
        elif not base_host:
            base_host = host
            internal += 1
        elif host == base_host:
            internal += 1
        else:
            external += 1
    return {"internal": internal, "external": external,
            "images": len(_IMAGE_RE.findall(text))}
