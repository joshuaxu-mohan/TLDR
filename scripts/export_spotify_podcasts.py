"""
One-off script: export Spotify followed podcasts to sources.yaml format.

Usage
-----
    python scripts/export_spotify_podcasts.py

    # Redirect YAML to a file for later diffing:
    python scripts/export_spotify_podcasts.py > scripts/spotify_podcasts.yaml

Output
------
  stdout                     — YAML block ready to paste into src/config/sources.yaml
  scripts/spotify_export.csv — Full export with RSS URLs and iTunes match confidence
                               scores for review before pasting.

Spotify app setup (one-time)
----------------------------
  1. Go to https://developer.spotify.com/dashboard and create an application.
  2. In the app settings, add  http://127.0.0.1:8888/callback  as a Redirect URI.
  3. Add to .env:
       SPOTIFY_CLIENT_ID=<your-client-id>
       SPOTIFY_CLIENT_SECRET=<your-client-secret>

The OAuth token is cached at scripts/.spotify_cache so you only authorise once.
That file is in .gitignore — do not commit it.

iTunes RSS lookup
-----------------
Each show name is searched via the iTunes Search API to find its RSS feed URL.
Results are matched by name similarity (difflib).  Matches below 70% confidence
are flagged with a warning comment in the YAML output.  Review the CSV for any
shows that got a blank or low-confidence feed URL and fill them in manually.

Dependencies
------------
    pip install spotipy          (or: pip install -r requirements.txt)
"""

import csv
import os
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")

SPOTIFY_CLIENT_ID: str = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET: str = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SPOTIFY_SCOPE = "user-library-read"

# spotipy writes a token here; .gitignore already excludes this path
_CACHE_PATH = Path(__file__).parent / ".spotify_cache"

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_RESULT_LIMIT = 5
ITUNES_DELAY_SECONDS = 1.0    # polite delay between iTunes requests

NAME_MATCH_THRESHOLD = 0.70   # minimum similarity for a "confident" RSS match

CSV_PATH = Path(__file__).parent / "spotify_export.csv"
CSV_COLUMNS = ["name", "publisher", "description", "rss_url", "itunes_match_confidence"]

_DESCRIPTION_MAX_CHARS = 200  # truncate descriptions in the CSV


# ---------------------------------------------------------------------------
# Spotify
# ---------------------------------------------------------------------------

def _authenticate_spotify():
    """
    Run the Spotify OAuth flow and return an authenticated Spotify client.

    On first run this opens a browser tab (or prints a URL) for you to
    authorise the app.  The token is cached at scripts/.spotify_cache so
    subsequent runs skip the browser step.
    """
    try:
        import spotipy
        from spotipy.cache_handler import CacheFileHandler
        from spotipy.oauth2 import SpotifyOAuth
    except ImportError as exc:
        raise SystemExit(
            "spotipy is not installed. Run:\n  pip install spotipy"
        ) from exc

    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        raise SystemExit(
            "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET are not set.\n"
            "Add them to .env — see the docstring at the top of this script."
        )

    try:
        auth_manager = SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            scope=SPOTIFY_SCOPE,
            cache_handler=CacheFileHandler(cache_path=str(_CACHE_PATH)),
        )
        sp = spotipy.Spotify(auth_manager=auth_manager)
        # Trigger the auth flow and fail fast if credentials are wrong
        sp.current_user()
        return sp
    except Exception as exc:
        raise SystemExit(f"Spotify authentication failed: {exc}") from exc


def _fetch_all_shows(sp) -> list[dict]:
    """
    Paginate through /me/shows and return every followed show object.

    The Spotify API returns up to 50 shows per page; we follow the `next`
    cursor until there are no more pages.
    """
    shows: list[dict] = []
    page = sp.current_user_saved_shows(limit=50)

    while page:
        for item in page.get("items", []):
            if item and item.get("show"):
                shows.append(item["show"])

        next_url = page.get("next")
        if next_url:
            page = sp.next(page)
        else:
            break

    return shows


# ---------------------------------------------------------------------------
# iTunes RSS lookup
# ---------------------------------------------------------------------------

