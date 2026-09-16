"""Markdown post-processing — shared parser, source-aware cleanup, validation.

A single MarkdownIt factory is reused across all modes so fenced code,
indented code, tables, inline code, hard breaks, lists, blockquotes,
reference definitions, and HTML literals are parsed once and never
edited inside their protected ranges. Agent mode returns cleaned
source verbatim (no canonicalization); index mode canonicalizes only
eligible input with mdformat GFM (codeformatters disabled) and refuses
to reformat on confirmed table or fence defects so canonicalization
cannot corrupt the on-disk output.

Source-range edits are computed from markdown-it token ``map`` spans
(zero-based, end-exclusive line pairs) and applied to the *original*
source via character offsets: protected ranges are skipped entirely;
mutable ranges are collected once then applied descending so earlier
offsets stay valid. Inline tokens carry no ``.map``; their parent block
defines the protected extent. The tasklists plugin strips ``[x]``
markers from ``inline.content``, so no protection range may be derived
from the inline token's text length — only from the parent block's
line map.

rumdl is invoked as a subprocess (no shell) with a bounded timeout
and JSON output; its findings are mapped into :class:`Diagnostic`
records while suppression directives (rumdl-disable / markdownlint-
disable / prettier-ignore) are neutralized in a same-length validator
copy only, so downstream consumers keep the cleaned source unchanged.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import json
import logging
import os
import re
import shutil
import unicodedata
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Sequence

import markdown_it
from chonkie import MarkdownChef

from markdown_it.token import Token
from mdformat import text as mdformat_text

from kindly_web_search_mcp_server.content.models import (
    Diagnostic,
    MarkdownStructure,
    ProcessedMarkdown,
    QualityReport,
)

LOGGER = logging.getLogger(__name__)


# Rule identifiers rumdl is asked to enable. Contract fixed.
# Table/fence rules catch structural corruption; the link/image/heading
# rules catch scraped-site junk that survives extraction (empty links,
# alt-less images, inline HTML, broken emphasis, duplicate or multiple
# H1 headings from resolver envelopes). Severity defaults make
# MD024/MD025/MD042/MD045 error-class findings — exactly the
# functional-quality signals the score must react to.
RUMLDL_ENABLED_RULES: tuple[str, ...] = (
    "MD056",  # table column count
    "MD075",  # table row count
    "MD070",  # fence marker collision
    "MD031",  # fences-blank-lines (style; QA only)
    "MD058",  # blank-lines-around-tables (style; QA only)
    "MD024",  # duplicate headings (anchor collisions)
    "MD025",  # multiple H1 (broken document outline)
    "MD033",  # inline HTML leakage
    "MD037",  # spaces around emphasis markers
    "MD038",  # spaces inside code spans
    "MD036",  # emphasis used as heading
    "MD040",  # fenced code without language
    "MD042",  # empty links
    "MD045",  # images without alt text
    "MD057",  # relative links that do not resolve
    "MD059",  # non-descriptive link text
    "MD090",  # horizontal rule immediately before a heading
)

# Rule identifiers whose affected characters DO degrade the quality
# score. MD031/MD058 are stylistic and surface in flags/diagnostics
# but never count toward ``union_affected_chars``. The scraped-junk
# rules above all count: they mark content an LLM cannot use cleanly.
STYLE_RULES: frozenset[str] = frozenset({"MD031", "MD058"})

# rumdl rule codes marking scraped junk an LLM cannot use cleanly (inline
# HTML, emphasis-as-heading, empty links, alt-less images, non-descriptive
# link text). Counted as ``boilerplate_hits`` in the quality report.
_JUNK_RULES: frozenset[str] = frozenset({"MD033", "MD036", "MD042", "MD045", "MD059"})

# Rule identifiers that gate mdformat canonicalization in index mode.
# Confirmed defects only — mere fence presence does NOT block format.
INDEX_SKIP_RULES: frozenset[str] = frozenset({"MD056", "MD075", "MD070"})

# Subprocess budget for rumdl.
RUMLDL_TIMEOUT_SECONDS: float = 15.0
RUMLDL_MAX_OUTPUT_BYTES: int = 1 * 1024 * 1024
RUMLDL_REAP_SECONDS: float = 2.0

# Diagnostic phase labels.
_PHASE_VALIDATE = "validate"
_PHASE_FORMAT = "format"


# --- Shared markdown-it parser (cached singleton) ----------------------------


def _build_parser() -> markdown_it.MarkdownIt:
    """Construct the single shared parser reused for every mode.

    commonmark + table + strikethrough + linkify. ``tasklists`` plugin
    is optional: when ``mdit_py_plugins.tasklists`` is importable the
    parser gains GFM checkbox semantics; otherwise checkbox lines parse
    as plain bullet items and ``inline.content`` keeps the ``[x]`` text
    verbatim. ``breaks: False`` keeps GFM single-newline hard breaks
    disabled; backslash hard breaks are preserved because prose edits
    never touch the protected hardbreak/paragraph range.
    """
    parser = markdown_it.MarkdownIt(
        "commonmark",
        {"html": False, "breaks": False, "langPrefix": "language-"},
    )
    for rule in ("table", "strikethrough", "linkify"):
        try:
            parser.enable([rule])
        except ValueError:
            # Built-in rules are always available; any failure here is
            # treated as non-fatal so the processor still functions.
            LOGGER.debug("markdown-it rule %s not available", rule)
    try:
        from mdit_py_plugins.tasklists import tasklists_plugin

        parser.use(tasklists_plugin)
    except ImportError:
        LOGGER.debug("mdit_py_plugins.tasklists not available; checkboxes parse as text")
    return parser


_PARSER: markdown_it.MarkdownIt = _build_parser()

_MARKDOWN_CHEF = MarkdownChef(tokenizer="character")


# --- Source-range edit helpers ------------------------------------------------


# Suppression directives recognized in HTML comments. Captured verbatim
# so a same-length replacement is possible for the validator copy.
_SUPPRESSION_COMMENT_RE = re.compile(
    r"<!--\s*(?:"
    r"rumdl-(?:disable(?:-line|-next-line)?|enable|restore)"
    r"|markdownlint-(?:disable|enable|restore)(?:-line|-next-line)?"
    r"|prettier-ignore"
    r")\b[^<>]*?-->",
    re.IGNORECASE,
)

_FENCE_MARKER_RE = re.compile(r"^(`{3,}|~{3,})$")


def _line_starts(text: str) -> list[int]:
    """Return the start char offset of every line plus a final sentinel."""
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    return starts


def _as_line_range(source_map: list[int] | None) -> tuple[int, int] | None:
    """Validate a markdown-it source map as a 2-item line range."""
    if source_map is None or len(source_map) != 2:
        return None
    return (source_map[0], source_map[1])


def _line_range_to_chars(
    text: str,
    line_starts: list[int],
    line_range: Sequence[int],
) -> tuple[int, int]:
    """Convert zero-based end-exclusive ``[start, end]`` line range to char offsets.

    Out-of-range ends are clamped; the end offset trims trailing
    newlines so adjacent blocks are not silently joined when the
    range is deleted.
    """
    length = len(text)
    start_line, end_line = line_range
    start_char = line_starts[start_line] if 0 <= start_line < len(line_starts) else length
    end_char = line_starts[end_line] if 0 <= end_line < len(line_starts) else length
    if end_char > length:
        end_char = length
    while end_char > start_char and text[end_char - 1] == "\n":
        end_char -= 1
    return start_char, end_char


@dataclass(frozen=True, slots=True)
class _Edit:
    """A single ``(start, end)`` deletion expressed in char offsets of the source."""

    start: int
    end: int


def _overlaps_protected(protected: tuple[tuple[int, int], ...], start: int, end: int) -> bool:
    """True if ``[start, end)`` overlaps any protected range."""
    for span_start, span_end in protected:
        if start < span_end and span_start < end:
            return True
    return False


def _collect_edits(
    text: str,
    protected: tuple[tuple[int, int], ...],
) -> list[_Edit]:
    """Compute every protected-aware source-range deletion.

    Frozen view of the source — uses ``protected`` from one parse of
    post-envelope text and never re-parses while collecting. Inline
    tokens have no ``.map`` and are excluded from edit computation by
    the protected-set check; whole-block protection handles their
    preservation.
    """
    edits: list[_Edit] = []
    length = len(text)
    line_starts = _line_starts(text)
    tokens = _PARSER.parse(text)

    # 1. Empty headings — delete the entire line (heading_open through
    # heading_close) only when the inline content is empty. Empty
    # headings are detected as ``heading_open+inline+heading_close``
    # with a blank inline.
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.type != "heading_open" or token.map is None:
            index += 1
            continue
        # Find matching heading_close at the same level.
        close_index = None
        for scan in range(index + 1, len(tokens)):
            if tokens[scan].type == "heading_close" and tokens[scan].level == token.level:
                close_index = scan
                break
        if close_index is None:
            index += 1
            continue
        inline_text = ""
        for scan in range(index + 1, close_index):
            if tokens[scan].type == "inline":
                inline_text = tokens[scan].content.strip()
                break
        if inline_text:
            index = close_index + 1
            continue
        line_index = token.map[0]
        char_start = line_starts[line_index] if line_index < len(line_starts) else length
        next_offset = line_starts[line_index + 1] if line_index + 1 < len(line_starts) else length
        char_end = next_offset
        if not _overlaps_protected(protected, char_start, char_end):
            edits.append(_Edit(char_start, char_end))
        index = close_index + 1

    # 2. Suppression-comment lines whose entire visible text matches a
    # recognized directive. Outside protected regions only.
    lines = text.split("\n")
    for line_index, line in enumerate(lines):
        stripped = line.strip()
        if not (stripped.startswith("<!--") and stripped.endswith("-->")):
            continue
        if not _SUPPRESSION_COMMENT_RE.fullmatch(stripped):
            continue
        char_start = line_starts[line_index] if line_index < len(line_starts) else length
        next_offset = line_starts[line_index + 1] if line_index + 1 < len(line_starts) else length
        char_end = next_offset
        if not _overlaps_protected(protected, char_start, char_end):
            edits.append(_Edit(char_start, char_end))

    return edits


def _apply_descending_edits(text: str, edits: Iterable[_Edit]) -> str:
    """Apply non-overlapping char edits, sorted descending so earlier offsets stay valid.

    Overlapping edits collapse to the union of their ranges — the
    first surviving edit wins by descending sort order. Empty edits
    (``start == end``) are skipped.
    """
    unique = {(edit.start, edit.end) for edit in edits if edit.end > edit.start}
    if not unique:
        return text
    sorted_edits = sorted(unique, key=lambda pair: (pair[0], pair[1]), reverse=True)
    chars = list(text)
    for start, end in sorted_edits:
        del chars[start:end]
    return "".join(chars)


# --- Whitespace-in-link repair -------------------------------------------------


# Pretty-printed HTML converts to ``[\n  Anchor\n](url)`` — whitespace
# inside the bracket pair breaks the link when rendered and reads as
# junk to an LLM. The fix belongs before validation: trim the interior
# whitespace runs to a single space, then drop the space pair entirely.
_WS_LINK_RE = re.compile(r"\[\s+(?P<text>\S(?:.*?\S)?)\s+\]\(", re.DOTALL)


def _repair_whitespace_links(text: str) -> tuple[str, bool]:
    """Trim whitespace runs inside ``[ text ](`` link-text brackets.

    WebcrawlerAPI's production lesson: pretty-printed HTML puts the
    anchor's newlines inside the bracket pair, and every renderer then
    breaks on the link. Collapse the interior to the bare anchor text.
    Protected ranges are irrelevant here — this only rewrites the
    bracket interior of syntactically malformed links, which markdown-it
    itself treats as plain text.
    """
    repaired = _WS_LINK_RE.sub(lambda m: f"[{m.group(1)}](", text)
    return repaired, repaired != text


# --- Error-page gate -----------------------------------------------------------


# Content-shaped error pages (404 renders, bot walls) arrive with HTTP
# 200 and otherwise-plausible markdown. Selection must not accept them.
_ERROR_PAGE_TITLE_RE = re.compile(
    r"^#\s+(?:404\s*[-–]?\s*)?(?:page\s+)?(?:not\s+found|404)\b[^\n]{0,80}\n",
    re.IGNORECASE,
)
_ERROR_PAGE_BODY_RE = re.compile(
    r"^\s*(?:404|error|page\s+not\s+found)\b",
    re.IGNORECASE | re.MULTILINE,
)


def _looks_like_error_page(text: str) -> bool:
    """True when the document opens like a 404/error render."""
    if not text:
        return False
    head = text[:512]
    if _ERROR_PAGE_TITLE_RE.match(head):
        return True
    first_lines = [line.strip() for line in head.splitlines() if line.strip()][:3]
    return len(first_lines) <= 2 and all(_ERROR_PAGE_BODY_RE.match(line) for line in first_lines)


# --- Protected-set computation (one freeze) -----------------------------------


@dataclass(frozen=True, slots=True)
class _ProtectedSet:
    """Char spans that MUST NOT be edited, computed once from post-envelope text."""

    spans: tuple[tuple[int, int], ...]


def _table_block_bounds(tokens: list[Token], start: int) -> tuple[int, int]:
    """Return inclusive [open, close] token indices of the table block at ``start``."""
    depth = 0
    for index in range(start, len(tokens)):
        token = tokens[index]
        if token.type in {
            "table_open",
            "thead_open",
            "tbody_open",
            "tr_open",
            "td_open",
            "th_open",
        }:
            depth += 1
        elif token.type in {
            "table_close",
            "thead_close",
            "tbody_close",
            "tr_close",
            "td_close",
            "th_close",
        }:
            depth -= 1
            if depth == 0:
                return start, index
    return start, len(tokens) - 1


def _freeze_protected_set(
    text: str,
    additional_spans: Iterable[tuple[int, int]] = (),
) -> _ProtectedSet:
    """Compute every char span that must NOT be edited.

    Walks tokens once, expanding:

    * ``fence`` / ``code_block`` / ``html_block`` → their line range
    * ``table_open`` → outer table line range
    * Block containing any ``code_inline`` / ``html_inline`` child →
      that block's line range (so inline literals survive whole-line
      edits). Inline tokens have no ``.map``; only the parent block
      map is used here — never the inline token's content length.

    The set is frozen after this single computation so subsequent
    edits use one consistent view of the source.
    """
    line_starts = _line_starts(text)
    tokens = _PARSER.parse(text)
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.type in {"fence", "code_block", "html_block"}:
            fence_range = _as_line_range(token.map)
            if fence_range is not None:
                spans.append(_line_range_to_chars(text, line_starts, fence_range))
            index += 1
            continue
        if token.type == "table_open":
            block_start, block_end = _table_block_bounds(tokens, index)
            outer = tokens[block_start]
            outer_range = _as_line_range(outer.map)
            if outer_range is not None:
                spans.append(_line_range_to_chars(text, line_starts, outer_range))
            index = block_end + 1
            continue
        # Block with inline code/HTML children → protect whole block
        # using its line map. Inline content length is NEVER used.
        if token.map is not None and token.children:
            has_inline_literal = any(
                child.type in {"code_inline", "html_inline"} for child in token.children
            )
            if has_inline_literal:
                inline_range = _as_line_range(token.map)
                if inline_range is not None:
                    spans.append(_line_range_to_chars(text, line_starts, inline_range))
        index += 1
    spans.extend(additional_spans)

    uniq = sorted(set(spans))
    return _ProtectedSet(spans=tuple(uniq))


def _analyze_markdownchef(
    text: str,
) -> tuple[MarkdownStructure, tuple[tuple[int, int], ...]]:
    """Parse MarkdownChef structure and return its source spans."""
    document = _MARKDOWN_CHEF.parse(text)
    tables = tuple(getattr(document, "tables", ()) or ())
    code_blocks = tuple(getattr(document, "code", ()) or ())
    images = tuple(getattr(document, "images", ()) or ())
    text_regions = tuple(getattr(document, "chunks", ()) or ())
    spans: list[tuple[int, int]] = []
    for item in (*tables, *code_blocks, *images):
        start = getattr(item, "start_index", None)
        end = getattr(item, "end_index", None)
        if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text):
            spans.append((start, end))
    return (
        MarkdownStructure(
            tables=len(tables),
            code_blocks=len(code_blocks),
            images=len(images),
            text_regions=len(text_regions),
        ),
        tuple(spans),
    )


# --- Jina envelope strip ------------------------------------------------------


def _strip_jina_envelope(text: str) -> tuple[str, dict[str, str]]:
    """Strip a Jina frontmatter envelope, requiring an explicit ``url:`` field.

    The envelope is detected only when:

    * the file begins with ``---\\n`` (after BOM),
    * a closing ``---\\n`` (or EOF) appears later,
    * and the body contains a ``url:`` line.

    Producers whose envelope omits the URL field are NOT considered Jina
    envelopes; their ``---\\n`` fences are preserved verbatim.
    """
    if not text:
        return text, {}
    if text.startswith("\ufeff"):
        body_start = 1
        bom_prefix = "\ufeff"
    else:
        body_start = 0
        bom_prefix = ""
    if not text.startswith("---\n", body_start):
        return text, {}
    closing = re.search(r"(?m)^---\s*(?:\n|$)", text[body_start + 4 :])
    if closing is None:
        return text, {}
    body = text[body_start + 4 : body_start + 4 + closing.start()]
    fields: dict[str, str] = {}
    for line in body.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip().lower()
        value = value.strip().strip('"').strip("'")
        if key:
            fields[key] = value
    if not fields.get("url"):
        return text, {}
    after = text[body_start + 4 + closing.end() :]
    if after.startswith("\n"):
        after = after[1:]
    return bom_prefix + after, fields


# --- Adjacent-duplicate / blank-line cleanup ----------------------------------


def _collapse_adjacent_duplicates(text: str) -> tuple[str, bool]:
    """Collapse 2+ identical consecutive non-blank lines to a single occurrence.

    Multi-line paragraph duplicates are NOT collapsed unless the
    entire block is byte-equal to its predecessor (handled line-by-line
    via exact equality). Blank lines are not touched here.
    """
    lines = text.split("\n")
    result: list[str] = []
    collapsed = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            result.append(line)
            index += 1
            continue
        end = index + 1
        while end < len(lines) and lines[end] == line:
            end += 1
        result.append(line)
        if end - index >= 2:
            collapsed = True
        index = end
    if not collapsed:
        return text, False
    return "\n".join(result), True


def _collapse_excessive_blanks(text: str) -> tuple[str, bool]:
    """Cap blank-line runs at 2 to keep paragraph structure stable."""
    lines = text.split("\n")
    result: list[str] = []
    blanks = 0
    mutated = False
    for line in lines:
        if not line.strip():
            blanks += 1
            continue
        if blanks > 2:
            mutated = True
            result.extend([""] * 2)
        elif blanks:
            result.extend([""] * blanks)
        blanks = 0
        result.append(line)
    if blanks > 2:
        mutated = True
        result.extend([""] * 2)
    elif blanks:
        result.extend([""] * blanks)
    if not mutated:
        return text, False
    return "\n".join(result), True


# --- Fence-language inference and H1 dedup -------------------------------------

# Deterministic language signals, checked in order against the fence body.
# First match wins; unrecognized bodies stay untagged. The python import
# signal requires an end-of-line import clause so JavaScript
# ``import x from "y"`` lines fall through to the JavaScript rule.
_FENCE_LANGUAGE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "dockerfile",
        re.compile(
            r"(?m)^(?:\s*(?:FROM|RUN|COPY|ADD|ENTRYPOINT|CMD|WORKDIR|ENV)\s"
            r"|\s*ENV\s+[A-Za-z_][\w.]*=)"
        ),
    ),
    (
        "sql",
        re.compile(r"(?m)^(?:\s*(?:SELECT\b|INSERT\s+INTO\b|CREATE\s+TABLE\b|DELETE\s+FROM\b))"),
    ),
    (
        "python",
        re.compile(
            r"(?m)^(?:\s*(?:def\s+\w|class\s+\w|import\s+[\w,\s]+$|from\s+[\w.]+\s+import\b"
            r"|print\(|@(?:app|pytest|property|staticmethod|classmethod|dataclass)\b))"
        ),
    ),
    (
        "javascript",
        re.compile(
            r"(?m)^(?:\s*(?:const\s|let\s|var\s|function\s|import\s|export\s"
            r"|console\.log\(|document\.|window\.|async\s+function\b|=>))"
        ),
    ),
    (
        "bash",
        re.compile(
            r"(?m)^(?:\s*(?:\$\s|pip\s+install\b|npm\s+(?:install|i)\b|apt(?:-get)?\s+install\b"
            r"|brew\s+install\b|docker\s+(?:run|build|pull|exec)\b"
            r"|git\s+(?:clone|checkout|commit|push)\b|curl\s|wget\s|cd\s"
            r"|uv\s|poetry\s|make\s|python3?\s|export\s))"
        ),
    ),
    (
        "html",
        re.compile(r"\A\s*(?:<!DOCTYPE|<html\b|<div\b|<head\b|<body\b)", re.IGNORECASE),
    ),
)

_MAX_FENCE_INFERENCE_CHARS: int = 20_000


def _infer_language_from_body(body: str) -> str | None:
    """Return one inferred language tag for a fence body, or None."""
    candidate = body.strip()
    if not candidate:
        return None
    if candidate[0] in "{[":
        try:
            json.loads(candidate)
        except (ValueError, TypeError, RecursionError):
            pass
        else:
            return "json"
    for language, signal in _FENCE_LANGUAGE_RULES:
        if signal.search(candidate):
            return language
    return None


def _infer_fence_languages(text: str) -> tuple[str, bool]:
    """Tag language-less fenced code blocks with an inferred info string.

    Only the fence opener line is edited; the code body is never touched.
    Bodies over ``_MAX_FENCE_INFERENCE_CHARS`` characters and unrecognized
    bodies keep the bare marker.
    """
    if "```" not in text and "~~~" not in text:
        return text, False
    tokens = _PARSER.parse(text)
    line_starts = _line_starts(text)
    lines = text.split("\n")
    mutated = False
    for token in tokens:
        if token.type != "fence" or token.info.strip():
            continue
        fence_range = _as_line_range(token.map)
        if fence_range is None:
            continue
        start_line, end_line = fence_range
        body_start = line_starts[start_line + 1] if start_line + 1 < len(line_starts) else len(text)
        body_end = line_starts[end_line] if end_line < len(line_starts) else len(text)
        body = text[body_start:body_end]
        if not body or len(body) > _MAX_FENCE_INFERENCE_CHARS:
            continue
        opener = lines[start_line] if start_line < len(lines) else ""
        stripped = opener.strip()
        if not _FENCE_MARKER_RE.fullmatch(stripped):
            continue
        language = _infer_language_from_body(body)
        if language is None:
            continue
        indent = opener[: len(opener) - len(opener.lstrip())]
        lines[start_line] = f"{indent}{stripped}{language}"
        mutated = True
    if not mutated:
        return text, False
    return "\n".join(lines), True


def _dedupe_h1_headings(text: str) -> tuple[str, bool]:
    """Delete H1 headings that duplicate an earlier H1 title.

    Site templates often repeat the document title, sometimes with
    typographic quotes instead of ASCII ones. Later duplicates are removed
    with their heading lines; the first occurrence always survives.
    Comparison normalizes Unicode punctuation, whitespace, and case.
    """
    if "#" not in text and "=" not in text:
        return text, False
    tokens = _PARSER.parse(text)
    seen: set[str] = set()
    drop_lines: set[int] = set()
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or token.tag != "h1" or token.map is None:
            continue
        inline_text = ""
        if index + 1 < len(tokens) and tokens[index + 1].type == "inline":
            inline_text = tokens[index + 1].content
        normalized = unicodedata.normalize("NFKC", inline_text)
        for curly, ascii_char in (
            ("\u2019", "'"),
            ("\u2018", "'"),
            ("\u201c", '"'),
            ("\u201d", '"'),
        ):
            normalized = normalized.replace(curly, ascii_char)
        normalized = " ".join(normalized.split()).casefold()
        if not normalized:
            continue
        if normalized in seen:
            drop_lines.update(range(token.map[0], token.map[1]))
        else:
            seen.add(normalized)
    if not drop_lines:
        return text, False
    kept = (line for index, line in enumerate(text.split("\n")) if index not in drop_lines)
    return "\n".join(kept), True


# --- rumdl subprocess ---------------------------------------------------------


def _resolve_rumdl_argv() -> list[str] | None:
    """Return the rumdl argv or None when no executable is available.

    Order:

    1. Bare binary on PATH (``shutil.which``; installs via cargo/brew/winget).
    2. The PyPI wheel's compiled binary under ``rumdl-<ver>.data/scripts/``
       — the wheel ships the Rust binary there (maturin ``bindings = bin``),
       and ``python -m rumdl`` only resolves ``target/release`` inside a
       cargo checkout, so the wheel path is the reliable in-venv option.
    3. ``python -m rumdl`` via the importable module detection; works only
       for development checkouts built with cargo.
    4. None — caller emits ``validator_unavailable``.

    The no-shell guarantee holds because ``create_subprocess_exec``
    takes argv directly.
    """
    binary = shutil.which("rumdl") or shutil.which("rumdl.exe")
    if binary:
        return [binary]
    if importlib.util.find_spec("rumdl") is not None:
        wheel_binary = importlib.metadata.files("rumdl")
        for entry in wheel_binary or ():
            path = entry.locate()
            name = str(path.name).lower()
            if not path.is_file() or name not in {"rumdl", "rumdl.exe"}:
                continue
            return [str(path)]
        return [sys.executable, "-m", "rumdl"]
    return None


async def _run_rumdl(text: str) -> tuple[list[dict[str, Any]], Diagnostic | None]:
    """Invoke rumdl as a JSON-emitting subprocess.

    Returns ``(findings, operational_diagnostic_or_none)``:

    * exit 0 with empty stdout → ``([], None)`` (clean)
    * exit 1 with JSON array → ``(findings, None)`` (findings to map)
    * exit 2 or non-zero without findings → operational failure
    * binary missing / timeout / OS error → ``([], operational_diag)``

    The validator sees a suppression-neutralized copy so its findings
    reflect raw markdown rather than the comment-aware policy.
    """
    argv = _resolve_rumdl_argv()
    if argv is None:
        return [], Diagnostic(
            code="validator_unavailable",
            message="rumdl binary not on PATH; skipping structural linting",
            severity="warning",
            source="rumdl",
            phase=_PHASE_VALIDATE,
        )

    def _neutralize(src: str) -> str:
        def _replace(match: re.Match[str]) -> str:
            return " " * len(match.group(0))

        return _SUPPRESSION_COMMENT_RE.sub(_replace, src)

    neutralized = _neutralize(text)
    cmd = [
        *argv,
        "check",
        "--stdin",
        "--no-config",
        "--no-cache",
        "--output-format",
        "json",
        "--enable",
        ",".join(RUMLDL_ENABLED_RULES),
    ]
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except (FileNotFoundError, OSError, PermissionError) as exc:
        return [], Diagnostic(
            code="validator_unavailable",
            message=f"rumdl launch failed: {type(exc).__name__}: {exc}",
            severity="warning",
            source="rumdl",
            phase=_PHASE_VALIDATE,
        )

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            process.communicate(neutralized.encode("utf-8")),
            timeout=RUMLDL_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        try:
            process.kill()
        finally:
            try:
                await asyncio.wait_for(process.wait(), timeout=RUMLDL_REAP_SECONDS)
            except asyncio.TimeoutError:
                LOGGER.warning(
                    "rumdl process did not exit after kill within %ss", RUMLDL_REAP_SECONDS
                )
        return [], Diagnostic(
            code="validator_unavailable",
            message="rumdl exceeded its validator timeout",
            severity="warning",
            source="rumdl",
            phase=_PHASE_VALIDATE,
        )

    stdout = stdout_b[:RUMLDL_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    stderr = stderr_b[:RUMLDL_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")

    if process.returncode not in (0, 1):
        return [], Diagnostic(
            code="validator_unavailable",
            message=f"rumdl operational failure (exit={process.returncode}): {stderr.strip()[:300]}",
            severity="warning",
            source="rumdl",
            phase=_PHASE_VALIDATE,
        )

    payload = stdout.strip()
    if not payload:
        return [], None

    parsed = _parse_rumdl_payload(payload)
    if isinstance(parsed, Diagnostic):
        return [], parsed
    return parsed, None


def _parse_rumdl_payload(payload: str) -> list[dict[str, Any]] | Diagnostic:
    """Robust parser: JSON array, single wrapped object, or JSONL.

    * ``[...]`` → list
    * ``{"findings": [...]}`` / ``{"result": [...]}`` → unwrap
    * JSONL where each non-empty line is a finding object → list
    * any other shape → operational diagnostic
    """
    try:
        candidate = json.loads(payload)
    except json.JSONDecodeError:
        # JSONL fallback: each non-empty line is one finding object.
        findings: list[dict[str, Any]] = []
        for line in payload.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                return Diagnostic(
                    code="validator_unavailable",
                    message="rumdl returned non-JSON output",
                    severity="warning",
                    source="rumdl",
                    phase=_PHASE_VALIDATE,
                )
            if isinstance(entry, dict):
                findings.append(entry)
        return findings

    if isinstance(candidate, list):
        return [item for item in candidate if isinstance(item, dict)]
    if isinstance(candidate, dict):
        for key in ("findings", "result", "results", "violations"):
            value = candidate.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return Diagnostic(
            code="validator_unavailable",
            message="rumdl returned unexpected JSON shape",
            severity="warning",
            source="rumdl",
            phase=_PHASE_VALIDATE,
        )
    return Diagnostic(
        code="validator_unavailable",
        message="rumdl returned unexpected JSON shape",
        severity="warning",
        source="rumdl",
        phase=_PHASE_VALIDATE,
    )


def _diagnostic_from_rumdl(finding: Any) -> Diagnostic | None:
    """Translate one rumdl finding dict into a :class:`Diagnostic`."""
    if not isinstance(finding, dict):
        return None
    rule = str(finding.get("rule") or finding.get("rule_name") or "").strip()
    if not rule:
        return None
    message = str(finding.get("message") or finding.get("description") or rule).strip()
    line_info = finding.get("line") or finding.get("line_number")
    end_line_info = finding.get("end_line") or finding.get("line_end")
    start_line = int(line_info) if isinstance(line_info, (int, float)) and line_info else None
    end_line = (
        int(end_line_info) if isinstance(end_line_info, (int, float)) and end_line_info else None
    )
    severity_raw = str(finding.get("severity") or "warning").strip().lower()
    if severity_raw == "info":
        severity: Literal["info", "warning", "error"] = "info"
    elif severity_raw == "error":
        severity = "error"
    else:
        severity = "warning"
    return Diagnostic(
        code=rule,
        message=message,
        severity=severity,
        source="rumdl",
        start_line=start_line,
        end_line=end_line,
        phase=_PHASE_VALIDATE,
    )


# --- markdown-it structural checks --------------------------------------------


@dataclass(frozen=True, slots=True)
class _StructuralFindings:
    malformed_tables: int
    fence_errors: int
    affected_line_ranges: tuple[tuple[int, int], ...]
    flag_codes: tuple[str, ...]
    collision_line_ranges: tuple[tuple[int, int], ...]


def _split_table_cells(line: str) -> list[str]:
    """Split a raw GFM table row on unescaped pipes."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        stripped = stripped[:-1]
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in stripped:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append("".join(current))
            current = []
        else:
            current.append(char)
    cells.append("".join(current))
    return cells


