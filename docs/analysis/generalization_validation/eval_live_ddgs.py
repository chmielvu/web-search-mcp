"""Live ddgs (DuckDuckGo) exercise: compare ORIGINAL vs REFINED queries for a
curated set of pairs spanning the techniques validated in eval_local.py.

Uses the exact same ddgs.text() call shape as the project's own provider
(src/kindly_web_search_mcp_server/search/providers/ddg.py) for realism:
    from ddgs import DDGS
    with DDGS(timeout=...) as ddgs:
        ddgs.text(query, max_results=N)

Rate-limit conscious: small curated N, sleep between calls, tolerate
"No results found" like the production provider does.

Run: python analysis/generalization_validation/eval_live_ddgs.py
"""
from __future__ import annotations

import json
import time
from urllib.parse import urlparse

from ddgs import DDGS

PAIRS = [
    # (label, technique, original, refined)
    ("typo_fix_rapidfuzz", "spelling", "nikee air max size 10", "nike air max size 10"),
    ("typo_fix_rapidfuzz", "spelling", "resturants near me open now", "restaurants near me open now"),
    ("typo_fix_rapidfuzz", "spelling", "flght delay compensation eu rules", "flight delay compensation eu rules"),
    ("segmentation_wordninja", "segmentation", "toplawyersinnewyork", "top lawyers in new york"),
    ("segmentation_wordninja", "segmentation", "buyusedcarnearme", "buy used car near me"),
    ("segmentation_wordninja", "segmentation", "nearbycoffeeshops", "nearby coffee shops"),
    ("segmentation_wordninja", "segmentation", "cheapflightstoparis", "cheap flights to paris"),
    # negative-control: symspell's own overcorrected output from eval_local.py,
    # used AS the "refined" side to empirically show overcorrection hurts results.
    (
        "symspell_overcorrection_harm",
        "spelling_negative_control",
        "iphone 15 vs samsung galaxy s24 comparison",
        "iphone of is samsung galaxy see comparison",
    ),
    # WordNet synonym expansion: OR-style broadened query
    (
        "wordnet_synonym_expansion",
        "synonym_expansion",
        "affordable health insurance",
        "affordable OR cheap health insurance",
    ),
]


def run_query(ddgs: DDGS, query: str, max_results: int = 10) -> dict:
    t0 = time.perf_counter()
    try:
        raw = list(ddgs.text(query, max_results=max_results))
    except Exception as exc:
        if "No results found" in str(exc):
            raw = []
        else:
            return {"error": f"{type(exc).__name__}: {exc}", "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
    latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    domains = []
    titles = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        link = item.get("href") or item.get("link") or item.get("url") or ""
        titles.append(item.get("title", ""))
        try:
            domains.append(urlparse(link).netloc.lower().lstrip("www."))
        except Exception:
            pass
    return {
        "result_count": len(raw),
        "unique_domains": len(set(domains)),
        "domains": domains,
        "top_titles": titles[:3],
        "latency_ms": latency_ms,
    }


def main():
    results = []
    with DDGS(timeout=15) as ddgs:
        for label, technique, original, refined in PAIRS:
            print(f"\n=== {label} ({technique}) ===")
            print(f"  original: {original!r}")
            orig_res = run_query(ddgs, original)
            print(f"    -> {json.dumps({k: v for k, v in orig_res.items() if k != 'domains'})}")
            time.sleep(2.5)

            print(f"  refined:  {refined!r}")
            ref_res = run_query(ddgs, refined)
            print(f"    -> {json.dumps({k: v for k, v in ref_res.items() if k != 'domains'})}")
            time.sleep(2.5)

            results.append({
                "label": label,
                "technique": technique,
                "original_query": original,
                "refined_query": refined,
                "original": orig_res,
                "refined": ref_res,
            })

    out_path = "live_ddgs_results.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nSaved raw results to {out_path}")

    print("\n" + "=" * 70)
    print("SUMMARY")
    for r in results:
        o, f = r["original"], r["refined"]
        if "error" in o or "error" in f:
            print(f"  [{r['label']:28s}] ERROR: orig={o.get('error')} refined={f.get('error')}")
            continue
        delta_results = f["result_count"] - o["result_count"]
        delta_domains = f["unique_domains"] - o["unique_domains"]
        print(
            f"  [{r['label']:28s}] results {o['result_count']:2d} -> {f['result_count']:2d} "
            f"(Δ{delta_results:+d})  domains {o['unique_domains']:2d} -> {f['unique_domains']:2d} "
            f"(Δ{delta_domains:+d})  lat {o['latency_ms']:.0f}ms -> {f['latency_ms']:.0f}ms"
        )


if __name__ == "__main__":
    main()
