"""Strict base model for all contracts.

All contract models inherit from StrictBase to enforce:
- strict=True  (no coercion)
- frozen=True  (immutable)
- extra="forbid"  (reject unknown fields)

Use ``Literal`` types and ``Annotated`` with ``BeforeValidator`` for
JSON-tuple serialization (e.g., tuple[str, ...] stored as JSON arrays).
"""

from __future__ import annotations

from typing import Annotated, Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema


T = TypeVar("T")


# --------------------------------------------------------------------
# JSON-safe tuple handling
# --------------------------------------------------------------------


class _FrozenTuple(list):
    """A frozen (immutable) list suitable as a Pydantic field."""

    __slots__ = ()


def _tuple_to_list(v: Any) -> list:
    if isinstance(v, (list, tuple, _FrozenTuple)):
        return list(v)
    if isinstance(v, str):
        import json

        return json.loads(v)
    return v


# Use Annotated + BeforeValidator so tuple[str,...] accepts JSON arrays.
JsonTuple = Annotated[list, Field(json_schema_extra={"type": "array"}), _tuple_to_list]
"""Sentinel annotated type for JSON-serialized tuples.

Usage: field: JsonTuple[str]  (renders as list in JSON, reads tuple back)
"""


class StrictBase(BaseModel):
    """Base for all canonical contract models.

    Guarantees strict validation, immutability, and no extra fields.
    """

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
    )
