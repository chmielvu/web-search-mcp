"""Structured error handling with MCP isError compliance.

P1 Critical Pattern: formatToolError from Exa MCP
- Structured error responses with error_type classification
- 429 rate limit detection with actionable guidance
- MCP protocol compliance: isError: true flag
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class StructuredToolError:
    """Structured error for MCP tool responses.

    Attributes:
        error: Human-readable error message
        error_type: Classification: "rate_limit", "auth", "network", "content", "config", "unknown"
        action: Optional actionable guidance for the agent
        provider: Optional provider name that caused the error
        status_code: Optional HTTP status code
        retry_after: Optional seconds to wait before retrying (for rate limits)
    """

    error: str
    error_type: str = "unknown"
    action: str | None = None
    provider: str | None = None
    status_code: int | None = None
    retry_after: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to MCP-compliant error response dict.

        MCP spec requires isError: true for tool execution failures.
        """
        result: dict[str, Any] = {
            "error": self.error,
            "error_type": self.error_type,
            "isError": True,  # MCP protocol requirement
        }
        if self.action:
            result["action"] = self.action
        if self.provider:
            result["provider"] = self.provider
        if self.status_code:
            result["status_code"] = self.status_code
        if self.retry_after:
            result["retry_after"] = self.retry_after
        return result


def classify_error(
    error: Exception,
    provider: str | None = None,
) -> StructuredToolError:
    """Classify an exception into a structured tool error.

    Detects:
    - Rate limits (429) with tier-specific guidance
    - Auth failures (401, 403)
    - Network/timeout issues
    - Content errors (404, parsing failures)
    - Configuration errors

    Args:
        error: The exception to classify
        provider: Optional provider name (e.g., "searxng", "tavily")
        url: Optional URL that caused the error

    Returns:
        StructuredToolError with appropriate error_type and action
    """
    # HTTP status errors
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return _classify_http_status(status, provider, error)

    # Timeout errors
    if isinstance(error, httpx.TimeoutException):
        return StructuredToolError(
            error=f"Request timed out: {str(error)[:80]}",
            error_type="network",
            action="The request took too long. Try again with a simpler query or check network connectivity.",
            provider=provider,
        )

    # Network errors (connection refused, DNS failures, etc.)
    if isinstance(error, httpx.NetworkError):
        return StructuredToolError(
            error=f"Network error: {str(error)[:80]}",
            error_type="network",
            action="Could not connect to the server. Check if the service is running and accessible.",
            provider=provider,
        )

    # Provider-specific config errors
    error_name = type(error).__name__
    if "Config" in error_name or "ConfigError" in error_name:
        return StructuredToolError(
            error=str(error),
            error_type="config",
            action="Configuration error. Check environment variables and server settings.",
            provider=provider,
        )

    # Content/parsing errors
    if "Parse" in error_name or "JSON" in error_name or "Value" in error_name:
        return StructuredToolError(
            error=f"Content parsing error: {str(error)[:80]}",
            error_type="content",
            action="The server returned unexpected content format. The service may be misconfigured.",
            provider=provider,
        )

    # YouTube-specific errors (IP blocking)
    if "YouTube" in str(error) or "transcript" in str(error).lower():
        if (
            "IP" in str(error)
            or "blocked" in str(error).lower()
            or "Cloud" in str(error)
        ):
            return StructuredToolError(
                error="YouTube transcript API blocked this IP (cloud IPs are blocked)",
                error_type="network",
                action="Set KINDLY_YOUTUBE_TRANSCRIPT_PROXY_URL or run from a residential IP.",
                provider="youtube",
            )

    # Generic fallback
    return StructuredToolError(
        error=str(error)[:200],
        error_type="unknown",
        action="An unexpected error occurred. Check logs for details.",
        provider=provider,
    )


