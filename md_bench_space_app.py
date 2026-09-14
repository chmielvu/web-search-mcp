"""Gradio Space: extraction-backend bench (CPU basic) + per-backend JSON API.

One endpoint per backend library — the whole point is comparing them:

    from gradio_client import Client
    c = Client("chmielvu/md-extraction-bench")
    c.view_api()  # /extract_rs, /extract_pulpie, /extract_mineru,
                  # /extract_p2, /extract_all, /extract, /methods
    out = c.predict("https://example.com/article", "", "balanced",
                    api_name="/extract_rs")
    # out = {"ok": True, "backend": "rs-trafilatura", "meta": {...},
    #        "stats": {...}, "markdown": ..., "clean_html": ...}

Backends:
  rs-trafilatura  deterministic Rust extraction + page-type profiles (CPU, ms)
  pulpie          Pulpie Orange Small 210M block classifier -> clean HTML -> MD
  mineru          MinerU-HTML 0.5B SLM main-element selector -> clean HTML -> MD
  p2-direct       lxml + denylist converter, no main-content selection (baseline)

ML backends lazy-load on first call (slow first hit, fast after) and are
thread-safe via per-backend locks for concurrent Gradio requests.
"""

from __future__ import annotations

import re
import threading
import time

import gradio as gr
import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as _md

try:
    import rs_trafilatura  # type: ignore[import-not-found]
except Exception:
    rs_trafilatura = None  # type: ignore

_PULPIE = None
_PULPIE_ERR: str | None = None
_pulpie_lock = threading.Lock()

_MINERU = None
_MINERU_ERR: str | None = None
_mineru_lock = threading.Lock()


def get_pulpie():
    """Pulpie Orange Small on CPU. Lazy: ~400MB weights on first call."""
    global _PULPIE, _PULPIE_ERR
    with _pulpie_lock:
        if _PULPIE is None and _PULPIE_ERR is None:
            try:
                from pulpie import Extractor

                _PULPIE = Extractor(model="orange-small", device="cpu")
            except Exception as exc:
                _PULPIE_ERR = str(exc)
        return _PULPIE


def get_mineru():
    """MinerU-HTML 0.5B Transformers backend on CPU. Lazy: ~1GB weights on first call.

    Generation is capped at 2048 new tokens: upstream defaults to 16384, which
    at CPU greedy-decode speed (~5-10 tok/s on 2 vCPU) hangs pages for 30+ min.
    Compact item-ID output only needs hundreds of tokens.
    """
    global _MINERU, _MINERU_ERR
    with _mineru_lock:
        if _MINERU is None and _MINERU_ERR is None:
            try:
                from mineru_html import MinerUHTML_Transformers

                _MINERU = MinerUHTML_Transformers(
                    model_init_kwargs={"device_map": "cpu", "dtype": "float32"},
                    model_gen_kwargs={"max_new_tokens": 2048},
                )
            except Exception as exc:
                _MINERU_ERR = str(exc)
        return _MINERU
RS_MODES = ["balanced", "precision", "recall"]

HEADERS = {"User-Agent": "Mozilla/5.0 Chrome/124 Safari/537.36", "Accept": "text/html,*/*;q=0.8"}



def p2_convert(html: str) -> str:
    """P2 converter: lxml + extended denylist + ATX headings."""
    soup = BeautifulSoup(html or "", "lxml")
    for el in soup(["script", "style", "nav", "header", "footer", "aside", "form", "iframe", "noscript"]):
        el.decompose()
    return _md(str(soup), heading_style="ATX", bullets="-")


def _stats(md_text: str) -> dict:
    lines = [line for line in md_text.splitlines() if line.strip()]
    return {
        "chars": len(md_text),
        "words": len(md_text.split()),
        "lines": len(lines),
        "fences": md_text.count("```"),
        "pipes": sum(1 for line in lines if "|" in line),
        "heads": sum(1 for line in lines if re.match(r"\s{0,3}#{1,6}\s+\S", line)),
    }


def _fetch_html(url: str, html_in: str) -> tuple[str, str]:
    """Return (html, fetch_note). Raises RuntimeError on empty input/fetch failure."""
    html = (html_in or "").strip()
    note = ""
    if not html and (url or "").strip():
        with httpx.Client(headers=HEADERS, timeout=20.0, follow_redirects=True) as c:
            r = c.get(url.strip())
            r.raise_for_status()
            html = r.text[:500000]
            note = f"fetched {len(html)} chars st={r.status_code}"
    if not html:
        raise RuntimeError("provide URL or raw HTML")
    return html, note


