from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.cache.schema import (
    get_semantic_cache_schema,
    get_semantic_cache_table_name,
)


class TestSemanticCacheSchema(unittest.TestCase):
    def test_table_name_follows_active_embedding_config(self) -> None:
        table_name = get_semantic_cache_table_name(
            model_name="ibm-granite/granite-embedding-97m-multilingual-r2",
            embedding_dim=384,
        )
        self.assertEqual(
            table_name,
            "semantic_cache_ibm_granite_granite_embedding_97m_multilingual_r2_384",
        )

    def test_schema_uses_requested_embedding_dimension(self) -> None:
        schema = get_semantic_cache_schema(embedding_dim=384)
        embedding_field = schema.field("embedding")
        self.assertEqual(embedding_field.type.list_size, 384)


if __name__ == "__main__":
    unittest.main()