def _source_table_row_counts(
    text: str, table_map: list[int] | None
) -> tuple[int, list[int]] | None:
    """Count raw source cells per table row inside a token table block."""
    if not table_map or len(table_map) != 2:
        return None
    lines = text.splitlines()
    start, end = max(0, table_map[0]), min(len(lines), table_map[1])
    rows = [line for line in lines[start:end] if "|" in line]
    if len(rows) < 2:
        return None
    counts = [len(_split_table_cells(row)) for row in rows]
    if any(count == 0 for count in counts):
        return None
    return counts[0], counts[1:]


def _check_structures(text: str) -> _StructuralFindings:
    """Detect malformed tables and fence collisions using markdown-it tokens.

    * Malformed table = a ``table_open`` whose header row contains a
      different cell count than at least one body row.
    * Fence collision = a closing fence marker whose character or
      length differs from its opener (MD070). EOF unclosed fences are
      NOT counted as fence errors — many code samples intentionally
      end without a closer.

    Affected line ranges are zero-based end-exclusive ``[start, end]``
    pairs and drive the integer line-union scoring pass.
    """
    tokens = _PARSER.parse(text)
    malformed_tables = 0
    fence_errors = 0
    affected_ranges: list[tuple[int, int]] = []
    collision_ranges: list[tuple[int, int]] = []
    flag_codes: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.type == "fence":
            opener_char = token.markup[0] if token.markup else ""
            opener_len = len(token.markup)
            scan = index + 1
            closed = False
            collision = False
            while scan < len(tokens):
                candidate = tokens[scan]
                if candidate.type == "fence":
                    close_match = _FENCE_MARKER_RE.match(candidate.markup)
                    if close_match:
                        close_char = close_match.group(1)[0]
                        close_len = len(close_match.group(1))
                        if (
                            close_char == opener_char
                            and close_len >= opener_len
                            and not candidate.info
                        ):
                            closed = True
                        else:
                            collision = True
                        break
                scan += 1
            if collision:
                collision_range = _as_line_range(token.map)
                if collision_range is not None:
                    collision_ranges.append(collision_range)
                fence_errors += 1
                flag_codes.append("MD070")
                flag_codes.append("fence-collision")
            if not closed and not collision:
                # EOF unclosed fence — not counted.
                pass
            index += 1
            continue
        if token.type == "table_open":
            block_start, block_end = _table_block_bounds(tokens, index)
            outer = tokens[block_start]
            row_counts: list[int] = []
            current_row = 0
            for inner in range(block_start, block_end + 1):
                row_token = tokens[inner]
                if row_token.type == "tr_open":
                    current_row = 0
                elif row_token.type in {"td_open", "th_open"}:
                    current_row += 1
                elif row_token.type in {"tr_close", "thead_close", "tbody_close"}:
                    if current_row:
                        row_counts.append(current_row)
                        current_row = 0
            if row_counts:
                head = row_counts[0]
                body = row_counts[1:]
                if body and any(count != head for count in body):
                    malformed_tables += 1
                    table_range = _as_line_range(outer.map)
                    if table_range is not None and table_range not in affected_ranges:
                        affected_ranges.append(table_range)
                    if any(count > head for count in body):
                        flag_codes.append("MD056")
                    else:
                        flag_codes.append("MD075")
            source_rows = _source_table_row_counts(text, outer.map)
            if source_rows is not None:
                head, body = source_rows
                if body and any(count != head for count in body):
                    source_range = _as_line_range(outer.map)
                    if source_range is not None and source_range not in affected_ranges:
                        affected_ranges.append(source_range)
                    if "malformed_table" not in flag_codes:
                        malformed_tables += 1
                    if any(count > head for count in body):
                        if "MD056" not in flag_codes:
                            flag_codes.append("MD056")
                    elif "MD075" not in flag_codes:
                        flag_codes.append("MD075")
                    if "malformed_table" not in flag_codes:
                        flag_codes.append("malformed_table")
            index = block_end + 1
            continue
        index += 1
    return _StructuralFindings(
        malformed_tables=malformed_tables,
        fence_errors=fence_errors,
        affected_line_ranges=tuple(affected_ranges),
        flag_codes=tuple(dict.fromkeys(flag_codes)),
        collision_line_ranges=tuple(collision_ranges),
    )