def _name_similarity(a: str, b: str) -> float:
    """Return a 0–1 similarity score between two strings (case-insensitive)."""
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _lookup_itunes_rss(show_name: str) -> tuple[str, float]:
    """
    Search the iTunes Search API for a podcast by name and return its RSS feed URL.

    Returns (feed_url, confidence) where confidence is the name similarity of
    the best matching result.  Returns ("", 0.0) when no results are found or
    the request fails.
    """
    try:
        response = requests.get(
            ITUNES_SEARCH_URL,
            params={"term": show_name, "media": "podcast", "limit": ITUNES_RESULT_LIMIT},
            timeout=10,
            headers={"User-Agent": "my-daily-digest/1.0 (podcast export script)"},
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as exc:
        _warn(f"iTunes request failed for {show_name!r}: {exc}")
        return "", 0.0
    except (ValueError, KeyError) as exc:
        _warn(f"iTunes response parse error for {show_name!r}: {exc}")
        return "", 0.0

    results = data.get("results", [])
    if not results:
        return "", 0.0

    best_url = ""
    best_score = 0.0

    for result in results:
        feed_url: str = result.get("feedUrl", "").strip()
        itunes_name: str = result.get("collectionName", "")
        if not feed_url:
            continue
        score = _name_similarity(show_name, itunes_name)
        if score > best_score:
            best_score = score
            best_url = feed_url

    return best_url, best_score


# ---------------------------------------------------------------------------
# YAML formatting
# ---------------------------------------------------------------------------

def _yaml_scalar(value: str) -> str:
    """
    Return value as a YAML scalar string, quoting only when necessary.

    Podcast names can contain colons, apostrophes, and other YAML-special
    characters.  We use single-quote style when needed and escape inner
    single quotes by doubling them.
    """
    if not value:
        return "''"
    # Characters that force quoting in plain YAML scalars
    _SPECIAL = set(":{}[]|>&*!,#?'\"\\@`")
    if any(ch in _SPECIAL for ch in value) or value[0] in "-?:,[]{}#&*!|>'\"%@`":
        return "'" + value.replace("'", "''") + "'"
    return value


def _format_yaml_entry(
    name: str,
    publisher: str,
    rss_url: str,
    confidence: float,
) -> str:
    """Build a single podcast entry for sources.yaml (indented, comment-annotated)."""
    if rss_url and confidence < NAME_MATCH_THRESHOLD:
        feed_suffix = f"  # WARNING: low-confidence iTunes match ({confidence:.0%}) — verify manually"
    elif not rss_url:
        feed_suffix = "  # TODO: could not find feed URL — add manually"
    else:
        feed_suffix = ""

    lines = []
    if publisher:
        lines.append(f"  # Publisher: {publisher}")
    lines += [
        f"  - name: {_yaml_scalar(name)}",
        f"    feed_url: {_yaml_scalar(rss_url)}{feed_suffix}",
        f"    transcript_tier: unknown    # TODO: set to website | rss | whisper",
        f"    whisper_model: base         # only relevant when tier is whisper",
        f"    default_topics: []          # TODO: fill in e.g. [Tech, AI]",
        f"    enabled: false              # set to true once feed_url is confirmed",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _warn(message: str) -> None:
    print(f"  [WARNING] {message}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Authenticating with Spotify...", file=sys.stderr)
    sp = _authenticate_spotify()

    print("Fetching followed shows...", file=sys.stderr)
    shows = _fetch_all_shows(sp)

    if not shows:
        print(
            "No followed shows found.\n"
            "Follow some podcasts in Spotify (Library → Podcasts) and try again.",
            file=sys.stderr,
        )
        return

    print(f"Found {len(shows)} followed show(s).\n", file=sys.stderr)
    print("Looking up RSS feeds via iTunes Search API...", file=sys.stderr)

    rows: list[dict] = []

    for i, show in enumerate(shows, start=1):
        name: str = (show.get("name") or "").strip()
        publisher: str = (show.get("publisher") or "").strip()
        description: str = (show.get("description") or "").strip()
        description_short = description[:_DESCRIPTION_MAX_CHARS].replace("\n", " ")

        print(f"  [{i:>3}/{len(shows)}] {name}", file=sys.stderr)

        rss_url, confidence = _lookup_itunes_rss(name)

        if not rss_url:
            _warn(f"No iTunes match found for: {name!r}")
        elif confidence < NAME_MATCH_THRESHOLD:
            _warn(f"Low-confidence match ({confidence:.0%}) for: {name!r} — verify feed URL")

        rows.append({
            "name": name,
            "publisher": publisher,
            "description": description_short,
            "rss_url": rss_url,
            "itunes_match_confidence": f"{confidence:.2f}",
        })

        # Rate-limit iTunes requests — skip the delay after the last item
        if i < len(shows):
            time.sleep(ITUNES_DELAY_SECONDS)

    # -----------------------------------------------------------------------
    # Output (a): YAML block to stdout
    # -----------------------------------------------------------------------
    print("\npodcasts:")
    for row in rows:
        print(
            _format_yaml_entry(
                name=row["name"],
                publisher=row["publisher"],
                rss_url=row["rss_url"],
                confidence=float(row["itunes_match_confidence"]),
            )
        )
        print()  # blank line between entries

    sys.stdout.flush()

    # -----------------------------------------------------------------------
    # Output (b): CSV for review
    # -----------------------------------------------------------------------
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    # Summary stats to stderr so they don't pollute the YAML stdout
    matched = sum(1 for r in rows if r["rss_url"])
    high_conf = sum(
        1 for r in rows
        if r["rss_url"] and float(r["itunes_match_confidence"]) >= NAME_MATCH_THRESHOLD
    )
    low_conf = matched - high_conf
    unmatched = len(rows) - matched

    print("", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    print(f"Total shows:              {len(rows)}", file=sys.stderr)
    print(f"RSS URL found:            {matched}", file=sys.stderr)
    print(f"  High confidence (≥70%): {high_conf}", file=sys.stderr)
    print(f"  Low confidence (<70%):  {low_conf}  ← verify these in the CSV", file=sys.stderr)
    print(f"No RSS URL found:         {unmatched}  ← add manually", file=sys.stderr)
    print(f"\nCSV saved to: {CSV_PATH}", file=sys.stderr)
    print("=" * 50, file=sys.stderr)


if __name__ == "__main__":
    main()
