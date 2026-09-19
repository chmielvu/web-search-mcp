"""Narrow, reusable pre-network validation helpers for agent-supplied input.

These helpers run **before** any provider call. They enforce three cheap
properties that an LLM agent's text might violate even when the field "looks
fine":

1. The string contains no control characters (U+0000-U+001F or U+007F) that
   would silently truncate or escape their way through shell pipelines.
2. The string is not a likely-credential artifact (sk-/ghp_/github_pat_/xai-/
   AIza-/Bearer prefixes and long token-shaped assignments).
3. File paths do not traverse the working directory or target sensitive
   credential stores (.env/.key/.pem).

Validation deliberately rejects rather than silently sanitises: an agent that
passed a token into ``--query`` needs the loud failure, not a quietly
stripped payload. ``InputValidationError`` subclasses ``ValueError`` so the
existing ``read_url_inputs`` contract stays intact; callers in command
modules convert it to ``CliError(usage_error)`` at the boundary.

URL validation lives here too: a single ``https://`` check covers control
characters, whitespace, and the http/https scheme gate in one place. Numeric
limit checks (``--limit``, ``--max-pages``) use ``validate_int_in_range`` so
every command can share the same range semantics.
"""

from __future__ import annotations

import re
from typing import Final
from urllib.parse import urlsplit

# U+0000-U+001F (C0 controls + NUL/tab/newline) and U+007F (DEL).
_CONTROL_CHAR_PATTERN: Final[re.Pattern[str]] = re.compile(r"[\x00-\x1f\x7f]")

# Path traversal segments as they would appear literally in a CLI argument,
# regardless of the host OS. Backslash forms matter on POSIX shells where a
# user types them by hand; the rest of the path is still normalised later.
_TRAVERSAL_PATTERN: Final[re.Pattern[str]] = re.compile(r"\.\.[/\\]")

# Filenames that almost always hold credentials or private material. Matched
# against the basename only so legitimate paths containing ``.env.example``
# still pass.
_SENSITIVE_BASENAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\.(?:env|key|pem)(?:\..+)?$",
    re.IGNORECASE,
)

# Prefixes that identify leaked API tokens. The list is intentionally short:
# false positives on natural-language queries are worse than the marginal
# coverage gain from a longer regex.
_CREDENTIAL_PREFIX_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:sk-[A-Za-z0-9]{8,}|ghp_[A-Za-z0-9]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|xai-[A-Za-z0-9]{8,}|"
    r"AIza[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9_\-.=]{16,})"
)

# Long hex / base64 token assignments such as ``token=abcdef...`` or
# ``api_key: "..."``. The shape matters more than the prefix so we catch
# unknown credential types without needing a per-vendor regex.
_LONG_TOKEN_ASSIGNMENT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:token|api[_-]?key|secret|password|auth)"
    r"\s*[:=]\s*['\"]?[A-Za-z0-9_\-.+/=]{24,}",
    re.IGNORECASE,
)
# Opaque filesystem paths must not carry shell syntax even though the CLI
# receives argv values rather than invoking a shell itself. Rejecting it keeps
# copied agent arguments safe when they are later composed into shell commands.
_SHELL_METACHAR_PATTERN: Final[re.Pattern[str]] = re.compile(r"[;|&$()]")

# Conservative URL grammar: scheme + authority + optional path. Whitespace and
# control characters are rejected outright via ``_CONTROL_CHAR_PATTERN``.
_URL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^https?://[^\s\x00-\x1f\x7f]+$",
    re.IGNORECASE,
)


class InputValidationError(ValueError):
    """Raised when an agent-supplied string violates a pre-network policy.

    Subclasses ``ValueError`` so existing call sites that catch
    ``ValueError`` continue to work; ``read_url_inputs`` keeps its public
    contract intact and command modules convert this to ``CliError`` at the
    boundary.
    """


def _ensure_text(value: object, *, field: str) -> str:
    """Coerce ``value`` to ``str`` or raise ``InputValidationError``."""
    if isinstance(value, str):
        return value
    raise InputValidationError(f"{field} must be a string; got {type(value).__name__}.")


def contains_control_chars(value: str) -> bool:
    """Return True if ``value`` contains any C0 control or DEL character."""
    return _CONTROL_CHAR_PATTERN.search(value) is not None


def contains_credential_material(value: str) -> bool:
    """Return True if ``value`` looks like a leaked credential."""
    if _CREDENTIAL_PREFIX_PATTERN.search(value):
        return True
    return bool(_LONG_TOKEN_ASSIGNMENT_PATTERN.search(value))


def is_path_traversal(value: str) -> bool:
    """Return True if ``value`` contains ``../`` or ``..\\`` segments."""
    return _TRAVERSAL_PATTERN.search(value) is not None


