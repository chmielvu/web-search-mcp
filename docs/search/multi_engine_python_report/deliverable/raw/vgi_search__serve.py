# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python[http,oauth]>=0.16.0",
#     "httpx>=0.27",
# ]
# ///
"""HTTP entrypoint for the search worker.

Forces the worker's CLI into HTTP mode (``Worker.main()`` serves stdio by
default) so callers only pass ``--host``/``--port``.
"""

import sys

from search_worker import SearchWorker

if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--http" not in argv:
        argv = ["--http", *argv]
    sys.argv = [sys.argv[0], *argv]
    SearchWorker.main()
