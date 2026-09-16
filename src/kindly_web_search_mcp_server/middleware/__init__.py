"""FastMCP middleware for query quality protection and advisory."""

from .alias_mapping import (
    ArgumentAliasingMiddleware,
    create_argument_aliasing_middleware,
)
from .expensive_tool_protection import (
    EXPENSIVE_TOOLS,
    QUERY_QUALITY_STEERING_MESSAGE,
    ExpensiveToolProtectionMiddleware,
    create_expensive_tool_middleware,
)
from .query_guidance import (
    GEMINI_QUERY_ADVISORY,
    GEMINI_TOOLS,
    DynamicGuidanceMiddleware,
    create_dynamic_guidance_middleware,
)
from .rate_limits import (
    DifferentiatedRateLimitMiddleware,
    create_differentiated_rate_limit_middleware,
)
from .result_persistence import (
    ResultPersistenceMiddleware,
    create_result_persistence_middleware,
)
from .stdout_guard import (
    StdoutGuardMiddleware,
    create_stdout_guard_middleware,
)

__all__ = [
    "EXPENSIVE_TOOLS",
    "GEMINI_QUERY_ADVISORY",
    "GEMINI_TOOLS",
    "QUERY_QUALITY_STEERING_MESSAGE",
    "ArgumentAliasingMiddleware",
    "DifferentiatedRateLimitMiddleware",
    "DynamicGuidanceMiddleware",
    "ExpensiveToolProtectionMiddleware",
    "ResultPersistenceMiddleware",
    "StdoutGuardMiddleware",
    "create_argument_aliasing_middleware",
    "create_differentiated_rate_limit_middleware",
    "create_dynamic_guidance_middleware",
    "create_expensive_tool_middleware",
    "create_result_persistence_middleware",
    "create_stdout_guard_middleware",
]