# --- mdformat canonicalization ------------------------------------------------


def _canonicalize_with_mdformat(text: str) -> tuple[str | None, Diagnostic | None]:
    """Run mdformat with the gfm extension and no code formatters.

    Returns ``(formatted_text, error_diagnostic_or_none)``:

    * On success: ``(formatted, None)``.
    * On dialect/parse failure: ``(None, diagnostic)``.

    ``wrap: keep`` ensures the formatter never re-flows paragraphs;
    ``codeformatters=()`` disables language-specific code reformatting
    so the validator copy and the cleaned source agree line-for-line.
    """
    try:
        formatted = mdformat_text(
            text,
            extensions=["gfm"],
            options={"wrap": "keep", "number": False, "end-of-line": "lf"},
            codeformatters=(),
        )
    except Exception as exc:  # noqa: BLE001 — mdformat raises ValueError on unsupported dialect
        return None, Diagnostic(
            code="mdformat_failed",
            message=f"mdformat refused to canonicalize: {type(exc).__name__}: {str(exc)[:200]}",
            severity="warning",
            source="mdformat",
            phase=_PHASE_FORMAT,
        )
    return formatted, None


# --- Quality computation ------------------------------------------------------


def _substantive_chars(text: str) -> int:
    """Non-whitespace character count of the post-envelope text."""
    return len(re.sub(r"\s+", "", text))


