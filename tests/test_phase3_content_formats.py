from __future__ import annotations

import pytest

from kindly_web_search_mcp_server.content.format_renderers import render_mhtml_markdown
from kindly_web_search_mcp_server.content.typed_content import (
    detect_content_format,
    render_typed_content,
)


def test_detects_rtf_subtitles_and_svg() -> None:
    assert detect_content_format("https://example.com/file.rtf", "text/plain", "{\\rtf1}") == "rtf"
    assert (
        detect_content_format("https://example.com/captions.vtt", "text/plain", "WEBVTT") == "vtt"
    )
    assert detect_content_format("https://example.com/captions.srt", "text/plain", "1") == "srt"
    assert (
        detect_content_format("https://example.com/diagram.svg", "image/svg+xml", "<svg />")
        == "svg"
    )


def test_renders_vtt_and_srt_cues() -> None:
    vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.500\nHello <b>world</b>.\n"
    srt = "1\n00:00:01,000 --> 00:00:02,000\nSecond cue.\n"

    vtt_md, vtt_meta, _ = render_typed_content("vtt", vtt, "https://example.com/a.vtt")
    srt_md, srt_meta, _ = render_typed_content("srt", srt, "https://example.com/a.srt")

    assert "Hello world." in vtt_md
    assert vtt_meta["cue_count"] == 1
    assert "Second cue." in srt_md
    assert srt_meta["cue_count"] == 1


def test_renders_svg_accessible_text_without_executing_scripts() -> None:
    pytest.importorskip("defusedxml")
    svg = '<svg viewBox="0 0 10 10"><title>System diagram</title><script>alert(1)</script><text>Node A</text></svg>'

    markdown, metadata, _ = render_typed_content("svg", svg, "https://example.com/diagram.svg")

    assert "System diagram" in markdown
    assert "Node A" in markdown
    assert "alert(1)" not in markdown
    assert metadata["element_count"] == 4


def test_renders_mhtml_html_part_without_external_fetches() -> None:
    body = (
        "From: sender@example.com\n"
        "MIME-Version: 1.0\n"
        "Content-Type: multipart/related; boundary=part\n\n"
        "--part\n"
        "Content-Type: text/html; charset=utf-8\n\n"
        "<html><body><h1>Saved page</h1><p>Offline text.</p></body></html>\n"
        "--part--\n"
    ).encode()

    markdown, metadata = render_mhtml_markdown(body, "https://example.com/page.mhtml")

    assert "Saved page" in markdown
    assert "Offline text." in markdown
    assert metadata["selected_part"] == "text/html"
