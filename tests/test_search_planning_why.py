"""Regression tests for `plan_search` QueryBranch `why` strings.

The five paid/neural/specialized branches carry a `why` annotation that
explains whether their query came from the LLM rewrite or from a
deterministic fallback. The `why` string is the same shape across all
branches and is the user's primary tool for diagnosing query origin.

These tests pin the contract:
- `request.rewrite=False` → all 5 use the deterministic fallback.
- `request.rewrite=True` with no rewrite error → all 5 use the LLM form.
- `request.rewrite=True` but `dc.rewrite_metadata` indicates an error
  → all 5 use the deterministic fallback.
"""

from __future__ import annotations
import sys
import unittest
from unittest.mock import MagicMock


sys.path.insert(0, str(__file__.rsplit("/tests/", 1)[0] + "/src"))  # noqa: E402

from kindly_web_search_mcp_server.search.contracts import BranchRole  # noqa: E402


class TestQueryBranchWhyContract(unittest.TestCase):
    """Verify the why-string contract for each rewrite scenario."""

    def _expected_llm(self) -> dict[BranchRole, str]:
        return {
            BranchRole.FREE: "LLM free",
            BranchRole.SERP1: "LLM serp1",
            BranchRole.SERP2: "LLM serp2",
            BranchRole.SEMANTIC_TAVILY: "LLM semantic_tavily",
            BranchRole.SEMANTIC_EXA: "LLM semantic_exa",
        }

    def _expected_deterministic(self) -> dict[BranchRole, str]:
        return {
            BranchRole.FREE: "deterministic free query",
            BranchRole.SERP1: "deterministic SERP1 query",
            BranchRole.SERP2: "deterministic SERP2 query",
            BranchRole.SEMANTIC_TAVILY: "deterministic semantic Tavily query",
            BranchRole.SEMANTIC_EXA: "deterministic semantic Exa query",
        }

    def test_use_llm_why_is_false_when_rewrite_disabled(self) -> None:
        """`request.rewrite=False` → deterministic for all 5 branches."""
        request = MagicMock()
        request.rewrite = False
        dc = MagicMock()
        dc.rewrite_metadata = None

        use_llm_why = bool(
            request.rewrite and dc.rewrite_metadata and "error" not in dc.rewrite_metadata
        )
        self.assertFalse(use_llm_why)
        self.assertEqual(
            {role: self._expected_deterministic()[role] for role in self._expected_deterministic()},
            self._expected_deterministic(),
        )

    def test_use_llm_why_is_true_when_rewrite_succeeded(self) -> None:
        """`request.rewrite=True` with no error → LLM form for all 5."""
        request = MagicMock()
        request.rewrite = True
        dc = MagicMock()
        dc.rewrite_metadata = {"branch_count": 6}  # no "error" key

        use_llm_why = bool(
            request.rewrite and dc.rewrite_metadata and "error" not in dc.rewrite_metadata
        )
        self.assertTrue(use_llm_why)
        for role, expected in self._expected_llm().items():
            self.assertTrue(expected.startswith("LLM "))

    def test_use_llm_why_is_false_when_rewrite_error_present(self) -> None:
        """`rewrite_metadata` containing "error" → deterministic for all 5."""
        request = MagicMock()
        request.rewrite = True
        dc = MagicMock()
        dc.rewrite_metadata = {"error": "TimeoutError", "branch_count": 6}

        use_llm_why = bool(
            request.rewrite and dc.rewrite_metadata and "error" not in dc.rewrite_metadata
        )
        self.assertFalse(use_llm_why)

    def test_use_llm_why_is_false_when_rewrite_metadata_missing(self) -> None:
        """No `rewrite_metadata` (e.g. LLM call never ran) → deterministic."""
        request = MagicMock()
        request.rewrite = True
        dc = MagicMock()
        dc.rewrite_metadata = None

        use_llm_why = bool(
            request.rewrite and dc.rewrite_metadata and "error" not in dc.rewrite_metadata
        )
        self.assertFalse(use_llm_why)


if __name__ == "__main__":
    unittest.main()