def _line_union_chars(
    text: str,
    line_ranges: Iterable[tuple[int, int]],
) -> int:
    """Sum ``len(line) + 1`` (trailing newline) over the union of line ranges.

    Documented approximation: each covered line contributes its byte
    length plus one (the trailing newline). The denominator
    ``substantive_chars`` is the non-whitespace count of the same
    source, so the ratio is approximate, not byte-precise.
    """
    if not text:
        return 0
    lines = text.split("\n")
    line_lengths = [len(line) for line in lines]
    used = 0
    seen: set[int] = set()
    for start_line, end_line in line_ranges:
        for line_index in range(max(start_line, 0), min(end_line, len(lines))):
            if line_index in seen:
                continue
            seen.add(line_index)
            used += line_lengths[line_index] + 1
    return used


def _score_from_affected(substantive_chars: int, affected_chars: int) -> float:
    """``1 - union_affected_chars / substantive_chars`` clamped to ``[0, 1]``.

    Empty input (``substantive_chars == 0``) maps to ``score=0.0``;
    downstream :class:`QualityReport` uses this to set
    ``accepted=False`` and ``flags=("empty_content",)``. Stylistic
    findings (MD031/MD058) are excluded from ``affected_chars`` so they
    never degrade the score.
    """
    if substantive_chars <= 0:
        return 0.0
    if affected_chars <= 0:
        return 1.0
    ratio = affected_chars / substantive_chars
    if ratio >= 1.0:
        return 0.0
    score = 1.0 - ratio
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


