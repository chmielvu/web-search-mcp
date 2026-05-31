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
    assert re.search(r"\bmetadata\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\blinks\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\bcontinuation_notice\b", doc, flags=re.IGNORECASE)


def test_batch_get_content_tool_docstring_defines_decision_boundary() -> None:
    from kindly_web_search_mcp_server.server import batch_get_content

    doc = _doc(batch_get_content)
    assert re.search(r"\b3\+\s+URLs\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\bget_content\b", doc)
    assert re.search(r"\btotal_char_budget\b", doc)
    assert re.search(r"\bhas_more\b.*\bcursor\b", doc, flags=re.IGNORECASE | re.DOTALL)
    assert re.search(r"\bmetadata\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\blinks\b", doc, flags=re.IGNORECASE)


def test_discover_links_tool_docstring_exposes_link_discovery_boundary() -> None:
    from kindly_web_search_mcp_server.server import discover_links

    doc = _doc(discover_links)
    assert re.search(r"\boutbound links\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\bsitemap\b", doc, flags=re.IGNORECASE)
    assert re.search(r"\bmax_links\b", doc, flags=re.IGNORECASE)


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

    doc = _call(get_workflow_doc)
    for term in [
        "web_search",
        "get_content",
        "batch_get_content",
        "discover_links",
        "gemini_search",
        "perplexity_search",
        "youtube_search",
        "youtube_transcript",
        "composio_similarlinks",
        "quick_web_search",
    ]:
        assert term in doc


def test_workflow_prompts_encode_server_features() -> None:
    from kindly_web_search_mcp_server.server import (
        plan_web_research_prompt,
        evaluate_web_results_prompt,
        research_gap_analysis_prompt,
    )

    plan_msgs = _call(plan_web_research_prompt, "test question", depth="medium")
    eval_msgs = _call(evaluate_web_results_prompt, "test goal")
    gap_msgs = _call(
        research_gap_analysis_prompt,
        "test goal",
        sources_found="example.com, docs.example.org",
    )

    # All return list[Message]
    assert isinstance(plan_msgs, list)
    assert isinstance(eval_msgs, list)
    assert isinstance(gap_msgs, list)

    # plan_web_research encodes tool routing and query formulation
    plan_text = " ".join(_message_text(m) for m in plan_msgs)
    assert "web_search" in plan_text
    assert "gemini_search" in plan_text
    assert "perplexity_search" in plan_text
    assert "academic_search" in plan_text
    assert "batch_get_content" in plan_text
    assert "rewrite=true" in plan_text
    assert "rewrite=false" in plan_text
    assert "provider_count" in plan_text
    assert "quick" in plan_text
    assert "deep" in plan_text

    # evaluate_web_results encodes quality signals and decision rules
    eval_text = " ".join(_message_text(m) for m in eval_msgs)
    assert "provider_count" in eval_text
    assert "has_more" in eval_text
    assert "cursor" in eval_text
    assert "batch_get_content" in eval_text
    assert "get_content" in eval_text

    # research_gap_analysis encodes iteration and gap patterns
    gap_text = " ".join(_message_text(m) for m in gap_msgs)
    assert "decomposition" in gap_text.lower() or "triangulation" in gap_text.lower()
    assert "termination" in gap_text.lower()
    assert "breadth" in gap_text.lower()
    assert "provider_count" in gap_text


def test_suggest_tool_prompt_encodes_tool_routing_table() -> None:
    from kindly_web_search_mcp_server.server import suggest_tool_prompt

    msgs = _call(
        suggest_tool_prompt,
        "I need to find academic papers about transformer attention mechanisms",
    )

    assert isinstance(msgs, list)
    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert msgs[1].role == "user"

    # First message should contain the full tool routing table.
    routing_text = _message_text(msgs[0])
    assert "web_search" in routing_text
    assert "academic_search" in routing_text
    assert "gemini_search" in routing_text
    assert "perplexity_search" in routing_text
    assert "get_content" in routing_text
    assert "batch_get_content" in routing_text
    assert "discover_links" in routing_text

    # User message should contain the task
    user_text = _message_text(msgs[1])
    assert "transformer attention" in user_text
    assert "Which tool" in user_text
