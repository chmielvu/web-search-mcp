"""Legacy observability row builders — removed in clean cutover.

``build_response_result_rows`` targeted the dropped ``web_search_response_results``
table. Response-level persistence is now handled by ``persist_search_outcome``
via the unified 9-table schema in ``writers/``.
"""

from __future__ import annotations
