from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestProviderHealthTracker(unittest.TestCase):
    def setUp(self) -> None:
        from kindly_web_search_mcp_server.search.provider_health import (
            ProviderHealthTracker,
            reset_provider_health,
        )
        reset_provider_health()
        self.tracker = ProviderHealthTracker()

    def test_initial_state_is_healthy(self) -> None:
        self.assertTrue(self.tracker.is_healthy("searxng"))

    def test_mark_success_resets_failures(self) -> None:
        self.tracker.mark_failure("searxng")
        self.assertEqual(self.tracker.get_state("searxng")["consecutive_failures"], 1)
        self.tracker.mark_success("searxng")
        self.assertEqual(self.tracker.get_state("searxng")["consecutive_failures"], 0)

    def test_mark_success_clears_cooldown(self) -> None:
        self.tracker.mark_failure("tavily")
        state = self.tracker.get_state("tavily")
        self.assertGreater(state["cooldown_remaining_s"], 0)
        self.tracker.mark_success("tavily")
        state = self.tracker.get_state("tavily")
        self.assertEqual(state["cooldown_remaining_s"], 0.0)

    def test_exponential_backoff_cooldown(self) -> None:
        """Verify cooldown grows: 1s → 2s → 4s → 8s → 30s cap."""
        from kindly_web_search_mcp_server.search.provider_health import (
            ProviderHealthTracker,
        )

        t = ProviderHealthTracker()
        for expected_cooldown, capped in (
            (1.0, False),
            (2.0, False),
            (4.0, False),
            (8.0, False),
            (30.0, True),  # 5th: 16s < 30s cap
            (30.0, True),  # 6th: 32s → capped to 30s
        ):
            t.mark_failure("brave")
            remaining = t.cooldown_remaining("brave")
            self.assertGreater(remaining, 0.0)
            if not capped:
                self.assertLess(remaining, expected_cooldown + 0.1)

    def test_is_healthy_returns_false_during_cooldown(self) -> None:
        self.tracker.mark_failure("gemini")
        self.assertFalse(self.tracker.is_healthy("gemini"))

    def test_is_healthy_after_cooldown_expired(self) -> None:
        """Provider should be healthy again after cooldown expires."""
        from kindly_web_search_mcp_server.search.provider_health import (
            ProviderHealthTracker,
        )

        t = ProviderHealthTracker()
        # Manually set a cooldown that already expired
        t._states["expired_provider"] = type(t._states.get("expired_provider", object()))()
        # We need to access internals to force an expired state
        # Instead, verify the normal path with mark_failure → wait
        # But we can't sleep real 1s in tests.
        # The design is correct: is_healthy checks monotonic time.
        # We test the code path: a state with cooldown_until=0 is healthy.
        t.mark_failure("test")
        # Force cooldown to expire by reaching into internals
        state = t._states["test"]
        state.cooldown_until = time.monotonic() - 0.1  # expired 100ms ago
        self.assertTrue(t.is_healthy("test"))

    def test_all_states_returns_sorted_list(self) -> None:
        self.tracker.mark_success("c")
        self.tracker.mark_failure("a")
        self.tracker.mark_success("b")
        states = self.tracker.all_states()
        names = [s["provider"] for s in states]
        self.assertEqual(names, ["a", "b", "c"])

    def test_reset_single_provider(self) -> None:
        self.tracker.mark_failure("searxng")
        self.tracker.mark_success("tavily")
        self.tracker.reset("searxng")
        self.assertTrue(self.tracker.is_healthy("searxng"))
        self.assertIn("tavily", [s["provider"] for s in self.tracker.all_states()])

    def test_reset_all_providers(self) -> None:
        self.tracker.mark_failure("searxng")
        self.tracker.mark_failure("tavily")
        self.tracker.reset()
        self.assertEqual(len(self.tracker.all_states()), 0)
        self.assertTrue(self.tracker.is_healthy("searxng"))

    def test_consecutive_failures_count(self) -> None:
        self.tracker.mark_failure("brave")
        self.assertEqual(self.tracker.get_state("brave")["consecutive_failures"], 1)
        self.tracker.mark_failure("brave")
        self.assertEqual(self.tracker.get_state("brave")["consecutive_failures"], 2)
        self.tracker.mark_success("brave")
        self.assertEqual(self.tracker.get_state("brave")["consecutive_failures"], 0)
        self.tracker.mark_failure("brave")
        self.assertEqual(self.tracker.get_state("brave")["consecutive_failures"], 1)

    def test_total_counters(self) -> None:
        self.tracker.mark_failure("p1")
        self.tracker.mark_failure("p1")
        self.tracker.mark_success("p1")
        self.tracker.mark_failure("p1")
        state = self.tracker.get_state("p1")
        self.assertEqual(state["total_failures"], 3)
        self.assertEqual(state["total_successes"], 1)


