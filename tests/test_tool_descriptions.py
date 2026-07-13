from __future__ import annotations

import re


def _doc(obj: object) -> str:
    if hasattr(obj, "fn"):
        obj = getattr(obj, "fn")
    doc = getattr(obj, "__doc__", None) or ""
    return doc.strip()


def _call(obj: object, *args: object, **kwargs: object) -> object:
    target = getattr(obj, "fn", obj)
    return target(*args, **kwargs)


def _message_text(message: object) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    text = getattr(content, "text", None)
    if isinstance(text, str):
        return text
    return str(content)


def _count_bullets_in_section(doc: str, header: str) -> int:
    """
    Count '-' bullet lines inside a docstring section starting at `header`.

    This is intentionally heuristic: the goal is to enforce that we provide multiple
    concrete examples without asserting exact phrasing.
    """
    match = re.search(
        rf"^\s*{re.escape(header)}\s*:\s*$",
        doc,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if not match:
        return 0
    after = doc[match.end() :]
    # Stop at the next section header-like line (e.g., "When not to use:", "Args:", "Returns:", etc.).
    stop = re.search(
        r"^\s*[A-Z][A-Za-z _/-]{2,}\s*:\s*$",
        after,
        flags=re.MULTILINE,
    )
    body = after[: stop.start()] if stop else after
    return len(re.findall(r"^\s*-\s+\S", body, flags=re.MULTILINE))


def test_web_search_tool_docstring_is_agent_oriented() -> None:
    from kindly_web_search_mcp_server.server import web_search

    doc = _doc(web_search)
    assert doc, "web_search must have a non-empty docstring (tool description)."

    # Multiple concrete "when to use" examples (agent-facing guidance).
    assert _count_bullets_in_section(doc, "When to use") >= 2

    # Explicit "when not to use" and cross-reference to get_content.
    assert re.search(r"when not to use", doc, flags=re.IGNORECASE)
    assert re.search(r"\bget_content\b", doc)

    # Env vars in a configuration/prerequisites context (not just mentioned).
    assert re.search(
        r"(requires|prereq|config).{0,200}\bSEARXNG_BASE_URL\b",
        doc,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert re.search(
        r"(requires|prereq|config).{0,200}\bTAVILY_API_KEY\b",
        doc,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # num_results default + recommended range (context control).
    assert re.search(r"\bnum_results\b.*\bdefault\b", doc, flags=re.IGNORECASE | re.DOTALL)
    assert re.search(
        r"\bnum_results\b.*\brecommended\b.*\brange\b",
        doc,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert re.search(
        r"\brewrite=True\b.*\bnormal discovery\b", doc, flags=re.IGNORECASE | re.DOTALL
    )
    assert re.search(r"\brewrite=False\b.*\bexact", doc, flags=re.IGNORECASE | re.DOTALL)

    # Output shape and lightweight result guarantees.
    assert re.search(r"results", doc)
    assert re.search(r"lightweight", doc, flags=re.IGNORECASE)
    assert re.search(
        r"\bprovider_count\b.*\bagreement signal\b",
        doc,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert not re.search(r"page_content.*always.*string", doc, flags=re.IGNORECASE | re.DOTALL)


def test_get_content_tool_docstring_is_agent_oriented() -> None:
    from kindly_web_search_mcp_server.server import get_content

    doc = _doc(get_content)
    assert doc, "get_content must have a non-empty docstring (tool description)."

    # Core description elements
    assert re.search(r"\bmarkdown\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\bwindowing\b|\bwindow\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\b7-stage\b|\bcontent resolution\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\bhas_more\b", doc, flags=re.IGNORECASE)

    # Summary and focus features
    assert re.search(r"summary_mode", doc, flags=re.IGNORECASE)
    assert re.search(r"focus_query", doc, flags=re.IGNORECASE)


def test_batch_get_content_tool_docstring_defines_decision_boundary() -> None:
    from kindly_web_search_mcp_server.server import batch_get_content

    doc = _doc(batch_get_content)
    assert re.search(r"\b3\+\s*URLs\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\bget_content\b", doc)
    assert re.search(r"total character budget", doc, flags=re.IGNORECASE)
    assert re.search(r"\bhas_more\b.*\bcursor\b", doc, flags=re.IGNORECASE | re.DOTALL)
    assert re.search(r"summary_mode", doc, flags=re.IGNORECASE)
    assert re.search(r"focus_query", doc, flags=re.IGNORECASE)


def test_discover_links_tool_docstring_exposes_link_discovery_boundary() -> None:
    from kindly_web_search_mcp_server.server import discover_links

    doc = _doc(discover_links)
    assert re.search(r"\boutbound links\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\bsitemap\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\bURLs only\b|\bURLs\b.*\bnot page content\b", doc, flags=re.IGNORECASE)


def test_generate_sitemap_tool_docstring_exposes_tavily_map_contract() -> None:
    from kindly_web_search_mcp_server.server import generate_sitemap

    doc = _doc(generate_sitemap)
    assert re.search(r"\bTavily\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\bMap\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\binstructions\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\bmax_depth\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\bmax_breadth\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\blimit\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\bselect_paths\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\bexclude_domains\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\ballow_external\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\bfallback\b", doc, flags=re.IGNORECASE)


def test_server_instructions_are_routing_policy_not_provider_readme() -> None:
    from kindly_web_search_mcp_server.server import mcp

    instructions = mcp.instructions
    assert "quick_web_search" in instructions
    assert "web_search" in instructions
    assert "rewrite=true" in instructions
    assert "batch_get_content" in instructions


def test_workflow_resource_mentions_all_steering_tools() -> None:
    from kindly_web_search_mcp_server.server import get_workflow_doc

    doc = _call(get_workflow_doc)
    for term in [
        "web_search",
        "get_content",
        "batch_get_content",
        "discover_links",
        "gemini_search",
        "youtube_search",
        "youtube_transcript",
        "composio_similarlinks",
        "quick_web_search",
    ]:
        assert term in doc


def test_workflow_doc_encodes_routing_and_depth_strategy() -> None:
    """Verify the workflow doc covers tool routing, depth strategy, and gap analysis."""
    from kindly_web_search_mcp_server.server import get_workflow_doc

    doc = _call(get_workflow_doc)

    # Tool routing table should mention key tools
    assert "web_search" in doc
    assert "gemini_search" in doc
    assert "batch_get_content" in doc
    assert "academic_search" in doc

    # Query rewrite policy
    assert "rewrite=true" in doc
    assert "rewrite=false" in doc

    # Depth strategy
    assert "quick" in doc
    assert "medium" in doc
    assert "deep" in doc

    # Result evaluation
    assert "provider_count" in doc

    # Gap analysis / termination
    assert re.search(r"terminat", doc, re.IGNORECASE)


def test_workflow_prompt_returns_messages() -> None:
    """Verify the registered workflow prompt returns a list of Message objects."""
    from fastmcp.prompts import Message

    # The server registers web_search_workflow_prompt under name "web_search_workflow"
    # We can call it directly since server re-exports it
    from kindly_web_search_mcp_server.server import web_search_workflow_prompt

    result = _call(web_search_workflow_prompt)
    assert isinstance(result, list)
    assert len(result) >= 1
    assert isinstance(result[0], Message)
    assert result[0].role == "user"