def is_sensitive_basename(value: str) -> bool:
    """Return True if the basename of ``value`` targets credential material."""
    # ``Path(value).name`` would do the work, but we want to inspect the
    # literal token the user passed — extension chains like
    # ``.env.production.local`` should still be rejected.
    normalized = value.replace("\\", "/").rstrip("/")
    basename = normalized.rsplit("/", 1)[-1]
    return _SENSITIVE_BASENAME_PATTERN.match(basename) is not None


def is_http_url(value: str) -> bool:
    """Return True if ``value`` is a syntactically valid http/https URL."""
    if not _URL_PATTERN.match(value):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    return bool(parsed.netloc)


def reject_control_chars(
    value: object,
    *,
    field: str,
) -> str:
    """Return ``value`` as ``str`` when free of control characters.

    Raises ``InputValidationError`` with an actionable hint naming the field.
    """
    text = _ensure_text(value, field=field)
    if contains_control_chars(text):
        raise InputValidationError(
            f"{field} contains control characters (U+0000-U+001F or U+007F). "
            "Strip NULs, tabs, newlines, and escape sequences before retrying."
        )
    return text


def reject_credential_material(
    value: object,
    *,
    field: str,
) -> str:
    """Return ``value`` as ``str`` when it carries no credential material.

    Allows ordinary punctuation (``token=foo`` does not match unless the
    value is at least 24 characters long), so legitimate phrases like
    ``"Bearer of the standard"`` are not flagged.
    """
    text = _ensure_text(value, field=field)
    if contains_credential_material(text):
        raise InputValidationError(
            f"{field} appears to contain a credential (API key, token, or "
            "Bearer assignment). Pass the value through an environment "
            "variable or secret manager instead."
        )
    return text


def validate_text_input(
    value: object,
    *,
    field: str,
) -> str:
    """Apply ``reject_control_chars`` then ``reject_credential_material``."""
    text = reject_control_chars(value, field=field)
    return reject_credential_material(text, field=field)


def validate_safe_path(
    value: object,
    *,
    field: str,
    allow_stdin: bool = False,
) -> str:
    """Validate a file path the CLI will read or write.

    The path must be free of control characters and ``..`` traversal segments,
    and its basename must not match ``.env/.key/.pem`` (with optional extra
    extensions). When ``allow_stdin`` is True the literal ``-`` passes
    through, mirroring how ``--input-file -`` already works.
    """
    text = _ensure_text(value, field=field)
    if allow_stdin and text == "-":
        return text
    if contains_control_chars(text):
        raise InputValidationError(
            f"{field} contains control characters. Pass a plain filesystem path."
        )
    if _SHELL_METACHAR_PATTERN.search(text):
        raise InputValidationError(
            f"{field} contains shell metacharacters. Pass a plain filesystem path "
            "without ;, |, &, $, or parentheses."
        )
    if is_path_traversal(text):
        raise InputValidationError(
            f"{field} contains a path-traversal segment ('../' or '..\\\\'). "
            "Use a relative path inside the working directory or an absolute "
            "path that does not escape the intended target."
        )
    if is_sensitive_basename(text):
        raise InputValidationError(
            f"{field} targets a credential file (.env/.key/.pem). "
            "Pick an output path under outputs/ or another non-secret directory."
        )
    return text


def validate_http_url(
    value: object,
    *,
    field: str,
) -> str:
    """Return ``value`` when it is a syntactically valid http/https URL."""
    text = _ensure_text(value, field=field)
    if contains_control_chars(text):
        raise InputValidationError(
            f"{field} contains control characters or whitespace. Pass a "
            "single http(s) URL without line breaks or escape sequences."
        )
    text = reject_credential_material(text, field=field)
    if not is_http_url(text):
        raise InputValidationError(
            f"{field} must be an http:// or https:// URL. "
            "Check the scheme, host, and that the value contains no spaces."
        )
    return text


def validate_int_in_range(
    value: object,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> int:
    """Coerce ``value`` to ``int`` and require ``minimum <= value <= maximum``.

    Accepts ``int`` and ``str`` (parsed via ``int``); rejects everything else
    and any out-of-range value with an actionable hint.
    """
    if isinstance(value, bool):
        # ``bool`` is a subclass of ``int``; reject True/False explicitly so
        # ``--limit True`` does not silently bind ``limit=1``.
        raise InputValidationError(f"{field} must be an integer between {minimum} and {maximum}.")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str):
        try:
            number = int(value.strip())
        except ValueError as exc:
            raise InputValidationError(
                f"{field} must be an integer between {minimum} and {maximum}."
            ) from exc
    else:
        raise InputValidationError(f"{field} must be an integer between {minimum} and {maximum}.")
    if number < minimum or number > maximum:
        raise InputValidationError(
            f"{field}={number} is outside the allowed range [{minimum}, {maximum}]."
        )
    return number


__all__ = [
    "InputValidationError",
    "contains_control_chars",
    "contains_credential_material",
    "is_http_url",
    "is_path_traversal",
    "is_sensitive_basename",
    "reject_control_chars",
    "reject_credential_material",
    "validate_http_url",
    "validate_int_in_range",
    "validate_safe_path",
    "validate_text_input",
]
