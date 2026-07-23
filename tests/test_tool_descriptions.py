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
