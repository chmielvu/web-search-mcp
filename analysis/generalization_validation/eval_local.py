"""Local (no-network) accuracy/latency evaluation of candidate techniques
against the 105-case, 20-domain-bucket synthetic corpus with ground truth.

Run: python analysis/generalization_validation/eval_local.py
"""
from __future__ import annotations

import json
import statistics as stats
import time

from corpus import CORPUS
import techniques as T


def eval_segmentation():
    cases = [c for c in CORPUS if c.glued_segments]
    correct, latencies = 0, []
    rows = []
    for c in cases:
        t0 = time.perf_counter()
        got = tuple(w.lower() for w in T.segment_glued(c.text))
        latencies.append((time.perf_counter() - t0) * 1000)
        ok = got == c.glued_segments
        correct += ok
        rows.append((c.text, got, c.glued_segments, ok))
    return {
        "technique": "wordninja.segment_glued",
        "n": len(cases),
        "exact_match_accuracy": round(correct / len(cases), 3),
        "avg_latency_ms": round(stats.mean(latencies), 4),
        "p95_latency_ms": round(sorted(latencies)[int(len(latencies) * 0.95) - 1], 4),
        "rows": rows,
    }


def eval_spelling():
    cases = [c for c in CORPUS if c.typo_of]
    results = {"symspell": {"correct": 0, "lat": []}, "rapidfuzz": {"correct": 0, "lat": []}}
    rows = []
    for c in cases:
        t0 = time.perf_counter()
        sym_out = T.correct_spelling_symspell(c.text)
        results["symspell"]["lat"].append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        rf_out = T.correct_spelling_rapidfuzz(c.text)
        results["rapidfuzz"]["lat"].append((time.perf_counter() - t0) * 1000)
        sym_ok = sym_out.lower() == c.typo_of.lower()
        rf_ok = rf_out.lower() == c.typo_of.lower()
        results["symspell"]["correct"] += sym_ok
        results["rapidfuzz"]["correct"] += rf_ok
        rows.append((c.text, c.typo_of, sym_out, sym_ok, rf_out, rf_ok))
    out = []
    for name, d in results.items():
        out.append({
            "technique": f"spelling::{name}",
            "n": len(cases),
            "exact_match_accuracy": round(d["correct"] / len(cases), 3),
            "avg_latency_ms": round(stats.mean(d["lat"]), 4),
            "p95_latency_ms": round(sorted(d["lat"])[int(len(d["lat"]) * 0.95) - 1], 4),
        })
    return out, rows


def eval_spelling_false_positives():
    """Critical generalization check: run BOTH correctors on the clean (non-typo)
    queries too, to measure overcorrection rate on already-correct text
    (entity names, brand names, technical terms) -- this is the literature's
    documented failure mode for dictionary-based spellcheckers."""
    clean_cases = [c for c in CORPUS if not c.typo_of and c.true_lang == "en"]
    sym_changed, rf_changed = 0, 0
    rows = []
    for c in clean_cases:
        sym_out = T.correct_spelling_symspell(c.text)
        rf_out = T.correct_spelling_rapidfuzz(c.text)
        sym_flip = sym_out.lower() != c.text.lower()
        rf_flip = rf_out.lower() != c.text.lower()
        sym_changed += sym_flip
        rf_changed += rf_flip
        if sym_flip or rf_flip:
            rows.append((c.text, sym_out, sym_flip, rf_out, rf_flip))
    return {
        "n_clean": len(clean_cases),
        "symspell_overcorrection_rate": round(sym_changed / len(clean_cases), 3),
        "rapidfuzz_overcorrection_rate": round(rf_changed / len(clean_cases), 3),
        "overcorrected_examples": rows,
    }


def eval_langid():
    results = {"langdetect": {"correct": 0, "lat": []}, "langid": {"correct": 0, "lat": []}, "lingua": {"correct": 0, "lat": []}}
    rows = []
    for c in CORPUS:
        t0 = time.perf_counter()
        ld = T.detect_lang_langdetect(c.text)
        results["langdetect"]["lat"].append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        li = T.detect_lang_langid(c.text)
        results["langid"]["lat"].append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        lg = T.detect_lang_lingua(c.text)
        results["lingua"]["lat"].append((time.perf_counter() - t0) * 1000)
        results["langdetect"]["correct"] += (ld == c.true_lang)
        results["langid"]["correct"] += (li == c.true_lang)
        results["lingua"]["correct"] += (lg == c.true_lang)
        rows.append((c.text, c.true_lang, ld, li, lg))
    out = []
    for name, d in results.items():
        out.append({
            "technique": f"langid::{name}",
            "n": len(CORPUS),
            "accuracy": round(d["correct"] / len(CORPUS), 3),
            "avg_latency_ms": round(stats.mean(d["lat"]), 4),
            "p95_latency_ms": round(sorted(d["lat"])[int(len(d["lat"]) * 0.95) - 1], 4),
        })
    return out, rows


