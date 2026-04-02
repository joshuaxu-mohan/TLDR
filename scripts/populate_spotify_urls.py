"""
One-off script: populate the spotify_url column on podcast sources.

Strategy
--------
1. Read scripts/spotify_export.csv to get the shows the user already follows
   on Spotify (name + RSS URL).
2. For each podcast source in the database:
   a. Try to match it against the CSV by exact RSS URL — most reliable.
   b. If no RSS match, try fuzzy show-name matching (difflib, threshold 0.50).
3. For every matched source, call the Spotify Search API (client credentials,
   no user OAuth needed) to retrieve the canonical Spotify show URL.
4. Write spotify_url to the database via db.update_source().
5. Print a summary: matched, updated, unmatched.

Requirements
------------
- SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env
- spotipy installed (already in requirements.txt)
"""

import csv
import difflib
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Allow running as `python scripts/populate_spotify_urls.py` from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage import db  # noqa: E402 (import after sys.path fix)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

_CSV_PATH = Path(__file__).parent / "spotify_export.csv"
_SPOTIFY_SEARCH_DELAY = 0.25  # seconds between API calls — stay well under rate limits
_NAME_MATCH_THRESHOLD = 0.50  # difflib ratio; lower = more lenient


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def _load_csv() -> tuple[dict[str, dict], list[dict]]:
    """
    Return (rss_url_index, all_rows) from spotify_export.csv.

    rss_url_index maps rss_url → row dict for fast exact lookup.
    """
    if not _CSV_PATH.exists():
        print(f"ERROR: {_CSV_PATH} not found.")
        print("Run `python scripts/export_spotify_podcasts.py > /dev/null` first.")
        sys.exit(1)

    rows: list[dict] = []
    with _CSV_PATH.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append({k: (v or "").strip() for k, v in row.items()})

    rss_index = {r["rss_url"]: r for r in rows if r.get("rss_url")}
    return rss_index, rows


def _fuzzy_match(db_name: str, csv_rows: list[dict]) -> dict | None:
    """
    Return the CSV row whose name is the closest match to db_name, or None
    if the best ratio is below _NAME_MATCH_THRESHOLD.
    """
    csv_names = [r["name"] for r in csv_rows]
    matches = difflib.get_close_matches(
        db_name, csv_names, n=1, cutoff=_NAME_MATCH_THRESHOLD
    )
    if not matches:
        return None
    return next(r for r in csv_rows if r["name"] == matches[0])


# ---------------------------------------------------------------------------
# Spotify API helpers
# ---------------------------------------------------------------------------

def _make_spotify_client():
    """
    Create a Spotipy client using client credentials (no user OAuth).

    Raises SystemExit if credentials are missing.
    """
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
    except ImportError:
        print("ERROR: spotipy is not installed. Run: pip install spotipy")
        sys.exit(1)

    client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        print(
            "ERROR: SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set in .env"
        )
        sys.exit(1)

    return spotipy.Spotify(
        auth_manager=SpotifyClientCredentials(
            client_id=client_id,
            client_secret=client_secret,
        )
    )


def _search_spotify_show(sp, show_name: str) -> str | None:
    """
    Search Spotify for a show by name and return its canonical web URL.

    Returns None on no result or API error.
    """
    try:
        results = sp.search(q=show_name, type="show", limit=3, market="GB")
        items = results.get("shows", {}).get("items", []) or []
        if items:
            show_id = items[0]["id"]
            return f"https://open.spotify.com/show/{show_id}"
    except Exception as exc:
        print(f"    [Spotify search failed for {show_name!r}: {exc}]", flush=True)
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    db.init_db()

    rss_index, csv_rows = _load_csv()
    print(f"CSV: {len(csv_rows)} rows loaded from {_CSV_PATH.name}")

    sources = [s for s in db.get_all_sources() if s["type"] == "podcast"]
    print(f"DB:  {len(sources)} podcast sources\n")

    sp = _make_spotify_client()

    matched: list[tuple[str, str]] = []   # (source_name, spotify_url)
    unmatched: list[str] = []

    for source in sources:
        db_name: str = source["name"]
        db_rss: str = source["url"]
        source_id: int = source["id"]

        # Skip sources that already have a Spotify URL
        if source["spotify_url"]:
            print(f"  [skip] {db_name} — already set to {source['spotify_url']}")
            continue

        # 1. Exact RSS URL match
        csv_entry = rss_index.get(db_rss)
        match_method = "rss"

        # 2. Fuzzy name match fallback
        if csv_entry is None:
            csv_entry = _fuzzy_match(db_name, csv_rows)
            match_method = "name"

        if csv_entry is None:
            print(f"  [no CSV match] {db_name}")
            unmatched.append(db_name)
            continue

        # Use the CSV show name for the Spotify search (closer to Spotify's own naming)
        search_name = csv_entry["name"] or db_name
        print(f"  [{match_method}] {db_name!r} -> searching Spotify for {search_name!r} ...", end="", flush=True)

        time.sleep(_SPOTIFY_SEARCH_DELAY)
        spotify_url = _search_spotify_show(sp, search_name)

        if spotify_url is None:
            print(" not found on Spotify")
            unmatched.append(db_name)
            continue

        db.update_source(source_id, spotify_url=spotify_url)
        matched.append((db_name, spotify_url))
        print(f" ok: {spotify_url}")

    # --- Summary ---
    print(f"\n{'-' * 60}")
    print(f"Updated:   {len(matched)}")
    print(f"Unmatched: {len(unmatched)}")

    if unmatched:
        print("\nUnmatched sources (add spotify_url manually via PATCH /sources/{id}):")
        for name in unmatched:
            print(f"  - {name}")

    if matched:
        print("\nYAML snippet for sources.yaml (add to each matching podcast entry):")
        for name, url in matched:
            show_id = url.split("/")[-1]
            print(f"  # {name}")
            print(f"  spotify_url: {url}")


if __name__ == "__main__":
    main()