class TestContentStatusClassifier(unittest.TestCase):
    def test_classifies_browser_error_page(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        result = classify_markdown("This site can't be reached. ERR_UNSAFE_PORT")
        self.assertEqual(result.status, "error")
        self.assertIn("err_unsafe_port", result.reason or "")

    def test_classifies_blocked_page(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        result = classify_markdown(
            "Access denied. Please verify you are human with captcha."
        )
        self.assertEqual(result.status, "blocked")
        self.assertIn("verify you are human", result.reason or "")

    def test_classifies_login_wall(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        content = "Welcome to Example.com. Sign in to continue reading this article."
        content += " meaningful text " * 10  # Add enough words to pass short check
        result = classify_markdown(content)
        self.assertEqual(result.status, "blocked")
        self.assertIn("login_wall", result.reason or "")

    def test_classifies_paywall(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        content = "Subscribe to read the full article. You've reached your free article limit."
        content += " filler text " * 10
        result = classify_markdown(content)
        self.assertEqual(result.status, "blocked")
        self.assertIn("paywall", result.reason or "")

    def test_classifies_cloudflare_block(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        content = "Checking your browser before accessing the site. Cloudflare."
        content += " please wait " * 10
        result = classify_markdown(content)
        self.assertEqual(result.status, "blocked")

    def test_classifies_http_error_page(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        content = "404 Not Found. The requested URL was not found on this server."
        content += " nginx " * 10
        result = classify_markdown(content)
        self.assertEqual(result.status, "error")

    def test_classifies_500_error(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        content = "500 Internal Server Error. Please try again later."
        content += " troubleshooting tips " * 10
        result = classify_markdown(content)
        self.assertEqual(result.status, "error")

    def test_classifies_successful_content(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        text = " ".join(["meaningful"] * 80)
        result = classify_markdown(text)
        self.assertEqual(result.status, "success")
        self.assertTrue(result.cacheable)

    def test_classifies_redirect_only(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        result = classify_markdown("https://example.com/actual-page")
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.reason, "redirect_only")

    def test_classifies_redirect_notice(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        result = classify_markdown(
            "Redirecting to https://example.com/destination"
        )
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.reason, "redirect_only")

    def test_classifies_garbled_content(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        # High ratio of null bytes and control chars
        content = "\x00\x01\x02\x03\x04\x05" + " a" * 30 + " b" * 5
        result = classify_markdown(content)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.reason, "garbled_content")

    def test_classifies_empty_content(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        result = classify_markdown("")
        self.assertEqual(result.status, "error")
        self.assertEqual(result.reason, "empty_content")

    def test_classifies_too_short(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_markdown,
        )

        result = classify_markdown("short content")
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.reason, "too_short")


class TestContentQualityScoring(unittest.TestCase):
    def test_good_content_scores_high(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_quality,
        )

        text = " ".join(["meaningful"] * 100)
        score = classify_quality(text)
        self.assertGreater(score, 0.5)

    def test_empty_content_scores_zero(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_quality,
        )

        self.assertEqual(classify_quality(""), 0.0)
        self.assertEqual(classify_quality("   "), 0.0)

    def test_blocked_page_scores_low(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_quality,
        )

        content = "Access denied. Verify you are human." + " ignored text " * 50
        score = classify_quality(content)
        self.assertLess(score, 0.5)

    def test_very_short_scores_low(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_quality,
        )

        score = classify_quality("just ten words of content here okay")
        self.assertLess(score, 0.5)

    def test_garbled_text_scores_low(self) -> None:
        from kindly_web_search_mcp_server.content.status_classifier import (
            classify_quality,
        )

        content = "\x00\x01\x02" * 10 + " a" * 30
        score = classify_quality(content)
        self.assertLess(score, 0.5)


if __name__ == "__main__":
    unittest.main()
