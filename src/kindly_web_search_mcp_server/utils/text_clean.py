"""Text cleaning for agent queries and markdown hygiene for LLM-bound content.

Merged module (formerly ``heuristics/text_clean.py`` + the cleaning half of
``content/sanitize.py``):

- Query ingress: ``repair_unicode`` / ``clean_query`` — ftfy repair, fancy
  punctuation folding, zero-width stripping, whitespace collapse.
- LLM-bound light cleanup: ``clean_text_for_llm`` — same repairs plus
  whitespace collapse (no HTML re-extraction).
- Markdown hygiene: ``sanitize_markdown`` and its helpers — CommonMark
  fence/indent-aware unicode folding, image thinning, link repair,
  boilerplate/UI-chrome stripping, whitespace tidying, and Jina frontmatter
  parsing.
"""

from __future__ import annotations

import re

try:  # imported once at module load; per-call re-import cost 0.18ms/call
    import ftfy as _ftfy
except ImportError:  # pragma: no cover - ftfy is a hard dep in practice
    _ftfy = None

# Fancy punctuation → ASCII (agent/query ingress)
_FANCY_QUOTES = str.maketrans(
    {
        "\u2018": "'",  # ‘
        "\u2019": "'",  # ’
        "\u201a": "'",  # ‚
        "\u201b": "'",  # ‛
        "\u201c": '"',  # “
        "\u201d": '"',  # ”
        "\u201e": '"',  # „
        "\u201f": '"',  # ‟
        "\u2032": "'",  # ′
        "\u2033": '"',  # ″
        "\u00ab": '"',  # «
        "\u00bb": '"',  # »
        "\u2013": "-",  # –
        "\u2014": "-",  # —
        "\u2212": "-",  # −
        "\u2010": "-",  # ‑ (hyphen)
        "\u2011": "-",  # ‑ non-breaking hyphen
        "\u2012": "-",  # ‒ figure dash
        "\u2015": "-",  # ― horizontal bar
        "\u00ad": "",  # soft hyphen (drop)
        "\u2026": "...",  # …
    }
)

ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
_MULTI_WS = re.compile(r"[ \t]+")
_MULTI_BLANK_LINES = re.compile(r"\n{3,}")


def repair_unicode(text: str) -> str:
    """Fix mojibake when ftfy is installed; otherwise return text unchanged."""
    if not text or _ftfy is None:
        return text
    return _ftfy.fix_text(text)


def clean_query(text: str) -> str:
    """Agent/query ingress: unicode repair, strip, collapse ws, ASCII punctuation."""
    if not text:
        return ""
    cleaned = repair_unicode(text)
    cleaned = cleaned.translate(_FANCY_QUOTES)
    cleaned = ZERO_WIDTH.sub("", cleaned)
    return " ".join(cleaned.strip().split())


def clean_text_for_llm(text: str, role: str = "page") -> str:
    """Post-fetch / pre-LLM light cleanup. Does not re-extract HTML."""
    if not text:
        return ""
    cleaned = repair_unicode(text)
    cleaned = ZERO_WIDTH.sub("", cleaned)
    cleaned = _MULTI_WS.sub(" ", cleaned)
    cleaned = _MULTI_BLANK_LINES.sub("\n\n", cleaned)
    return cleaned.strip()


#
# --- Markdown cleaning (CommonMark fence/indent aware) ---
#

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(((?:[^()\s]|\([^()]*\))+)(?:\s+\"[^\"]*\")?\)")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(((?:[^()\s]|\([^()]*\))+)(?:\s+\"[^\"]*\")?\)")
_EMPTY_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(\s*\)")
_SELF_LINK_RE = re.compile(r"\[([^\]\[]+)\]\(\1\)")
_FRAGMENT_LINK_RE = re.compile(r"\[([^\]]+)\]\(#[^)]*\)")


_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*$")
_LIST_MARKER_RE = re.compile(r"^(?:[-*+]|\d{1,9}[.)])\s")
_CHROME_LINK_TEXT_RE = re.compile(
    r"^(?:permalink|§|¶|#|↩|🔗|anchor|top\s*of\s*page)$",
    re.IGNORECASE,
)

_GENERIC_ALTS = frozenset(
    {"image", "img", "logo", "icon", "avatar", "photo", "picture", "screenshot", "banner", "hero"}
)

_BOILERPLATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)^\s*share\s+(this|on\s+.*)\s*$"),
    re.compile(r"(?i)^\s*sign\s+up\s+for\s+(our\s+)?newsletter\s*$"),
    re.compile(r"(?i)^\s*related\s+(articles|posts|stories|videos)\s*$"),
    re.compile(r"(?i)^\s*leave\s+a\s+(comment|reply)\s*$"),
    re.compile(r"(?i)^\s*cookie\s+(settings|preferences|policy)\s*$"),
    re.compile(r"(?i)^\s*follow\s+us\s+on\s+.*\s*$"),
    re.compile(r"(?i)^\s*subscribe\s+(now|today)?\s*$"),
    re.compile(r"(?i)^\s*advertisement\s*$"),
    re.compile(r"(?i)^\s*\*{0,4}\s*hide caption\s*\*{0,4}\s*$"),
    re.compile(r"(?i)^\s*\*{0,4}\s*toggle caption\s*\*{0,4}\s*$"),
    re.compile(r"(?i)^\s*site search\s*$"),
    re.compile(r"(?i)^\s*more to explore\s*$"),
    re.compile(r"(?i)^\s*most watched\s*$"),
    re.compile(r"(?i)^\s*most read\s*$"),
    re.compile(r"(?i)^\s*also in news\s*$"),
    re.compile(r"^\s*[\*\-]\s*$"),
    re.compile(r"(?i)^\s*not yet fully loaded\s*$"),
)

