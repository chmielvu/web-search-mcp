from __future__ import annotations

import json
from typing import Any

from langchain.tools import tool
from pydantic import BaseModel

from .academic_tools import get_academic_tools
from .content_tools import get_content_tools
from .models import FinalAnswerInput
from .rerank_tools import get_rerank_tools
from .search_tools import get_search_tools


class _FinalAnswerOutput(BaseModel):
    answer: str
    sources: list[dict]
    confidence: float = 0.0
    gaps: str = ""


async def _final_answer_tool(
    answer: str,
    sources: list[dict],
    confidence: float = 0.0,
    gaps: str = "",
) -> str:
    """Signal that research is complete. The agent should call this with the
    final synthesized answer and the list of sources it actually used.
    The runner will prefer this structured payload over pure text extraction
    when present (extraction remains the fallback).
    """
    payload = _FinalAnswerOutput(
        answer=answer,
        sources=sources,
        confidence=confidence,
        gaps=gaps,
    )
    return json.dumps(payload.model_dump(exclude_none=True), indent=2)


final_answer_tool = tool(
    "final_answer",
    args_schema=FinalAnswerInput,
    description=(
        "Call this tool when you have completed the research and are ready to "
        "return a final answer. Provide the synthesized answer, the sources you "
        "actually relied on (with URLs), your confidence (0-1), and any gaps or "
        "uncertainties. This gives stronger citation guarantees than free text."
    ),
)(_final_answer_tool)


def build_agent_tools() -> list[Any]:
    tools = [
        *get_search_tools(),
        *get_content_tools(),
        *get_academic_tools(),
        *get_rerank_tools(),
    ]
    tools.append(final_answer_tool)
    return tools
