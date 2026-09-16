#!/usr/bin/env python3
"""
Multi-Search Aggregator — CLI Entry
====================================
用法:
    python run_search.py "RISC-V vector extension"
    python run_search.py "LDPC decoder" --engines zhipu,arxiv --top 10
    python run_search.py "machine learning" --timeout 20 --json
    python run_search.py "Python async" --json > results.json
"""

import argparse, json, os, sys

# Load .env before importing search_agg
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def main():
    parser = argparse.ArgumentParser(
        description="Multi-Search Aggregator — 30+ engines concurrent search",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_search.py "RISC-V vector extension"
  python run_search.py "GPT-4 architecture" --engines zhipu,arxiv,google --top 20
  python run_search.py "climate change" --timeout 60 --json > results.json
  python run_search.py --list-engines
        """,
    )
    parser.add_argument(
        "query",
        nargs="*",
        help="Search query string",
    )
    parser.add_argument(
        "--engines",
        type=str,
        default=None,
        help="Comma-separated engine IDs to use (e.g. 'zhipu_pro,zai,oc_arxiv'). "
             "Use --list-engines to see available engines.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=50,
        help="Number of top results to return (default: 50)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Per-engine timeout in seconds (default: from config.yaml, 35s)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        dest="json_output",
        help="Output results as JSON to stdout instead of human-readable format",
    )
    parser.add_argument(
        "--list-engines",
        action="store_true",
        default=False,
        help="List all available engines and exit",
    )

    args = parser.parse_args()

    if args.list_engines:
        from search_agg import ENGINES
        from search_agg.config import WEIGHTS
        print(f"\n{'='*60}")
        print(f"Available Engines ({len(ENGINES)} total)")
        print(f"{'='*60}\n")
        types = {}
        for e in ENGINES:
            t = e["type"]
            if t not in types:
                types[t] = []
            types[t].append(e)
        for t, engines in sorted(types.items()):
            print(f"[{t}]")
            for e in engines:
                w = WEIGHTS.get(e["name"], 5)
                retry = " 🔄" if e.get("retry") else ""
                print(f"  {e['id']:22s} {e['name']:20s} weight={w}{retry}")
        return 0

    query = " ".join(args.query).strip()
    if not query:
        print("Error: No query provided. Use --help for usage.", file=sys.stderr)
        return 1

    engine_filter = None
    if args.engines:
        engine_filter = [e.strip() for e in args.engines.split(",") if e.strip()]

    try:
        from search_agg import aggregate
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from search_agg import aggregate

    if args.json_output:
        import logging
        logging.disable(logging.CRITICAL)
        result = aggregate(
            query,
            top_n=args.top,
            engine_filter=engine_filter,
            custom_timeout=args.timeout,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        result = aggregate(
            query,
            top_n=args.top,
            engine_filter=engine_filter,
            custom_timeout=args.timeout,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
