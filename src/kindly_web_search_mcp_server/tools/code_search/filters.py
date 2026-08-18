"""Provider-neutral scope validation for code-search evidence."""

from __future__ import annotations

import fnmatch
import re
from collections import Counter
from typing import Iterable

from .models import CodeSearchHit, CodeSearchRequest, Diagnostic
from .query import QueryPlan

_LANGUAGE_ALIASES = {
    "c#": "csharp",
    "c++": "cpp",
    "golang": "go",
    "js": "javascript",
    "py": "python",
    "rs": "rust",
    "sh": "shell",
    "ts": "typescript",
}

_LANGUAGE_EXTENSIONS: dict[str, frozenset[str]] = {
    "c": frozenset({".c", ".h"}),
    "csharp": frozenset({".cs"}),
    "cpp": frozenset({".cc", ".cpp", ".cxx", ".h", ".hpp"}),
    "dart": frozenset({".dart"}),
    "go": frozenset({".go"}),
    "java": frozenset({".java"}),
    "javascript": frozenset({".cjs", ".js", ".mjs", ".jsx"}),
    "kotlin": frozenset({".kt", ".kts"}),
    "lua": frozenset({".lua"}),
    "objective-c": frozenset({".h", ".m", ".mm"}),
    "php": frozenset({".php"}),
    "powershell": frozenset({".ps1", ".psd1", ".psm1"}),
    "python": frozenset({".py", ".pyi"}),
    "r": frozenset({".r", ".R"}),
    "ruby": frozenset({".rb", ".rake", ".gemspec"}),
    "rust": frozenset({".rs"}),
    "scala": frozenset({".scala", ".sc"}),
    "shell": frozenset({".bash", ".sh", ".zsh"}),
    "swift": frozenset({".swift"}),
    "typescript": frozenset({".ts", ".tsx", ".mts", ".cts"}),
}

_GLOB_CHARS = set("*?[")


def _clean(value: str) -> str:
    return value.strip().strip('"').strip("'").strip()


def _qualifier_values(plan: QueryPlan, key: str) -> tuple[str, ...]:
    return tuple(
        value for qualifier, value in plan.qualifiers if qualifier == key and _clean(value)
    )


def _effective_scopes(plan: QueryPlan, request: CodeSearchRequest) -> dict[str, tuple[str, ...]]:
    return {
        "repositories": tuple(request.repositories) or _qualifier_values(plan, "repo"),
        "languages": (request.language,)
        if request.language
        else _qualifier_values(plan, "language"),
        "paths": (request.path,) if request.path else _qualifier_values(plan, "path"),
        "filenames": (request.filename,)
        if request.filename
        else _qualifier_values(plan, "filename"),
        "extensions": (request.extension,)
        if request.extension
        else _qualifier_values(plan, "extension"),
    }


def _normalized_path(value: str | None) -> str:
    return (value or "").replace("\\", "/").lstrip("/")


def _path_matches(path: str, pattern: str) -> bool:
    normalized_path = _normalized_path(path)
    normalized_pattern = _normalized_path(_clean(pattern))
    if not normalized_pattern:
        return True
    if any(char in normalized_pattern for char in _GLOB_CHARS):
        return fnmatch.fnmatchcase(normalized_path, normalized_pattern)
    return normalized_path == normalized_pattern or normalized_path.startswith(
        normalized_pattern.rstrip("/") + "/"
    )


def _extension_matches(path: str, extension: str) -> bool:
    expected = _clean(extension).casefold()
    if not expected:
        return True
    if not expected.startswith("."):
        expected = "." + expected
    return path.casefold().endswith(expected)


def _language_key(value: str) -> str:
    normalized = _clean(value).casefold()
    return _LANGUAGE_ALIASES.get(normalized, normalized)


def _language_matches(hit: CodeSearchHit, language: str) -> bool | None:
    expected = _language_key(language)
    metadata_language = hit.source_metadata.get("language")
    if isinstance(metadata_language, str) and metadata_language.strip():
        if _language_key(metadata_language) != expected:
            return False
    path = _normalized_path(hit.path)
    if not path:
        return None
    allowed_extensions = _LANGUAGE_EXTENSIONS.get(expected)
    if not allowed_extensions:
        return True
    suffix = re.search(r"\.[^./]+$", path)
    if suffix is None:
        return None
    return suffix.group(0).casefold() in {item.casefold() for item in allowed_extensions}


def _rejection_reason(hit: CodeSearchHit, scopes: dict[str, tuple[str, ...]]) -> str | None:
    is_exact_code = hit.result_kind == "code_match"
    repository = _clean(hit.repository or "")
    path = _normalized_path(hit.path)

    repositories = {_clean(value).casefold() for value in scopes["repositories"] if _clean(value)}
    if repositories:
        if repository and repository.casefold() not in repositories:
            return "repository_mismatch"
        if not repository and is_exact_code:
            return "repository_unverified"

    path_constraints = scopes["paths"]
    filename_constraints = scopes["filenames"]
    extension_constraints = scopes["extensions"]
    has_file_scope = bool(path_constraints or filename_constraints or extension_constraints)
    if has_file_scope and not path:
        return "location_unverified"
    if path_constraints and not any(_path_matches(path, value) for value in path_constraints):
        return "path_mismatch"
    if filename_constraints:
        basename = path.rsplit("/", 1)[-1]
        if not any(fnmatch.fnmatchcase(basename, _clean(value)) for value in filename_constraints):
            return "filename_mismatch"
    if extension_constraints and not any(
        _extension_matches(path, value) for value in extension_constraints
    ):
        return "extension_mismatch"

    languages = scopes["languages"]
    if languages:
        language_result = _language_matches(hit, languages[0])
        if language_result is False:
            return "language_mismatch"
        if language_result is None and is_exact_code:
            return "language_unverified"

    return None


def filter_scoped_hits(
    plan: QueryPlan,
    request: CodeSearchRequest,
    hits: Iterable[CodeSearchHit],
) -> tuple[list[CodeSearchHit], Diagnostic | None]:
    """Drop evidence that cannot satisfy an explicit caller scope.

    Provider-side filters are hints, not proof: several backends accept the
    request fields but return broader results. Exact code hits without enough
    location metadata are rejected under an explicit scope. Semantic/context
    hits may remain when a scope cannot be verified, except for explicit
    path/filename/extension constraints where an unlocated hit is unusable.
    """

    scopes = _effective_scopes(plan, request)
    if not any(scopes.values()):
        return list(hits), None

    kept: list[CodeSearchHit] = []
    reasons: Counter[str] = Counter()
    providers: Counter[str] = Counter()
    for hit in hits:
        reason = _rejection_reason(hit, scopes)
        if reason is None:
            if hit.result_kind == "code_match":
                hit.source_metadata["scope_verified"] = True
            kept.append(hit)
            continue
        reasons[reason] += 1
        providers[hit.provider] += 1

    if not reasons:
        return kept, None
    diagnostic = Diagnostic(
        provider="code_search",
        outcome="partial",
        message=f"Filtered {sum(reasons.values())} provider result(s) that violated explicit scope",
        failure_kind="validation",
        query=request.query,
        details={
            "filtered_count": sum(reasons.values()),
            "reasons": dict(reasons),
            "providers": dict(providers),
            "scopes": {key: list(values) for key, values in scopes.items() if values},
        },
    )
    return kept, diagnostic
