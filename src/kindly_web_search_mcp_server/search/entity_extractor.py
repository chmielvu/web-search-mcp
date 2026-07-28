"""Shared hosted GLiNER2 content entity extraction gateway."""

from __future__ import annotations

from ..entity.gliner_client import get_gliner_client
from ..entity.models import EntitySpan


async def extract_entities(
    text: str,
    *,
    provider_name: str = "gliner2",
    session_id: str | None = None,
) -> list[EntitySpan]:
    """Extract optional fetched-content entities through the VPS service."""
    del provider_name, session_id
    return await get_gliner_client().extract_entities(text)
