"""FastMCP middleware for query quality protection and advisory."""

from .expensive_tool_protection import (
    EXPENSIVE_TOOLS,
    QUERY_QUALITY_STEERING_MESSAGE,
    ExpensiveToolProtectionMiddleware,
    create_expensive_tool_middleware,
)
from .gemini_advisory import (
    GEMINI_TOOLS,
    GEMINI_QUERY_ADVISORY,
    GeminiAdvisoryMiddleware,
    create_gemini_advisory_middleware,
)
from .query_guidance import (
    QUERY_QUALITY_TIPS,
    RESULT_EXTRACTION_GUIDANCE,
    QueryQualityMiddleware,
    ResultGuidanceMiddleware,
    create_query_quality_middleware,
    create_result_guidance_middleware,
)
from .rate_limits import (
    DifferentiatedRateLimitMiddleware,
    create_differentiated_rate_limit_middleware,
)

__all__ = [
    "EXPENSIVE_TOOLS",
    "QUERY_QUALITY_STEERING_MESSAGE",
    "ExpensiveToolProtectionMiddleware",
    "create_expensive_tool_middleware",
    "GEMINI_TOOLS",
    "GEMINI_QUERY_ADVISORY",
    "GeminiAdvisoryMiddleware",
    "create_gemini_advisory_middleware",
    "QUERY_QUALITY_TIPS",
    "RESULT_EXTRACTION_GUIDANCE",
    "QueryQualityMiddleware",
    "ResultGuidanceMiddleware",
    "create_query_quality_middleware",
    "create_result_guidance_middleware",
    "DifferentiatedRateLimitMiddleware",
    "create_differentiated_rate_limit_middleware",
]
