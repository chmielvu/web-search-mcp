"""Web results index module — remote Qdrant on HF Space."""

from .bm25_encoder import encode_bm25
from .web_results_index import WebResultsIndex, get_web_results_index

__all__ = [
    "encode_bm25",
    "WebResultsIndex",
    "get_web_results_index",
]