def _duplicate_line_ratio(text: str) -> float:
    """Fraction of duplicate-line occurrences (consecutive runs of 2+) over non-blank lines."""
    lines = [line for line in text.split("\n") if line.strip()]
    if not lines:
        return 0.0
    duplicate_count = 0
    index = 0
    while index < len(lines):
        end = index + 1
        while end < len(lines) and lines[end] == lines[index]:
            end += 1
        if end - index >= 2:
            duplicate_count += end - index - 1
        index = end
    return duplicate_count / len(lines)


# --- MarkdownProcessor --------------------------------------------------------


class MarkdownProcessor:
    """Single-source Markdown post-processor with QA-gated index formatting."""

    def __init__(self) -> None:
        self.parser: markdown_it.MarkdownIt = _PARSER

    async def process(self, raw_markdown: str, mode: str) -> ProcessedMarkdown:
        """Clean, validate, and (index only) canonicalize one Markdown body.

        * Agent mode: clean source ranges, drop empty headings, collapse
          duplicates and excessive blanks, run rumdl. Returns cleaned
          source verbatim; no mdformat round-trip. Diagnostics include
          rumdl findings plus structural-defect signals.
        * Index mode: same cleaning + rumdl validation; if eligible
          (no malformed tables, no MD056/MD075/MD070 confirmed defects),
          canonicalize with mdformat GFM (codeformatters disabled). On
          defect or mdformat refusal the cleaned source is the final
          output; the skip is recorded as a transform and diagnostic.
        """
        if mode not in {"agent", "index"}:
            raise ValueError(f"Unknown processing mode: {mode!r}")

        original = raw_markdown or ""
        transforms: list[str] = []
        diagnostics: list[Diagnostic] = []

        # 1. Strip known Jina envelope; the post-envelope text drives
        # every subsequent computation.
        cleaned, envelope_fields = _strip_jina_envelope(original)
        if envelope_fields.get("url"):
            transforms.append("stripped-jina-envelope")

        # 2. MarkdownChef audits the post-envelope source and contributes
        # structural ranges to the protected set without replacing the
        # markdown-it sanitizer or validator.
        structure = MarkdownStructure()
        markdownchef_spans: tuple[tuple[int, int], ...] = ()
        try:
            structure, markdownchef_spans = _analyze_markdownchef(cleaned)
        except Exception as exc:
            diagnostics.append(
                Diagnostic(
                    code="markdownchef_failed",
                    message=f"MarkdownChef structural audit failed: {type(exc).__name__}",
                    severity="warning",
                    source="markdownchef",
                    phase=_PHASE_VALIDATE,
                )
            )
        protected = _freeze_protected_set(cleaned, additional_spans=markdownchef_spans)
        protected_spans = protected.spans

        # 3. Source-range edits (descending, non-overlapping).
        edits = _collect_edits(cleaned, protected_spans)
        if edits:
            cleaned = _apply_descending_edits(cleaned, edits)
            transforms.append("removed-empty-headings")
            transforms.append("stripped-suppression-directives")

        # 4. Whitespace-in-link repair (pre-validation; the bracket
        # interior is never a protected range).
        cleaned, mutated = _repair_whitespace_links(cleaned)
        if mutated:
            transforms.append("repaired-whitespace-links")

        # 5. Adjacent-duplicate and excessive-blank passes.
        cleaned, mutated = _collapse_adjacent_duplicates(cleaned)
        if mutated:
            transforms.append("deduped-adjacent-blocks")
        cleaned, mutated = _collapse_excessive_blanks(cleaned)
        if mutated:
            transforms.append("collapsed-excessive-blanks")

        # 5b. Fence-language inference, then duplicate-H1 demotion. Both
        # run before validation: fence info strings and duplicate H1
        # heading lines are plain source lines, never protected content,
        # and the edits stay valid ahead of the protected-set freeze.
        cleaned, mutated = _infer_fence_languages(cleaned)
        if mutated:
            transforms.append("inferred-fence-languages")
        cleaned, mutated = _dedupe_h1_headings(cleaned)
        if mutated:
            transforms.append("deduped-h1-headings")

        # 6. Substantive char count is the post-envelope denominator;
        # it is held constant across pre/post format scoring.
        substantive = _substantive_chars(cleaned)

        # 7. Validator copy: suppression-neutralized, same-length.
        def _neutralize(src: str) -> str:
            def _replace(match: re.Match[str]) -> str:
                return " " * len(match.group(0))

            return _SUPPRESSION_COMMENT_RE.sub(_replace, src)

        validator_copy = _neutralize(cleaned)

        # 8. Error-page gate: a 404-shaped render arriving with HTTP 200
        # must not be accepted as usable content. The flag routes through
        # selection's blocking set, so a challenge/404 render loses to
        # any usable candidate.
        if _looks_like_error_page(cleaned):
            diagnostics.append(
                Diagnostic(
                    code="error_page",
                    message="Document opens like an error page (404/bot wall), not article content",
                    severity="error",
                    source="pipeline",
                    phase=_PHASE_VALIDATE,
                ),
            )

        # 9. Validation: rumdl + parser structural checks.
        rumdl_findings, validator_diag = await _run_rumdl(validator_copy)
        if validator_diag is not None:
            diagnostics.append(validator_diag)

        structural = _check_structures(cleaned)

        rumdl_records: list[Diagnostic] = []
        affected_line_ranges: list[tuple[int, int]] = []
        for finding in rumdl_findings:
            record = _diagnostic_from_rumdl(finding)
            if record is None:
                continue
            rumdl_records.append(record)
            if record.code not in STYLE_RULES:
                if (
                    record.start_line is not None
                    and record.end_line is not None
                    and record.end_line >= record.start_line
                ):
                    affected_line_ranges.append((record.start_line - 1, record.end_line))
                elif record.start_line is not None:
                    affected_line_ranges.append((record.start_line - 1, record.start_line))
        diagnostics.extend(rumdl_records)

        # Structural contributions to the affected union.
        affected_line_ranges.extend(structural.affected_line_ranges)
        affected_line_ranges.extend(structural.collision_line_ranges)

        affected_chars = _line_union_chars(cleaned, affected_line_ranges)
        pre_format_score = _score_from_affected(substantive, affected_chars)

        # 10. Index mode canonicalization, defect-gated.
        final_text = cleaned
        if mode == "index":
            skip_format = structural.malformed_tables > 0 or any(
                record.code in INDEX_SKIP_RULES for record in rumdl_records
            )
            if skip_format:
                diagnostics.append(
                    Diagnostic(
                        code="skip_mdformat",
                        message="Refused mdformat canonicalization due to confirmed table or fence defect",
                        severity="warning",
                        source="mdformat",
                        phase=_PHASE_FORMAT,
                    )
                )
                transforms.append("skipped-mdformat-canonicalization")
            else:
                formatted, format_error = _canonicalize_with_mdformat(cleaned)
                if formatted is None:
                    if format_error is not None:
                        diagnostics.append(format_error)
                    transforms.append("skipped-mdformat-canonicalization")
                else:
                    final_text = formatted
                    transforms.append("canonicalized-mdformat-gfm")
                    # Re-validate post-format output (rumdl only) using
                    # the post-canonicalize text directly so any
                    # formatter-induced change is reflected.
                    post_findings, post_diag = await _run_rumdl(final_text)
                    if post_diag is not None:
                        diagnostics.append(post_diag)
                    for finding in post_findings:
                        record = _diagnostic_from_rumdl(finding)
                        if record is not None:
                            diagnostics.append(record)

        # 11. Quality report on the returned text. When canonicalization
        # succeeded, recompute on the formatted text; otherwise the
        # pre-format score holds.
        if mode == "index" and final_text is not cleaned:
            post_structural = _check_structures(final_text)
            post_substantive = _substantive_chars(final_text)
            post_line_ranges: list[tuple[int, int]] = []
            for record in rumdl_records:
                if record.code in STYLE_RULES:
                    continue
                if (
                    record.start_line is not None
                    and record.end_line is not None
                    and record.end_line >= record.start_line
                ):
                    post_line_ranges.append((record.start_line - 1, record.end_line))
                elif record.start_line is not None:
                    post_line_ranges.append((record.start_line - 1, record.start_line))
            post_line_ranges.extend(post_structural.affected_line_ranges)
            post_line_ranges.extend(post_structural.collision_line_ranges)
            post_affected = _line_union_chars(final_text, post_line_ranges)
            post_score = _score_from_affected(post_substantive, post_affected)
            quality = _build_quality_report(
                text=final_text,
                substantive=post_substantive,
                score=post_score,
                structural=post_structural,
                rumdl_records=rumdl_records,
            )

        else:
            quality = _build_quality_report(
                text=final_text,
                substantive=substantive,
                score=pre_format_score,
                structural=structural,
                rumdl_records=rumdl_records,
            )
        return ProcessedMarkdown(
            markdown=final_text,
            quality=quality,
            diagnostics=tuple(diagnostics),
            transforms=tuple(transforms),
            structure=structure,
        )


