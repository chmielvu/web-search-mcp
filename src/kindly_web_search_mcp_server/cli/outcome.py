"""Exit-code discipline for commands whose failure arrives inside the payload.

A command can complete without raising while its payload says the operation failed:
every URL errored, the transcript is missing, the page yielded no links. The process
still exited 0, so scripts and CI read success. Commands call these helpers after
building their payload and before emitting it.
"""

from __future__ import annotations

from typing import Any, NoReturn

from .errors import CliError, match_hint_rule
from .exit_codes import ExitCode


def raise_for_payload_error(payload: dict[str, Any], *, command: str, hint: str) -> None:
    """Exit non-zero when the payload carries a top-level ``error``."""
    error = payload.get("error")
    if not error:
        return
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("code") or "")
    else:
        message = str(error)
    _fail(command=command, message=message, hint=hint, status="error")


def raise_if_no_item_succeeded(
    payload: dict[str, Any],
    *,
    command: str,
    hint: str,
    items_key: str = "results",
    success_status: str = "success",
) -> None:
    """Exit non-zero when the payload lists items and none of them succeeded.

    Partial success stays a success: the caller received usable items and the
    per-item statuses describe the rest.
    """
    items = [item for item in (payload.get(items_key) or []) if isinstance(item, dict)]
    if not items or any(item.get("status") == success_status for item in items):
        return
    failed = [item for item in items if item.get("status") != success_status]
    first_error = next((text for text in map(_item_error_text, failed) if text), "")
    status = str(failed[0].get("status") or "error")
    if not first_error:
        first_error = f"All {len(items)} item(s) failed (status={status!r})."
    _fail(command=command, message=first_error, hint=hint, status=status)


def _item_error_text(item: dict[str, Any]) -> str:
    """Readable error text from an item whose ``error`` is a string or a typed dict."""
    error = item.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "")
    return str(error or "")


def _fail(*, command: str, message: str, hint: str, status: str) -> NoReturn:
    """Raise ``CliError``, reusing the CLI's message-based exit-code taxonomy."""
    matched = match_hint_rule(message) if message else None
    raise CliError(
        kind=matched.kind if matched else "provider_error",
        message=message or f"{command} failed (status={status!r}).",
        hint=matched.hint if matched else hint,
        exit_code=matched.exit_code if matched else ExitCode.PROVIDER_ERROR,
        context={"command": command, "status": status},
    )