# Line-anchored UI/navigation artifacts. Matched against link-unwrapped text,
# so both bare and [label](url) chrome wrappers are detected.
_UI_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)^skip to (?:main |page )?(?:content|search|navigation|nav)$"),
    re.compile(r"(?i)^jump to (?:content|main|nav(?:igation)?)$"),
    re.compile(r"(?i)^search(?: the docs| docs| this site)?\s*[.…!]*$"),
    re.compile(r"(?i)^(?:ctrl|cmd|\u2318)\s*\+?\s*k$"),
    re.compile(r"(?i)^copy (?:page|code|link|as markdown|citation)?$"),
    re.compile(
        r"(?i)^was this (?:page |article |doc(?:umentation)? )?helpful\??\s*(?:yes\s*/?\s*no|yesno)?$"
    ),
    re.compile(r"(?i)^powered by \S.{0,60}$"),
    re.compile(
        r"(?i)^(?:edit this page|improve this page|suggest (?:an? )?edits?|edit on (?:github|gitlab)"
        r"|view (?:source|page source|on github)|report (?:an? )?issue"
        r"|provide feedback|give feedback|feedback)$"
    ),
    re.compile(r"(?i)^(?:back|scroll) to top$"),
    re.compile(
        r"(?i)^(?:on this page|in this (?:article|section|guide)|table of contents|contents)$"
    ),
    re.compile(r"(?i)^(?:previous|prev)(?:\s+(?:page|post|article))?\s*(?:\u2192|\u203a|\u00bb)$"),
    re.compile(r"(?i)^(?:next)(?:\s+(?:page|post|article))?\s*(?:\u2192|\u203a|\u00bb)$"),
    re.compile(r"(?i)^(?:\u2190|\u2039|\u00ab)\s*next$"),
    re.compile(
        r"(?i)^(?:previous|prev)\s*(?:\||/|\u00b7|,)\s*next\s*(?:\u2192|\u203a|\u00bb)?\s*$"
    ),
    re.compile(
        r"(?i)^last (?:updated|modified|edited)\s*(?:on|:)?\s*"
        r"(?:\d{4}[-/]\d{2}|\d{1,2}\s+\w{3,9}|\w{3,9}\s+\d{1,2},?\s*\d{0,4}"
        r"|\d+\s+(?:day|week|month|hour|minute)s?\s+ago|today|yesterday).*$"
    ),
    re.compile(r"(?i)^(?:we|this (?:site|website)) (?:use|uses) cookies?[^…]*$"),
    re.compile(
        r"(?i)^(?:accept|allow|reject|decline|manage|customize)(?: all)? (?:cookies?|preferences|privacy)$"
    ),
    re.compile(r"(?i)^(?:toggle )?(?:main menu|navigation)$"),
    re.compile(r"^#{1,6}\s*$"),
    re.compile(r"^\s*\d{1,3}\.\s*$"),
    # Footer copyright lines (2026-09-09 exercise: MDN/simonwillison footers)
    re.compile(r"(?i)^portions of (?:this|the) content are ©.*$"),
    re.compile(r"^(?:\s*[*_>]*\s*)?©.*$"),
)
_BREADCRUMB_ROOT_RE = re.compile(
    r"(?i)^(?:home|start|docs|documentation|wiki|main|root|blog|learn|guides|reference|help|library)$"
)

# Prose-only unicode folding:
# typographic punctuation collapses to ASCII; arrows/bullets gain token savings.
# Never applied inside fenced/indented code.
PROSE_UNICODE_MAP = str.maketrans(
    {
        "\u00a0": " ",
        "\u2007": " ",
        "\u202f": " ",
        "\u2002": " ",
        "\u2003": " ",
        "\u2009": " ",
        "\u00ad": "",
        "\u2011": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u2032": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2033": '"',
        "\u00ab": '"',
        "\u00bb": '"',
        "\u2026": "...",
        "\u2192": "->",
        "\u2190": "<-",
        "\u2191": "^",
        "\u2193": "v",
        "\u2194": "<->",
        "\u21d2": "=>",
        "\u21d0": "<=",
        "\u21d4": "<=>",
        "\u2022": "-",
        "\u2023": "-",
        "\u25aa": "-",
        "\u25cf": "-",
        "\u00b7": "-",
        "\u2219": "-",
    }
)


