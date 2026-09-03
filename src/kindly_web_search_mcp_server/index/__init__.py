"""Web results index module — remote Qdrant on HF Space."""

from .bm25_encoder import encode_bm25
from .web_results_index import (
    COLLECTION_NAME,
    WebResultsIndex,
    get_web_results_index,
    index_final_results,
)

__all__ = [
    "COLLECTION_NAME",
    "encode_bm25",
    "WebResultsIndex",
    "get_web_results_index",
    "index_final_results",
]
