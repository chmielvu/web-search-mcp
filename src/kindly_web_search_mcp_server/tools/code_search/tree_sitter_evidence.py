"""Tree-sitter-backed source evidence extraction for code search.

The classifier is source-first: it reports AST evidence only for complete source
that can be parsed with a locally cached grammar. Provider snippets and missing
grammars fail open with an explicit status instead of being presented as
syntax-aware evidence.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any, Literal, cast


AstRole = Literal["definition", "callsite", "import", "structure"]
AstStatus = Literal[
    "ok",
    "empty",
    "unsupported_language",
    "grammar_not_cached",
    "parser_unavailable",
    "source_too_large",
    "parse_error",
]

AST_QUERY_VERSION = "code-search-ast-v1"
_DEFAULT_MAX_SOURCE_BYTES = 1_000_000
_DEFAULT_MAX_EVIDENCE = 128

_LANGUAGE_ALIASES: dict[str, str] = {
    "bash": "bash",
    "cjs": "javascript",
    "go": "go",
    "html": "html",
    "htm": "html",
    "java": "java",
    "javascript": "javascript",
    "js": "javascript",
    "jsx": "javascript",
    "mjs": "javascript",
    "mts": "typescript",
    "cts": "typescript",
    "py": "python",
    "python": "python",
    "pyi": "python",
    "rs": "rust",
    "rust": "rust",
    "shell": "bash",
    "sh": "bash",
    "sql": "sql",
    "ts": "typescript",
    "tsx": "typescript",
    "typescript": "typescript",
}

_EXTENSION_LANGUAGES: dict[str, str] = {
    ".bash": "bash",
    ".cjs": "javascript",
    ".cts": "typescript",
    ".go": "go",
    ".htm": "html",
    ".html": "html",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".mts": "typescript",
    ".py": "python",
    ".pyi": "python",
    ".rs": "rust",
    ".sh": "bash",
    ".sql": "sql",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".zsh": "bash",
}

# These grammar-level node names are intentionally narrow. Unknown nodes stay
# parseable but are not promoted to ranking evidence until tested explicitly.
_DEFINITION_NODES: dict[str, frozenset[str]] = {
    "bash": frozenset({"function_definition"}),
    "go": frozenset({"function_declaration", "method_declaration", "type_declaration"}),
    "java": frozenset(
        {"class_declaration", "interface_declaration", "enum_declaration", "method_declaration"}
    ),
    "javascript": frozenset({"class_declaration", "function_declaration", "method_definition"}),
    "python": frozenset({"class_definition", "function_definition"}),
    "rust": frozenset(
        {"function_item", "struct_item", "enum_item", "trait_item", "impl_item", "mod_item"}
    ),
    "sql": frozenset({"create_table", "create_view", "create_function", "create_procedure"}),
    "typescript": frozenset(
        {
            "class_declaration",
            "function_declaration",
            "method_definition",
            "interface_declaration",
            "type_alias_declaration",
            "enum_declaration",
        }
    ),
}

_CALL_NODES: dict[str, frozenset[str]] = {
    "bash": frozenset({"command"}),
    "go": frozenset({"call_expression"}),
    "java": frozenset({"method_invocation", "object_creation_expression"}),
    "javascript": frozenset({"call_expression", "new_expression"}),
    "python": frozenset({"call"}),
    "rust": frozenset({"call_expression", "macro_invocation"}),
    "sql": frozenset({"invocation"}),
    "typescript": frozenset({"call_expression", "new_expression"}),
}

_IMPORT_NODES: dict[str, frozenset[str]] = {
    "go": frozenset({"import_declaration"}),
    "java": frozenset({"import_declaration"}),
    "javascript": frozenset({"import_statement", "export_statement"}),
    "python": frozenset({"import_statement", "import_from_statement"}),
    "rust": frozenset({"use_declaration"}),
    "typescript": frozenset({"import_statement", "export_statement"}),
}

_STRUCTURE_NODES: dict[str, frozenset[str]] = {
    "html": frozenset({"element", "script_element", "style_element"}),
}

_NAME_FIELDS = ("name", "function", "declarator", "object", "type", "source")


@dataclass(frozen=True, slots=True)
class AstEvidence:
    """One syntax-aware evidence span using one-based source lines."""

    role: AstRole
    kind: str
    name: str | None
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int
    start_column: int
    end_column: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AstClassification:
    """Bounded parser result suitable for CodeSearchHit.source_metadata."""

    status: AstStatus
    language: str | None
    parser_version: str | None
    source_bytes: int
    evidence: tuple[AstEvidence, ...] = ()
    error: str | None = None
    source_digest: str | None = None

    @property
    def evidence_count(self) -> int:
        return len(self.evidence)

    def as_metadata(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "language": self.language,
            "parser_version": self.parser_version,
            "query_version": AST_QUERY_VERSION,
            "source_digest": self.source_digest,
            "source_bytes": self.source_bytes,
            "evidence": [item.as_dict() for item in self.evidence],
            **({"error": self.error} if self.error else {}),
        }


def canonical_language(language: str | None) -> str | None:
    """Normalize provider/path language names to language-pack names."""
    if not language:
        return None
    value = language.strip().casefold().lstrip(".")
    supported = set(_DEFINITION_NODES) | {"html"}
    return _LANGUAGE_ALIASES.get(value, value if value in supported else None)


def language_for_path(path: str | None) -> str | None:
    """Infer a supported parser language from a repository-relative path."""
    if not path or "." not in path.rsplit("/", 1)[-1]:
        return None
    suffix = "." + path.rsplit(".", 1)[-1].casefold()
    return _EXTENSION_LANGUAGES.get(suffix)


def required_languages() -> tuple[str, ...]:
    """Return the approved grammar prefetch set in stable order."""
    return ("bash", "go", "html", "java", "javascript", "python", "rust", "sql", "typescript")


def _max_source_bytes() -> int:
    raw = os.environ.get("TREE_SITTER_MAX_SOURCE_BYTES", str(_DEFAULT_MAX_SOURCE_BYTES))
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_MAX_SOURCE_BYTES


def _max_evidence() -> int:
    raw = os.environ.get("TREE_SITTER_MAX_EVIDENCE", str(_DEFAULT_MAX_EVIDENCE))
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_MAX_EVIDENCE


def _parser_version() -> str | None:
    try:
        import tree_sitter

        return str(getattr(tree_sitter, "__version__", "")) or None
    except ImportError:
        return None


def _cached_languages() -> set[str] | None:
    try:
        import tree_sitter_language_pack as language_pack

        downloaded = getattr(language_pack, "downloaded_languages", None)
        if not callable(downloaded):
            return None
        values = cast(Iterable[str], downloaded())
        return {canonical_language(item) or item.casefold() for item in values}
    except (ImportError, OSError, RuntimeError):
        return None


def _get_parser(language: str) -> tuple[Any | None, AstStatus, str | None]:
    cached = _cached_languages()
    if cached is None:
        return None, "parser_unavailable", "tree-sitter-language-pack is unavailable"
    if language not in cached:
        return None, "grammar_not_cached", f"grammar is not prefetched: {language}"
    try:
        from tree_sitter_language_pack import get_parser

        return get_parser(language), "ok", None  # type: ignore[arg-type]
    except Exception as exc:  # pragma: no cover - native loader varies by platform
        return None, "parser_unavailable", f"parser load failed: {type(exc).__name__}"


def _node_text(node: Any, source: bytes) -> str:
    try:
        value = source[node.start_byte : node.end_byte]
        return value.decode("utf-8", errors="replace").strip()[:240]
    except (AttributeError, TypeError, UnicodeError):
        return ""


def _field_text(node: Any, source: bytes) -> str | None:
    for field in _NAME_FIELDS:
        try:
            child = node.child_by_field_name(field)
        except (AttributeError, TypeError):
            child = None
        if child is not None:
            value = _node_text(child, source)
            if value:
                return value
    return None


def _node_lines(node: Any, source_line_start: int) -> tuple[int, int, int, int]:
    start_point = getattr(node, "start_point", (0, 0))
    end_point = getattr(node, "end_point", start_point)
    start_line = int(start_point[0]) + source_line_start
    end_line = int(end_point[0]) + source_line_start
    return start_line, max(start_line, end_line), int(start_point[1]), int(end_point[1])


def _overlaps_match(
    node: Any,
    *,
    source_line_start: int,
    match_line_start: int | None,
    match_line_end: int | None,
) -> bool:
    if match_line_start is None:
        return True
    line_range = _node_lines(node, source_line_start)
    start_line, end_line = line_range[0], line_range[1]
    target_end = match_line_end or match_line_start
    return start_line <= target_end and end_line >= match_line_start


def _role_for_node(language: str, node_type: str, node: Any, source: bytes) -> AstRole | None:
    if language == "bash" and node_type == "command":
        command_text = _node_text(node, source).lstrip()
        return "import" if command_text.startswith(("source ", ". ")) else "callsite"
    if node_type in _IMPORT_NODES.get(language, frozenset()):
        return "import"
    if node_type in _DEFINITION_NODES.get(language, frozenset()):
        return "definition"
    if node_type in _CALL_NODES.get(language, frozenset()):
        return "callsite"
    if node_type in _STRUCTURE_NODES.get(language, frozenset()):
        return "structure"
    return None


def _source_digest(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()[:16]


def classify_source(
    source: str | bytes,
    *,
    language: str | None = None,
    path: str | None = None,
    source_line_start: int = 1,
    match_line_start: int | None = None,
    match_line_end: int | None = None,
    max_source_bytes: int | None = None,
) -> AstClassification:
    """Extract AST evidence overlapping a provider match span.

    ``source_line_start`` is one-based and identifies the first line represented
    by ``source``. ``match_line_*`` are absolute one-based lines when known.
    """
    resolved_language = canonical_language(language) or language_for_path(path)
    if resolved_language is None:
        return AstClassification("unsupported_language", None, _parser_version(), 0)
    source_bytes = source.encode("utf-8") if isinstance(source, str) else bytes(source)
    if not source_bytes:
        return AstClassification("empty", resolved_language, _parser_version(), 0)
    byte_limit = max_source_bytes if max_source_bytes is not None else _max_source_bytes()
    if len(source_bytes) > byte_limit:
        return AstClassification(
            "source_too_large", resolved_language, _parser_version(), len(source_bytes)
        )

    parser, status, error = _get_parser(resolved_language)
    if parser is None:
        return AstClassification(status, resolved_language, _parser_version(), len(source_bytes), error=error)
    try:
        timeout_micros = int(float(os.environ.get("TREE_SITTER_PARSE_TIMEOUT_MS", "100")) * 1000)
        parser.timeout_micros = max(1, timeout_micros)
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        tree = parser.parse(source_bytes)
        root = tree.root_node
    except Exception as exc:  # pragma: no cover - native parser failures vary by grammar
        return AstClassification(
            "parse_error",
            resolved_language,
            _parser_version(),
            len(source_bytes),
            error=f"parse failed: {type(exc).__name__}",
        )

    evidence: list[AstEvidence] = []

    def visit(node: Any) -> None:
        role = _role_for_node(
            resolved_language,
            str(getattr(node, "type", "")),
            node,
            source_bytes,
        )
        if role and _overlaps_match(
            node,
            source_line_start=source_line_start,
            match_line_start=match_line_start,
            match_line_end=match_line_end,
        ):
            start_line, end_line, start_column, end_column = _node_lines(node, source_line_start)
            evidence.append(
                AstEvidence(
                    role=role,
                    kind=str(node.type),
                    name=_field_text(node, source_bytes),
                    start_byte=int(node.start_byte),
                    end_byte=int(node.end_byte),
                    start_line=start_line,
                    end_line=end_line,
                    start_column=start_column,
                    end_column=end_column,
                )
            )
        if len(evidence) >= _max_evidence():
            return
        for child in getattr(node, "children", ()):
            visit(child)
            if len(evidence) >= _max_evidence():
                return

    visit(root)
    return AstClassification(
        status="ok",
        language=resolved_language,
        parser_version=_parser_version(),
        source_bytes=len(source_bytes),
        evidence=tuple(evidence),
        source_digest=_source_digest(source_bytes),
    )


def prefetch_required_languages(languages: Iterable[str] | None = None) -> tuple[str, ...]:
    """Download approved grammars for deployment/bootstrap, never search runtime."""
    selected = tuple(
        dict.fromkeys(
            canonical_language(item) or item
            for item in (languages or required_languages())
        )
    )
    import tree_sitter_language_pack as language_pack

    prefetch = getattr(language_pack, "prefetch", None)
    if not callable(prefetch):
        raise RuntimeError("tree-sitter-language-pack does not expose prefetch()")
    prefetch(list(selected))
    return selected


__all__ = [
    "AST_QUERY_VERSION",
    "AstClassification",
    "AstEvidence",
    "AstRole",
    "AstStatus",
    "canonical_language",
    "classify_source",
    "language_for_path",
    "prefetch_required_languages",
    "required_languages",
]