def _line_indent(line: str) -> int:
    """Leading-space count; a leading tab counts as 4 (CommonMark approximation)."""
    tab_match = re.match(r"^\t+", line)
    if tab_match:
        return 4 * len(tab_match.group(0))
    return len(line) - len(line.lstrip(" "))


def _iter_code_aware_lines(text: str):
    """Yield ``(is_code, line)`` pairs with CommonMark fence/indent tracking.

    Fenced code (``` / ~~~) and indented code (4+ space lines after a blank
    line that do not continue a list item) are marked ``is_code=True`` and
    must be preserved verbatim by every pass.
    """
    fence_char: str | None = None
    fence_len = 0
    in_indented = False
    prev_blank = True
    prev_list = False

    for line in text.split("\n"):
        if fence_char is not None:
            close = _FENCE_CLOSE_RE.match(line)
            if close and close.group(1)[0] == fence_char and len(close.group(1)) >= fence_len:
                fence_char = None
                prev_blank, prev_list = False, False
            yield True, line
            continue

        open_match = _FENCE_OPEN_RE.match(line)
        if open_match:
            fence_char = open_match.group(1)[0]
            fence_len = len(open_match.group(1))
            prev_blank, prev_list = False, False
            yield True, line
            continue

        blank = not line.strip()
        if in_indented:
            if blank or _line_indent(line) >= 4:
                yield True, line
                continue
            in_indented = False
        elif _line_indent(line) >= 4 and prev_blank and not prev_list:
            in_indented = True
            yield True, line
            prev_blank = blank
            continue

        prev_blank = blank
        prev_list = bool(_LIST_MARKER_RE.match(line.strip()))
        yield False, line


def _looks_like_breadcrumb(line: str) -> bool:
    """Detect ``Home > Docs > API`` trails; conservative by design.

    Requires a known root word as the first segment, three or more short
    segments, no URLs, and no table pipes.
    """
    stripped = line.strip()
    if not stripped or len(stripped) > 100 or "|" in stripped or "://" in stripped:
        return False
    segments = [seg.strip() for seg in re.split(r"\s*[>\u00bb/]+\s*", stripped) if seg.strip()]
    if len(segments) < 3:
        return False
    if not _BREADCRUMB_ROOT_RE.match(segments[0]):
        return False
    return all(len(seg.split()) <= 4 for seg in segments)


def _collapse_duplicate_lines(lines: list[str]) -> list[str]:
    collapsed: list[str] = []
    index = 0
    while index < len(lines):
        end = index + 1
        while end < len(lines) and lines[end] == lines[index]:
            end += 1
        run = end - index
        if run >= 3:
            collapsed.append(lines[index])
        else:
            collapsed.extend(lines[index:end])
        index = end
    return collapsed


def _is_nav_link_row(line: str) -> bool:
    """Detect navigation rows: 3+ markdown links with almost no prose.

    Matches chrome like ``[Docs](/d) [Blog](/b) [API](/a)``; sentences that
    merely contain three inline links keep their connecting words and survive.
    """
    if len(line) > 200:
        return False
    links = _MD_LINK_RE.findall(line)
    if len(links) < 3:
        return False
    return len(_MD_LINK_RE.sub(" ", line).split()) <= 3


def _is_boilerplate_line(line: str) -> bool:
    probe = _MD_LINK_RE.sub(r"\1", line).strip()
    if any(pattern.match(probe) for pattern in _UI_LINE_PATTERNS):
        return True
    if any(pattern.match(line.strip()) for pattern in _BOILERPLATE_PATTERNS):
        return True
    return _looks_like_breadcrumb(probe) or _is_nav_link_row(line)


def strip_boilerplate(markdown: str) -> str:
    """Remove UI artifacts, boilerplate, breadcrumbs, and empty headings.

    Code-safe: fenced/indented code lines are never removed.
    """
    if not markdown:
        return markdown
    kept = [
        line
        for is_code, line in _iter_code_aware_lines(markdown)
        if is_code or not _is_boilerplate_line(line)
    ]
    return "\n".join(_collapse_duplicate_lines(kept))


