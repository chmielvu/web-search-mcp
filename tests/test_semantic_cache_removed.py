"""Test that semantic/LanceDB cache has been fully removed (Phase 5.3).

Written first (failing) per plan. Asserts that runtime imports of the
removed symbols and the lancedb package itself do not occur from our code.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestSemanticCacheRemoved(unittest.TestCase):
    def test_no_runtime_import_of_lancedb(self) -> None:
        """Our package must not cause 'lancedb' to be imported at runtime."""
        before = set(sys.modules.keys())
        # Import main entry points that previously pulled in semantic/lancedb
        import kindly_web_search_mcp_server.server  # noqa: F401
        import kindly_web_search_mcp_server.settings  # noqa: F401
        import kindly_web_search_mcp_server.cache  # noqa: F401

        after = set(sys.modules.keys())
        newly_loaded = after - before
        self.assertNotIn(
            "lancedb",
            newly_loaded,
            "lancedb was imported by our runtime code (should have been removed in 5.3)",
        )

    def test_removed_cache_symbols_cannot_be_imported(self) -> None:
        """Direct imports of deleted semantic symbols must fail."""
        with self.assertRaises(ImportError):
            pass  # type: ignore[attr-defined]

        with self.assertRaises(ImportError):
            pass

        with self.assertRaises(ImportError):
            pass

        # Also via top level cache
        with self.assertRaises(AttributeError):
            import kindly_web_search_mcp_server.cache as c  # noqa: F401

            _ = c.SemanticCacheStore

    def test_settings_no_longer_expose_semantic_or_lancedb_keys(self) -> None:
        from kindly_web_search_mcp_server.settings import settings

        self.assertFalse(
            hasattr(settings, "lancedb_dir"),
            "KINDLY_LANCEDB_DIR / lancedb_dir setting must be removed",
        )
        self.assertFalse(
            hasattr(settings, "semantic_cache_enabled"),
            "semantic_cache_enabled setting must be removed",
        )
        self.assertFalse(
            hasattr(settings, "semantic_cache_min_score"),
            "semantic_cache_min_score setting must be removed",
        )

    def test_server_module_has_no_semantic_cache_references(self) -> None:
        """After removal, server.py source should not mention the old cache paths."""
        import kindly_web_search_mcp_server.server as srv_mod

        src = Path(srv_mod.__file__).read_text(encoding="utf-8")
        forbidden = [
            "SemanticCacheStore",
            "get_semantic_cache",
            "set_semantic_cache",
            "semantic_cache_enabled",
            "lancedb_dir",
            "_get_cache_store",
            "_CACHE_STORE",
        ]
        for token in forbidden:
            self.assertNotIn(
                token,
                src,
                f"server.py still contains removed semantic cache token: {token}",
            )


if __name__ == "__main__":
    unittest.main()
