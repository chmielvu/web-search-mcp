"""Seed the blocklist from local farm list + optional community subscriptions.

Usage:
    python scripts/seed_blocklist.py                 # local seeds only
    python scripts/seed_blocklist.py --with-subscriptions   # local + SSSS + HUGE-AI + Bad Websites

Local seed source: data/blocklist_seed_farms.txt (one domain per line, # comments).
Community subscriptions use import_subscription() from search.blocklist.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kindly_web_search_mcp_server.search.blocklist import (
    KNOWN_SUBSCRIPTIONS,
    add_blocklist_pattern,
    blocklist_stats,
    import_subscription,
)


def seed_local(path: Path) -> dict:
    added = 0
    skipped = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Accept bare domain or explicit glob; normalize to glob.
        glob_pat = line if line.startswith(("*://", "http://", "https://")) else f"*://*.{line}/*"
        if add_blocklist_pattern(glob_pat, source="farm-seed-local"):
            added += 1
        else:
            skipped += 1
    return added, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--with-ssss", action="store_true", help="also import Super SEO Spam Suppressor"
    )
    parser.add_argument(
        "--with-huge-ai", action="store_true", help="also import HUGE AI Blocklist"
    )
    parser.add_argument(
        "--with-bad-websites", action="store_true", help="also import Bad Website Blocklist"
    )
    parser.add_argument(
        "--all", action="store_true", help="import every known community subscription"
    )
    args = parser.parse_args()

    seed_file = Path(__file__).resolve().parent.parent / "data" / "blocklist_seed_farms.txt"
    added, skipped = seed_local(seed_file)
    print(f"local seeds: added={added} skipped={skipped}")

    subs_to_import = []
    if args.all:
        subs_to_import = list(KNOWN_SUBSCRIPTIONS)
    else:
        for flag, key in (
            ("with_ssss", "ssss"),
            ("with_huge_ai", "huge-ai"),
            ("with_bad_websites", "bad-websites"),
        ):
            if getattr(args, flag):
                subs_to_import.append(key)

    for key in subs_to_import:
        sub = KNOWN_SUBSCRIPTIONS[key]
        print(f"importing {sub['name']} …")
        try:
            stats = import_subscription(sub["url"], source=f"subscription:{key}", timeout=30.0)
            print(f"  {stats}")
        except Exception as exc:  # network/size errors must not kill the seed run
            print(f"  import failed: {exc}")

    print(f"blocklist stats: {blocklist_stats()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