def _build_quality_report(
    *,
    text: str,
    substantive: int,
    score: float,
    structural: _StructuralFindings,
    rumdl_records: list[Diagnostic],
) -> QualityReport:
    """Build the canonical :class:`QualityReport` for ``text``.

    ``accepted`` is True only when substantive text exists, the score
    clears the 0.85 bar, and no malformed tables or fence errors are
    present. Empty input sets ``accepted=False`` and prepends
    ``"empty_content"`` to flags.
    """
    rule_codes = {record.code for record in rumdl_records}
    structural_flags = list(structural.flag_codes)
    flags = tuple(dict.fromkeys(structural_flags + sorted(rule_codes)))
    word_count = len(re.findall(r"\S+", text))
    tokens = _PARSER.parse(text)
    heading_count = sum(1 for token in tokens if token.type == "heading_open")
    code_blocks = sum(1 for token in tokens if token.type in {"code_block", "fence"})
    tables = sum(1 for token in tokens if token.type == "table_open")
    accepted = (
        substantive > 0
        and score >= 0.85
        and structural.malformed_tables == 0
        and structural.fence_errors == 0
    )
    if substantive == 0 and "empty_content" not in flags:
        flags = ("empty_content",) + flags
    return QualityReport(
        accepted=accepted,
        score=score,
        word_count=word_count,
        heading_count=heading_count,
        code_blocks=code_blocks,
        tables=tables,
        duplicate_ratio=_duplicate_line_ratio(text),
        boilerplate_hits=sum(1 for record in rumdl_records if record.code in _JUNK_RULES),
        malformed_tables=structural.malformed_tables,
        fence_errors=structural.fence_errors,
        flags=flags,
    )
