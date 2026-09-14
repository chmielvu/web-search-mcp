"""Throwaway: classic trafilatura vs rs-trafilatura quality shootout."""

import re
import sys

import httpx
import rs_trafilatura
import trafilatura

HEADERS = {"User-Agent": "Mozilla/5.0 Chrome/124 Safari/537.36", "Accept": "text/html,*/*;q=0.8"}

URLS = [
    "https://docs.python.org/3/library/asyncio-task.html",
    "https://en.wikipedia.org/wiki/Web_scraping",
    "https://en.wikipedia.org/wiki/List_of_countries_by_GDP_(nominal)",
    "https://github.com/psf/requests",
    "https://news.ycombinator.com/item?id=48806575",
]

NAV_PROBE = re.compile(r"(?i)navigation|breadcrumb|cookie|newsletter|subscribe|sign in|log in|follow us|share on|advertisement")
HEAD_RE = re.compile(r"\s{0,3}#{1,6}\s+\S")


def stats(md_text: str) -> dict:
    lines = [line for line in md_text.splitlines() if line.strip()]
    return {
        "chars": len(md_text),
        "words": len(md_text.split()),
        "lines": len(lines),
        "fences": md_text.count("```"),
        "pipes": sum(1 for line in lines if "|" in line),
        "heads": sum(1 for line in lines if HEAD_RE.match(line)),
        "navhits": sum(1 for line in lines if NAV_PROBE.search(line)),
    }


def classic(html: str, url: str, **kw):
    return trafilatura.extract(
        html, url=url, output_format="markdown", include_links=True, include_tables=True,
        include_images=False, include_comments=False, include_formatting=True, **kw,
    ) or ""


def main() -> None:
    print("classic trafilatura", trafilatura.__version__)
    with httpx.Client(headers=HEADERS, timeout=20.0, follow_redirects=True) as c:
        for url in URLS:
            try:
                html = c.get(url).text[:500000]
            except Exception as exc:
                print(f"\n===== {url} FETCH FAIL {exc}")
                continue
            print(f"\n===== {url} html={len(html)} =====")
            cands = {
                "classic-balanced": classic(html, url),
                "classic-precision": classic(html, url, favor_precision=True),
            }
            try:
                cands["classic-recall"] = classic(html, url, favor_recall=True)
            except Exception:
                pass
            for name, kw in [("rs-balanced", {}), ("rs-precision", {"favor_precision": True}), ("rs-recall", {"favor_recall": True})]:
                try:
                    x = rs_trafilatura.extract(html, url=url, output_markdown=True, include_links=True, include_tables=True, **kw)
                    cands[name] = (x.content_markdown or x.main_content or "", x.page_type, float(x.extraction_quality or 0))
                except Exception as exc:
                    print(f"{name:18s} FAIL {exc}")
            for name, val in cands.items():
                if isinstance(val, tuple):
                    md, pt, q = val
                    print(f"{name:18s} type={pt} q={q:.2f} {stats(md)}")
                else:
                    print(f"{name:18s} {stats(val)}")
            # code-fidelity probe: every <pre> block's first 40 clean chars present?
            pres = re.findall(r"<pre[^>]*>(.*?)</pre>", html, re.I | re.S)
            if pres:
                clean = lambda s: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()
                for name, val in cands.items():
                    md = val[0] if isinstance(val, tuple) else val
                    flat = re.sub(r"\s+", " ", md)
                    hits = sum(1 for p in pres if clean(p)[:40] and clean(p)[:40] in flat)
                    print(f"  codeblocks {name:16s} {hits}/{len(pres)} present")


if __name__ == "__main__":
    sys.exit(main())
