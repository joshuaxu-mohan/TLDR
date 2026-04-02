"""
Standalone test script for the RSS ingestor.

Run from the project root (Daily/) to verify that a feed parses correctly
before wiring it into the main pipeline.  Does NOT write to the database.

Usage
-----
# Test a specific feed URL:
python scripts/test_rss.py --url https://example.substack.com/feed

# Test all enabled Substack sources from sources.yaml:
python scripts/test_rss.py --all

# Limit output to the first N articles per feed:
python scripts/test_rss.py --url https://example.substack.com/feed --limit 3
"""

import argparse
import sys
from pathlib import Path

# Allow running from the project root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.loader import get_enabled_substacks
from src.ingestors.rss import fetch_feed


def _print_result(result, index: int) -> None:
    divider = "-" * 60
    status = "OK" if result.success else "FAIL"
    print(f"\n[{index}] {status} — {result.source_name}")
    print(divider)
    if not result.success:
        print(f"  Error: {result.error_message}")
        return
    print(f"  Title      : {result.title}")
    print(f"  URL        : {result.url}")
    print(f"  Published  : {result.published_at}")
    content_preview = result.content[:300].replace("\n", " ") if result.content else "(no content)"
    print(f"  Content    : {content_preview}{'...' if len(result.content or '') > 300 else ''}")
    print(f"  Word count : {len((result.content or '').split())}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test the RSS ingestor against a feed URL")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="RSS feed URL to test")
    group.add_argument("--all", action="store_true", help="Test all enabled sources from sources.yaml")
    parser.add_argument("--name", default="test-source", help="Source name label (used with --url)")
    parser.add_argument("--limit", type=int, default=0, help="Max articles to show per feed (0 = all)")
    args = parser.parse_args()

    feeds: list[tuple[str, str]] = []  # (name, feed_url)

    if args.all:
        sources = get_enabled_substacks()
        if not sources:
            print("No enabled Substack sources found in sources.yaml")
            sys.exit(1)
        for s in sources:
            feeds.append((s.get("name", "unknown"), s.get("feed_url", "")))
    else:
        feeds.append((args.name, args.url))

    total_ok = 0
    total_fail = 0

    for name, feed_url in feeds:
        if not feed_url:
            print(f"\n[SKIP] {name} — no feed_url")
            continue

        print(f"\n{'=' * 60}")
        print(f"Feed  : {feed_url}")
        print(f"Source: {name}")
        print(f"{'=' * 60}")

        results = fetch_feed(feed_url, name)
        if args.limit:
            results = results[: args.limit]

        for i, result in enumerate(results, start=1):
            _print_result(result, i)
            if result.success:
                total_ok += 1
            else:
                total_fail += 1

        print(f"\nSummary: {sum(1 for r in results if r.success)} ok, "
              f"{sum(1 for r in results if not r.success)} failed "
              f"({len(results)} total shown)")

    print(f"\n{'=' * 60}")
    print(f"Grand total: {total_ok} ok, {total_fail} failed")


if __name__ == "__main__":
    main()