def _ok(backend: str, meta: dict, markdown: str, clean_html: str = "") -> dict:
    return {"ok": True, "backend": backend, "error": "", "meta": meta, "stats": _stats(markdown), "markdown": markdown,
            "clean_html": clean_html}


def _fail(backend: str, error: str) -> dict:
    return {"ok": False, "backend": backend, "error": error, "meta": {}, "stats": {}, "markdown": "", "clean_html": ""}


# ---------------------------------------------------------------- backends


def run_rs(url: str, html_in: str, mode: str = "balanced") -> dict:
    """rs-trafilatura endpoint core. mode: balanced | precision | recall."""
    backend = "rs-trafilatura"
    if rs_trafilatura is None:
        return _fail(backend, "rs-trafilatura not installed in Space")
    if mode not in RS_MODES:
        return _fail(backend, f"unknown mode {mode!r}, choose from {RS_MODES}")
    try:
        html, note = _fetch_html(url, html_in)
    except Exception as exc:
        return _fail(backend, str(exc))
    t0 = time.perf_counter()
    x = rs_trafilatura.extract(
        html, url=url or None, output_markdown=True, include_links=True, include_tables=True,
        favor_precision=(mode == "precision"), favor_recall=(mode == "recall"),
    )
    m = x.content_markdown or x.main_content or ""
    meta = {"mode": mode, "page_type": x.page_type, "quality": float(x.extraction_quality or 0),
            "title": (x.title or "")[:120], "infer_s": round(time.perf_counter() - t0, 2)}
    if note:
        meta["fetch"] = note
    return _ok(backend, meta, m)


def run_pulpie(url: str, html_in: str) -> dict:
    backend = "pulpie-orange-small"
    try:
        html, note = _fetch_html(url, html_in)
    except Exception as exc:
        return _fail(backend, str(exc))
    ex = get_pulpie()
    if ex is None:
        return _fail(backend, f"pulpie unavailable: {_PULPIE_ERR}")
    t0 = time.perf_counter()
    res = ex.extract(html)
    clean = res.html or ""
    # Chain: Pulpie's reconstructed clean HTML through the P2 converter as well,
    # so the selection-vs-conversion split is observable per backend.
    m = res.markdown or p2_convert(clean)
    meta = {"n_main": res.n_main, "n_other": res.n_other, "infer_s": round(time.perf_counter() - t0, 2)}
    if note:
        meta["fetch"] = note
    return _ok(backend, meta, m, clean_html=clean)


def run_mineru(url: str, html_in: str) -> dict:
    """MinerU-HTML 0.5B endpoint core: SLM element selector -> main HTML -> MD."""
    backend = "mineru-html-0.5b"
    try:
        html, note = _fetch_html(url, html_in)
    except Exception as exc:
        return _fail(backend, str(exc))
    ex = get_mineru()
    if ex is None:
        return _fail(backend, f"mineru unavailable: {_MINERU_ERR}")
    t0 = time.perf_counter()
    # CPU prefill over huge pages dominates runtime (minutes per 100KB at
    # 0.5B-fp32 on 2 vCPU), so cap input and record it honestly in meta.
    truncated = len(html) > 150000
    try:
        result = ex.process(html[:150000])
        first = result[0] if isinstance(result, (list, tuple)) else result
        main_html = getattr(getattr(first, "output_data", first), "main_content", "") or ""
        if not main_html and isinstance(first, dict):
            main_html = first.get("main_content", "") or ""
    except Exception as exc:
        return _fail(backend, f"mineru inference failed: {exc}")
    m = p2_convert(main_html) if main_html else ""
    if not m.strip():
        return _fail(backend, "mineru returned empty main content")
    meta = {"infer_s": round(time.perf_counter() - t0, 2), "input_truncated_150k": truncated}
    if note:
        meta["fetch"] = note
    return _ok(backend, meta, m, clean_html=main_html)


def run_p2(url: str, html_in: str) -> dict:
    """P2-direct baseline: converter only, no main-content selection."""
    backend = "p2-direct"
    try:
        html, note = _fetch_html(url, html_in)
    except Exception as exc:
        return _fail(backend, str(exc))
    t0 = time.perf_counter()
    m = p2_convert(html)
    meta = {"converter": "lxml + denylist, no selection", "infer_s": round(time.perf_counter() - t0, 2)}
    if note:
        meta["fetch"] = note
    return _ok(backend, meta, m)


