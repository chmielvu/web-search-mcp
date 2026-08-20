"""Prefetch the approved Tree-sitter grammars for offline server runtime."""

from __future__ import annotations

import argparse

from kindly_web_search_mcp_server.tools.code_search.tree_sitter_evidence import (
    prefetch_required_languages,
    required_languages,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--language",
        action="append",
        dest="languages",
        help="Grammar to prefetch; repeat the flag. Defaults to the approved set.",
    )
    args = parser.parse_args()
    languages = tuple(args.languages or required_languages())
    selected = prefetch_required_languages(languages)
    print("Prefetched Tree-sitter grammars: " + ", ".join(selected))


if __name__ == "__main__":
    main()
