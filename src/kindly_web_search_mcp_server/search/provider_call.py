"""Provider invocation helpers for search execution."""

from __future__ import annotations

import inspect
from collections.abc import Mapping

from .options import SearchOptions


def build_provider_call_kwargs(
    provider_fn: object,
    *,
    search_options: SearchOptions | None,
    provider_arguments: Mapping[str, object] | None,
) -> dict[str, object]:
    signature = inspect.signature(provider_fn)  # type: ignore[arg-type]
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    kwargs: dict[str, object] = {}
    if search_options is not None and ("search_options" in signature.parameters or accepts_kwargs):
        kwargs["search_options"] = search_options
    if not provider_arguments:
        return kwargs
    if accepts_kwargs:
        kwargs.update(provider_arguments)
        return kwargs
    allowed = {
        name
        for name, parameter in signature.parameters.items()
        if name not in {"query", "num_results", "http_client"}
        and parameter.kind
        in {
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
    }
    kwargs.update({key: value for key, value in provider_arguments.items() if key in allowed})
    return kwargs