def _repair_links_line(line: str) -> str:
    """Apply image-thinning + link-repair substitutions to ONE prose line."""
    if "](" not in line:
        return line
    line = _MD_IMAGE_RE.sub(
        lambda m: (
            ""
            if m.group(2).strip().startswith("data:")
            else (
                m.group(1).strip()
                if m.group(1).strip()
                and m.group(1).strip().lower() not in _GENERIC_ALTS
                and len(m.group(1).strip()) > 2
                else ""
            )
        ),
        line,
    )
    line = _EMPTY_MD_LINK_RE.sub(r"\1", line)
    line = _SELF_LINK_RE.sub(r"\1", line)
    line = _FRAGMENT_LINK_RE.sub(r"\1", line)
    line = _MD_LINK_RE.sub(
        lambda m: "" if _CHROME_LINK_TEXT_RE.match(m.group(1).strip()) else m.group(0),
        line,
    )
    return line


def _repair_links_prose_only(text: str) -> str:
    """Fence-aware link/image repair (H15).

    The four substitutions below rewrite markdown-link-shaped text. Applied
    document-wide they corrupted code examples (verified: a bash fence with
    ``echo 'see [docs](#anchor)'`` lost its link syntax). Fence/indent-aware
    segmentation keeps code verbatim.
    """
    if "](" not in text:
        return text
    return "\n".join(
        line if is_code else _repair_links_line(line)
        for is_code, line in _iter_code_aware_lines(text)
    )


def _fold_prose_unicode(text: str) -> str:
    """Collapse typographic unicode to ASCII outside code segments."""
    if text.isascii():
        return text
    return "\n".join(
        line if is_code else line.translate(PROSE_UNICODE_MAP)
        for is_code, line in _iter_code_aware_lines(text)
    )


def _tidy_whitespace(text: str) -> str:
    """Fence-aware whitespace normalization.

    Prose lines collapse interior space runs and trailing whitespace while
    keeping leading indentation (nested lists, quotes, tables). Code lines
    pass through verbatim. 3+ blank lines collapse to 2.
    """
    result: list[str] = []
    pending_blanks = 0
    for is_code, line in _iter_code_aware_lines(text):
        if is_code:
            if pending_blanks:
                result.extend([""] * min(pending_blanks, 2))
                pending_blanks = 0
            result.append(line)
            continue
        if not line.strip():
            pending_blanks += 1
            continue
        if pending_blanks:
            result.extend([""] * min(pending_blanks, 2))
            pending_blanks = 0
        match = re.match(r"^([ \t>]*)(.*)$", line)
        assert match is not None
        body = re.sub(r"[ \t]{2,}", " ", match.group(2)).rstrip()
        result.append(match.group(1) + body)
    if pending_blanks:
        result.extend([""] * min(pending_blanks, 2))
    return "\n".join(result)


def polish_prose(text: str) -> str:
    """Prose-only half of the markdown sanitizer, without boilerplate stripping.
    Applies unicode repair, zero-width strip, fence-aware link/image repair,
    typographic folding, and whitespace tidying, ending with a full-document
    strip. Leaves line composition untouched so callers that classify on the
    raw body can polish after classification without a second boilerplate
    pass (Jina, Crawl4AI cloud rungs).
    """
    if not text:
        return text
    cleaned = repair_unicode(text)
    cleaned = ZERO_WIDTH.sub("", cleaned)
    cleaned = _repair_links_prose_only(cleaned)
    cleaned = _fold_prose_unicode(cleaned)
    cleaned = _tidy_whitespace(cleaned)
    return cleaned.strip()


def sanitize_markdown(markdown: str) -> str:
    """Clean markdown for LLM consumption without re-extracting HTML.

    Code-aware: fenced/indented code keeps its whitespace verbatim; prose is
    unicode-folded, image-thinned, link-repaired, boilerplate-stripped, and
    whitespace-tidied.
    """
    if not markdown:
        return markdown
    return strip_boilerplate(polish_prose(markdown))


#
# --- Jina frontmatter envelope ---
#

# Envelope body captured as a group (H14): the old ``[3:-5]`` slice assumed a
# trailing newline inside the match; the Jina client strips trailing
# whitespace, so EOF envelopes truncated the LAST field's final char
# (verified: url value "https://example.com" -> "https://example.co").
_JINA_FRONTMATTER_RE = re.compile(r"(?s)^---\n(?P<body>.*?\n)---(?:\n|$)")


def strip_jina_frontmatter(text: str) -> str:
    """Drop a Jina frontmatter envelope (identified by its ``url:`` field)."""
    if not text:
        return text
    match = _JINA_FRONTMATTER_RE.match(text)
    if match is None:
        return text
    return text[match.end() :]


def parse_jina_frontmatter(text: str) -> dict[str, str]:
    """Return the Jina envelope fields (title/url/warning/...) when present."""
    if not text:
        return {}
    match = _JINA_FRONTMATTER_RE.match(text)
    if match is None:
        return {}
    fields: dict[str, str] = {}
    for line in match.group("body").splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            fields[key.strip().lower()] = value.strip().strip('"').strip("'")
    return fields
