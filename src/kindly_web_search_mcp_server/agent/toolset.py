from __future__ import annotations

from typing import Any

from .academic_tools import get_academic_tools
from .content_tools import get_content_tools
from .rerank_tools import get_rerank_tools
from .search_tools import get_search_tools


def build_agent_tools() -> list[Any]:
    return [
        *get_search_tools(),
        *get_content_tools(),
        *get_academic_tools(),
        *get_rerank_tools(),
    ]
