"""FastMCP middleware for query quality protection and advisory."""

from .expensive_tool_protection import (
    EXPENSIVE_TOOLS,
    QUERY_QUALITY_STEERING_MESSAGE,
    ExpensiveToolProtectionMiddleware,
    create_expensive_tool_middleware,
)
from .query_guidance import (
    GEMINI_TOOLS,
    GEMINI_QUERY_ADVISORY,
    DynamicGuidanceMiddleware,
    create_dynamic_guidance_middleware,
)
from .alias_mapping import (
    ArgumentAliasingMiddleware,
    create_argument_aliasing_middleware,
)
from .rate_limits import (
    DifferentiatedRateLimitMiddleware,
    create_differentiated_rate_limit_middleware,
)
from .stdout_guard import (
    StdoutGuardMiddleware,
    create_stdout_guard_middleware,
)

__all__ = [
    "EXPENSIVE_TOOLS",
    "QUERY_QUALITY_STEERING_MESSAGE",
    "ExpensiveToolProtectionMiddleware",
    "create_expensive_tool_middleware",
    "GEMINI_TOOLS",
    "GEMINI_QUERY_ADVISORY",
    "DynamicGuidanceMiddleware",
    "create_dynamic_guidance_middleware",
    "ArgumentAliasingMiddleware",
    "create_argument_aliasing_middleware",
    "DifferentiatedRateLimitMiddleware",
    "create_differentiated_rate_limit_middleware",
    "StdoutGuardMiddleware",
    "create_stdout_guard_middleware",
]
