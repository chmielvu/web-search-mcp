from __future__ import annotations

from pathlib import Path

from kindly_web_search_mcp_server.content.sanitize import (
    repair_empty_md_links,
    sanitize_markdown,
    strip_boilerplate,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fetch_overhaul"


def test_strip_advertisement_and_empty_bullets() -> None:
    markdown = (_FIXTURES / "F-q1-bbc.md").read_text(encoding="utf-8")
    cleaned = strip_boilerplate(markdown)
    assert "Advertisement" not in cleaned
    assert not any("not yet fully loaded" in line.lower() for line in cleaned.splitlines())
    assert not any(line.strip() in {"*", "-"} for line in cleaned.splitlines())


def test_strip_npr_captions_keeps_story() -> None:
    markdown = (_FIXTURES / "F-q1-npr-captions.md").read_text(encoding="utf-8")
    cleaned = strip_boilerplate(markdown)
    assert "hide caption" not in cleaned.lower()
    assert "toggle caption" not in cleaned.lower()
    assert "Rescue crews" in cleaned
    assert "Meteorologists" in cleaned
    assert cleaned.count("Scott Olson/Getty Images") <= 1


def test_repair_empty_markdown_links() -> None:
    repaired = repair_empty_md_links("see the [guide]() on HTTP")
    assert repaired == "see the guide on HTTP"
    kept = sanitize_markdown("see the [guide](https://example.com/http-404)")
    assert "[guide](https://example.com/http-404)" in kept
