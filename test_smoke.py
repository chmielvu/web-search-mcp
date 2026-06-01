"""Smoke tests for provider_health and status_classifier.

Run with: .venv/Scripts/python.exe test_smoke.py
"""

import sys, time
from pathlib import Path

project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root / "src"))

# Test provider_health (import the search package first to trigger its __init__)
# We need opentelemetry installed via the venv...
try:
    from kindly_web_search_mcp_server.search.provider_health import (
        ProviderHealthTracker,
        reset_provider_health,
    )
except ImportError as e:
    print(f"SKIP provider_health (import failed: {e})")
    sys.exit(1)

# Basic tests
def test_basic():
    reset_provider_health()
    t = ProviderHealthTracker()
    assert t.is_healthy("searxng")
    t.mark_failure("searxng")
    assert not t.is_healthy("searxng")
    s = t.get_state("searxng")
    assert s["consecutive_failures"] == 1
    assert s["cooldown_remaining_s"] > 0
    t.mark_success("searxng")
    assert t.is_healthy("searxng")
    assert t.get_state("searxng")["consecutive_failures"] == 0
    print("PASS: test_basic")

def test_backoff():
    t = ProviderHealthTracker()
    for i in range(5):
        t.mark_failure("brave")
    assert t.get_state("brave")["consecutive_failures"] == 5
    t.mark_success("brave")
    assert t.get_state("brave")["consecutive_failures"] == 0
    print("PASS: test_backoff")

def test_counters():
    t = ProviderHealthTracker()
    t.mark_failure("p1")
    t.mark_failure("p1")
    t.mark_success("p1")
    t.mark_failure("p1")
    s = t.get_state("p1")
    assert s["total_failures"] == 3
    assert s["total_successes"] == 1
    print("PASS: test_counters")

def test_reset():
    t = ProviderHealthTracker()
    t.mark_failure("a")
    t.mark_failure("b")
    t.reset()
    assert len(t.all_states()) == 0
    assert t.is_healthy("a")
    t.mark_failure("x")
    t.reset("x")
    assert t.is_healthy("x")
    print("PASS: test_reset")

def test_sorted():
    t = ProviderHealthTracker()
    t.mark_success("c")
    t.mark_failure("a")
    t.mark_success("b")
    names = [s["provider"] for s in t.all_states()]
    assert names == ["a", "b", "c"], f"Expected sorted, got {names}"
    print("PASS: test_sorted")

def test_expired_cooldown():
    t = ProviderHealthTracker()
    t.mark_failure("expired")
    state = t._states["expired"]
    state.cooldown_until = time.monotonic() - 0.1
    assert t.is_healthy("expired")
    print("PASS: test_expired_cooldown")

# Status classifier tests
from kindly_web_search_mcp_server.content.status_classifier import (
    classify_markdown,
    classify_quality,
)

def test_login_wall():
    r = classify_markdown("Sign in to continue reading this article. " + "filler " * 30)
    assert r.status == "blocked"
    assert "login_wall" in (r.reason or "")
    print("PASS: test_login_wall")

def test_paywall():
    r = classify_markdown("Subscribe to read the full article. Upgrade to access. " + "filler " * 30)
    assert r.status == "blocked"
    assert "paywall" in (r.reason or "")
    print("PASS: test_paywall")

def test_cloudflare():
    r = classify_markdown("Checking your browser before accessing. Cloudflare. " + "wait " * 30)
    assert r.status == "blocked"
    print("PASS: test_cloudflare")

def test_404():
    r = classify_markdown("404 Not Found. The requested URL was not found. " + "nginx " * 30)
    assert r.status == "error"
    print("PASS: test_404")

def test_500():
    r = classify_markdown("500 Internal Server Error. Please try again. " + "retry " * 30)
    assert r.status == "error"
    print("PASS: test_500")

def test_success():
    r = classify_markdown(" ".join(["meaningful"] * 80))
    assert r.status == "success"
    assert r.cacheable
    print("PASS: test_success")

def test_redirect():
    assert classify_markdown("https://example.com/actual").status == "partial"
    assert classify_markdown("https://example.com/actual").reason == "redirect_only"
    assert classify_markdown("Redirecting to https://x.com/y").status == "partial"
    print("PASS: test_redirect")

def test_garbled():
    r = classify_markdown("\x00\x01\x02\x03\x04\x05" + " a" * 30 + " b" * 5)
    assert r.status == "error"
    assert r.reason == "garbled_content"
    print("PASS: test_garbled")

def test_empty_and_short():
    assert classify_markdown("").status == "error"
    assert classify_markdown("").reason == "empty_content"
    assert classify_markdown("too short").status == "partial"
    assert classify_markdown("too short").reason == "too_short"
    print("PASS: test_empty_and_short")

def test_quality():
    q1 = classify_quality(" ".join(["good"] * 100))
    assert q1 > 0.5, f"Good content should score >0.5, got {q1}"
    assert classify_quality("") == 0.0
    q3 = classify_quality("Access denied. Verify you are human." + " ignored " * 50)
    assert q3 < 0.5, f"Blocked should score <0.5, got {q3}"
    assert classify_quality("few words") < 0.5
    print("PASS: test_quality")

if __name__ == "__main__":
    test_basic()
    test_backoff()
    test_counters()
    test_reset()
    test_sorted()
    test_expired_cooldown()
    test_login_wall()
    test_paywall()
    test_cloudflare()
    test_404()
    test_500()
    test_success()
    test_redirect()
    test_garbled()
    test_empty_and_short()
    test_quality()
    print("\n========== ALL 16 TESTS PASSED ==========")
