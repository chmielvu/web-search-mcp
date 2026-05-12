from __future__ import annotations

import re


def _doc(obj: object) -> str:
    if hasattr(obj, "fn"):
        obj = getattr(obj, "fn")
    doc = getattr(obj, "__doc__", None) or ""
    return doc.strip()


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

    # Multiple concrete “when to use” examples (agent-facing guidance).
    assert _count_bullets_in_section(doc, "When to use") >= 2

    # Explicit “when not to use” and cross-reference to get_content.
    assert re.search(r"when not to use", doc, flags=re.IGNORECASE)
    assert re.search(r"\bget_content\b", doc)

    # Env vars in a configuration/prerequisites context (not just mentioned).
    assert re.search(r"(requires|prereq|config).{0,200}\bSEARXNG_BASE_URL\b", doc, flags=re.IGNORECASE | re.DOTALL)
    assert re.search(r"(requires|prereq|config).{0,200}\bTAVILY_API_KEY\b", doc, flags=re.IGNORECASE | re.DOTALL)

    # num_results default + recommended range (context control).
    assert re.search(r"\bnum_results\b.*\bdefault\b", doc, flags=re.IGNORECASE | re.DOTALL)
    assert re.search(r"\bnum_results\b.*\brecommended\b.*\brange\b", doc, flags=re.IGNORECASE | re.DOTALL)
    assert re.search(r"\brewrite=True\b.*\bnormal discovery\b", doc, flags=re.IGNORECASE | re.DOTALL)
    assert re.search(r"\brewrite=False\b.*\bexact", doc, flags=re.IGNORECASE | re.DOTALL)

    # Output shape and lightweight result guarantees.
    assert re.search(r"results", doc)
    assert re.search(r"lightweight", doc, flags=re.IGNORECASE)
    assert re.search(r"\bprovider_count\b.*\bagreement signal\b", doc, flags=re.IGNORECASE | re.DOTALL)
    assert not re.search(r"page_content.*always.*string", doc, flags=re.IGNORECASE | re.DOTALL)


def test_get_content_tool_docstring_is_agent_oriented() -> None:
    from kindly_web_search_mcp_server.server import get_content

    doc = _doc(get_content)
    assert doc, "get_content must have a non-empty docstring (tool description)."

    assert _count_bullets_in_section(doc, "When to use") >= 2
    assert re.search(r"when not to use", doc, flags=re.IGNORECASE)
    assert re.search(r"\bweb_search\(", doc)

    assert re.search(r"\binput_url\b", doc)
    assert re.search(r"\bnormalized_url\b", doc)
    assert re.search(r"\bfetched_url\b", doc)
    assert re.search(r"\bsource_type\b", doc)
    assert re.search(r"\bfetch_backend\b", doc)
    assert re.search(r"\bstatus\b.*\b(success|partial|blocked|unsupported|error)\b", doc, flags=re.IGNORECASE | re.DOTALL)
    assert re.search(r"\bwindow\b.*\bnext_offset\b", doc, flags=re.IGNORECASE | re.DOTALL)


def test_batch_get_content_tool_docstring_defines_decision_boundary() -> None:
    from kindly_web_search_mcp_server.server import batch_get_content

    doc = _doc(batch_get_content)
    assert re.search(r"\b3\+\s+URLs\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\bget_content\b", doc)
    assert re.search(r"\btotal_char_budget\b", doc)
    assert re.search(r"\bhas_more\b.*\bcursor\b", doc, flags=re.IGNORECASE | re.DOTALL)


def test_server_instructions_are_routing_policy_not_provider_readme() -> None:
    from kindly_web_search_mcp_server.server import mcp

    instructions = mcp.instructions
    assert "Tool routing" in instructions
    assert "rewrite=true" in instructions
    assert "rewrite=false" in instructions
    assert "batch_get_content" in instructions
    assert "perplexity_search only after" in instructions


def test_workflow_resource_mentions_all_steering_tools() -> None:
    from kindly_web_search_mcp_server.server import get_workflow_doc

    doc = get_workflow_doc()
    for term in [
        "web_search",
        "get_content",
        "batch_get_content",
        "gemini_search",
        "perplexity_search",
        "youtube_search",
        "youtube_transcript",
        "composio_similarlinks",
        "quick_web_search",
    ]:
        assert term in doc


def test_workflow_prompts_include_batch_and_expensive_tool_boundaries() -> None:
    from kindly_web_search_mcp_server.server import (
        debug_error_prompt,
        find_library_docs_prompt,
        research_topic_prompt,
    )

    debug_doc = debug_error_prompt("TypeError: bad")
    research_doc = research_topic_prompt("FastMCP tools")
    docs_doc = find_library_docs_prompt("FastMCP", "tools")

    assert "rewrite=False" in debug_doc
    assert "batch_get_content" in debug_doc
    assert "batch_get_content" in research_doc
    assert "perplexity_search" in research_doc
    assert "youtube_search" in research_doc
    assert "batch_get_content" in docs_doc
    assert "composio_similarlinks" in docs_doc