def eval_intent():
    correct = 0
    confusion: dict[str, dict[str, int]] = {}
    latencies = []
    rows = []
    for c in CORPUS:
        t0 = time.perf_counter()
        pred = T.classify_intent_rule_based(c.text)
        latencies.append((time.perf_counter() - t0) * 1000)
        confusion.setdefault(c.true_intent, {}).setdefault(pred, 0)
        confusion[c.true_intent][pred] += 1
        ok = pred == c.true_intent
        correct += ok
        rows.append((c.text, c.domain, c.true_intent, pred, ok))
    return {
        "technique": "classify_intent_rule_based",
        "n": len(CORPUS),
        "accuracy": round(correct / len(CORPUS), 3),
        "avg_latency_ms": round(stats.mean(latencies), 4),
        "confusion_matrix": confusion,
        "rows": rows,
    }


def eval_wordnet_coverage():
    """Coverage check: what fraction of content words per domain get >=1
    WordNet synonym? Low coverage on a domain = generalization gap."""
    by_domain: dict[str, list[float]] = {}
    for c in CORPUS:
        if c.true_lang != "en":
            continue
        syns = T.expand_synonyms_wordnet(c.text)
        content_words = [w for w in __import__("re").findall(r"[a-zA-Z]+", c.text.lower())
                         if w not in T._STOPWORDS and len(w) >= 3]
        if not content_words:
            continue
        covered = sum(1 for w in content_words if w in syns)
        by_domain.setdefault(c.domain, []).append(covered / len(content_words))
    return {d: round(stats.mean(v), 3) for d, v in sorted(by_domain.items())}


if __name__ == "__main__":
    print("=" * 70)
    print("SEGMENTATION (wordninja)")
    seg = eval_segmentation()
    print(json.dumps({k: v for k, v in seg.items() if k != "rows"}, indent=2))
    for r in seg["rows"]:
        status = "OK" if r[3] else "MISS"
        print(f"  [{status}] {r[0]!r} -> {r[1]} (expected {r[2]})")

    print("=" * 70)
    print("SPELL CORRECTION (typo cases)")
    spell_summary, spell_rows = eval_spelling()
    print(json.dumps(spell_summary, indent=2))
    for r in spell_rows:
        print(f"  orig={r[0]!r} expected={r[1]!r}")
        print(f"    symspell={r[2]!r} ok={r[3]}  rapidfuzz={r[4]!r} ok={r[5]}")

    print("=" * 70)
    print("SPELL CORRECTION OVERCORRECTION CHECK (clean queries)")
    over = eval_spelling_false_positives()
    print(json.dumps({k: v for k, v in over.items() if k != "overcorrected_examples"}, indent=2))
    for r in over["overcorrected_examples"]:
        print(f"  clean={r[0]!r} -> symspell={r[1]!r}(flip={r[2]}) rapidfuzz={r[3]!r}(flip={r[4]})")

    print("=" * 70)
    print("LANGUAGE IDENTIFICATION")
    lang_summary, lang_rows = eval_langid()
    print(json.dumps(lang_summary, indent=2))
    for r in lang_rows:
        if r[1] != "en" or True:
            pass
    misses = [r for r in lang_rows if not (r[1] == r[2] == r[3])]
    print(f"  total rows={len(lang_rows)}, non-unanimous/mismatch rows={len(misses)}")
    for r in misses[:30]:
        print(f"  text={r[0]!r} true={r[1]} langdetect={r[2]} langid={r[3]} lingua={r[4]}")

    print("=" * 70)
    print("INTENT CLASSIFICATION")
    intent = eval_intent()
    print(json.dumps({k: v for k, v in intent.items() if k not in ("rows", "confusion_matrix")}, indent=2))
    print("confusion_matrix (true -> {pred: count}):")
    print(json.dumps(intent["confusion_matrix"], indent=2))
    misses = [r for r in intent["rows"] if not r[4]]
    print(f"  {len(misses)} misclassified:")
    for r in misses:
        print(f"    [{r[1]}] {r[0]!r} true={r[2]} pred={r[3]}")

    print("=" * 70)
    print("WORDNET SYNONYM COVERAGE BY DOMAIN (fraction of content words with >=1 synonym)")
    cov = eval_wordnet_coverage()
    print(json.dumps(cov, indent=2))
