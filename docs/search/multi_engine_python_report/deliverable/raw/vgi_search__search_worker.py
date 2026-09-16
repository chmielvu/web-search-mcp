# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python[http]>=0.16.0",
#     "httpx>=0.27",
# ]
# ///
"""Repo-root stdio entry for the search worker (PEP 723 inline deps).

The worker itself -- the ``search`` catalog, the :class:`SearchWorker` class, and
``main()`` -- lives in the wheel-importable :mod:`vgi_search.worker` module so the
built distribution contains a runnable worker. This file is a thin shim that
re-exports them and runs ``main()`` under ``uv run search_worker.py`` (the command
the Makefile, ``ci/run-integration.sh``, ``scripts/mock_worker.py``, and the tests
still spawn unchanged).

Usage:
    uv run search_worker.py              # serve over stdio (DuckDB subprocess)
    python serve.py --port 8000          # serve over HTTP

    INSTALL vgi FROM community; LOAD vgi;
    ATTACH 'search' (TYPE vgi, LOCATION 'uv run search_worker.py');
"""

from __future__ import annotations

from vgi_search.worker import SearchWorker, main

__all__ = ["SearchWorker", "main"]


if __name__ == "__main__":
    main()