def _classify_http_status(
    status: int,
    provider: str | None,
    error: httpx.HTTPStatusError,
) -> StructuredToolError:
    """Classify HTTP status codes into structured errors."""

    # Rate limit (429)
    if status == 429:
        retry_after = _extract_retry_after(error.response)
        action = _rate_limit_action(provider, retry_after)
        return StructuredToolError(
            error=f"Rate limited (429): {provider or 'server'} is throttling requests",
            error_type="rate_limit",
            action=action,
            provider=provider,
            status_code=429,
            retry_after=retry_after,
        )

    # Authentication errors (401)
    if status == 401:
        return StructuredToolError(
            error="Authentication failed (401): Invalid or missing API key",
            error_type="auth",
            action=f"Check that the API key for {provider or 'this provider'} is configured correctly.",
            provider=provider,
            status_code=401,
        )

    # Permission errors (403)
    if status == 403:
        return StructuredToolError(
            error="Permission denied (403): Access forbidden",
            error_type="auth",
            action=_forbidden_action(provider),
            provider=provider,
            status_code=403,
        )

    # Not found (404)
    if status == 404:
        return StructuredToolError(
            error="Resource not found (404)",
            error_type="content",
            action="The requested resource does not exist. Check the URL or query.",
            provider=provider,
            status_code=404,
        )

    # Bad request (400)
    if status == 400:
        return StructuredToolError(
            error=f"Bad request (400): {str(error)[:100]}",
            error_type="content",
            action="The request was invalid. Check query parameters and format.",
            provider=provider,
            status_code=400,
        )

    # Server errors (5xx)
    if 500 <= status < 600:
        return StructuredToolError(
            error=f"Server error ({status}): {provider or 'server'} encountered an internal error",
            error_type="network",
            action="The server is experiencing issues. Try again later.",
            provider=provider,
            status_code=status,
        )

    # Other status codes
    return StructuredToolError(
        error=f"HTTP error ({status}): {str(error)[:80]}",
        error_type="network",
        action="Unexpected HTTP response. Check server status.",
        provider=provider,
        status_code=status,
    )


def _extract_retry_after(response: httpx.Response) -> int | None:
    """Extract Retry-After header value in seconds."""
    retry_after = response.headers.get("Retry-After")
    if not retry_after:
        return None
    try:
        return int(retry_after)
    except ValueError:
        return None


def _rate_limit_action(provider: str | None, retry_after: int | None) -> str:
    """Generate provider-specific rate limit guidance."""
    base = "Rate limit exceeded."

    if provider == "searxng":
        return f"{base} SearXNG is throttling requests. Wait {retry_after or 60}s or reduce query frequency."

    if provider == "tavily":
        return f"{base} Tavily API rate limit. Check your plan limits at tavily.com. Wait {retry_after or 30}s."

    if provider == "brave":
        return f"{base} Brave Search API rate limit. Check your API usage. Wait {retry_after or 30}s."

    if provider == "jina":
        return f"{base} Jina AI rate limit. Check your API tier. Wait {retry_after or 30}s."

    if provider == "gemini":
        return f"{base} Gemini API rate limit. Check Google AI Studio quotas. Wait {retry_after or 60}s."

    if provider == "perplexity":
        return f"{base} Perplexity Sonar rate limit. This is a premium resource. Wait {retry_after or 60}s."

    if retry_after:
        return f"{base} Wait {retry_after}s before retrying."

    return f"{base} Wait 30-60s before retrying."


def _forbidden_action(provider: str | None) -> str:
    """Generate provider-specific forbidden error guidance."""
    if provider == "searxng":
        return "SearXNG denied access. JSON output may be disabled. Enable 'json' format in SearXNG settings.yml."

    if provider in ("tavily", "brave", "jina"):
        return f"{provider} API denied access. Check that your API key is valid and has the required permissions."

    if provider == "youtube":
        return "YouTube blocked this request. Cloud IPs are often blocked. Set KINDLY_YOUTUBE_TRANSCRIPT_PROXY_URL."

    return "Access forbidden. Check permissions, API keys, or server configuration."


def format_tool_error(
    error: Exception,
    provider: str | None = None,
) -> dict[str, Any]:
    """Format an exception as an MCP-compliant tool error response.

    This is the primary entry point for error handling in tools.

    Usage:
        try:
            result = await search_provider(...)
            return result
        except Exception as e:
            return format_tool_error(e, provider="searxng")

    Returns:
        Dict with error details and isError: True (MCP compliance)
    """
    structured = classify_error(error, provider=provider)
    return structured.to_dict()


def is_error_response(response: dict[str, Any]) -> bool:
    """Check if a response dict is an error response.

    Args:
        response: Tool response dict

    Returns:
        True if response contains isError: True
    """
    return response.get("isError") is True