def run_all(url: str, html_in: str) -> dict:
    """Run every available backend on the same input. One call, full comparison."""
    return {
        "rs_balanced": run_rs(url, html_in, "balanced"),
        "rs_precision": run_rs(url, html_in, "precision"),
        "pulpie": run_pulpie(url, html_in),
        "mineru": run_mineru(url, html_in),
        "p2_direct": run_p2(url, html_in),
    }


def api_methods() -> list:
    available = []
    if rs_trafilatura is not None:
        available.append("rs-trafilatura")
    try:
        import pulpie  # type: ignore[import-not-found]  # noqa: F401

        available.append("pulpie-orange-small")
    except Exception:
        pass
    try:
        import mineru_html  # type: ignore[import-not-found]  # noqa: F401

        available.append("mineru-html-0.5b")
    except Exception:
        pass
    available.append("p2-direct")
    return available


# ---------------------------------------------------------------- UI


def ui_run(url: str, html_in: str, method: str):
    dispatch = {
        "rs-balanced": lambda: run_rs(url, html_in, "balanced"),
        "rs-precision": lambda: run_rs(url, html_in, "precision"),
        "rs-recall": lambda: run_rs(url, html_in, "recall"),
        "pulpie": lambda: run_pulpie(url, html_in),
        "mineru": lambda: run_mineru(url, html_in),
        "p2-direct": lambda: run_p2(url, html_in),
    }
    if method == "all":
        outs = run_all(url, html_in)
        rows = []
        for name, o in outs.items():
            s = o["stats"] if o["ok"] else {}
            rows.append(f"**{name}**: ok={o['ok']} " + (" ".join(f"{k}={v}" for k, v in s.items()) if s else o["error"][:120]))
        first = next((o["markdown"] for o in outs.values() if o["ok"]), "")
        return "\n\n".join(rows), first
    out = dispatch.get(method, lambda: _fail(method, "unknown method"))()
    if not out["ok"]:
        return f"error: {out['error']}", ""
    stats_line = " ".join(f"{k}={v}" for k, v in out["stats"].items())
    return f"{out['backend']} {out['meta']}\n{stats_line}", out["markdown"]


UI_METHODS = ["rs-balanced", "rs-precision", "rs-recall", "pulpie", "mineru", "p2-direct", "all"]

with gr.Blocks(title="Extraction backend bench (CPU)") as demo:
    gr.Markdown("# Extraction backend bench (CPU)\nOne endpoint per backend: `/extract_rs`, `/extract_pulpie`, `/extract_mineru`, `/extract_p2`, `/extract_all`, `/methods`. ML backends lazy-load on first call.")
    with gr.Row():
        with gr.Column(scale=1):
            url_in = gr.Textbox(label="URL")
            html_box = gr.Textbox(label="Raw HTML (optional, overrides fetch if set)", lines=6)
            method_in = gr.Dropdown(UI_METHODS, value="rs-balanced", label="Backend")
            go = gr.Button("Extract", variant="primary")
        with gr.Column(scale=1):
            stats_out = gr.Textbox(label="Stats")
            md_out = gr.Markdown(label="Markdown")
    go.click(fn=ui_run, inputs=[url_in, html_box, method_in], outputs=[stats_out, md_out], api_name="extract_ui")
    # Headless per-backend JSON endpoints (gradio_client: c.predict(url, html, api_name="/extract_pulpie")).
    _j1, _j2, _j3, _j4, _j5 = gr.JSON(visible=False), gr.JSON(visible=False), gr.JSON(visible=False), gr.JSON(visible=False), gr.JSON(visible=False)
    _jm, _ja = gr.JSON(visible=False), gr.JSON(visible=False)
    _b1, _b2, _b3, _b4, _b5, _b6, _b7 = (gr.Button(visible=False) for _ in range(7))
    _mode = gr.Textbox(value="balanced", visible=False)
    _b1.click(fn=run_rs, inputs=[url_in, html_box, _mode], outputs=[_j1], api_name="extract_rs")
    _b2.click(fn=run_pulpie, inputs=[url_in, html_box], outputs=[_j2], api_name="extract_pulpie")
    _b3.click(fn=run_mineru, inputs=[url_in, html_box], outputs=[_j3], api_name="extract_mineru")
    _b4.click(fn=run_p2, inputs=[url_in, html_box], outputs=[_j4], api_name="extract_p2")
    _b5.click(fn=run_all, inputs=[url_in, html_box], outputs=[_j5], api_name="extract_all")
    _b6.click(fn=api_methods, inputs=[], outputs=[_jm], api_name="methods")
    _b7.click(fn=run_rs, inputs=[url_in, html_box, _mode], outputs=[_ja], api_name="extract")
    demo.queue(max_size=8)

demo.launch()
