"""
Test Taddy API transcript coverage for all followed podcasts.

Usage:
    1. Set TADDY_USER_ID and TADDY_API_KEY in your .env file
    2. Run: python scripts/test_taddy_coverage.py

Output: a table showing each show's transcript status and audio URL availability.
"""

import os
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv()

TADDY_USER_ID = os.getenv("TADDY_USER_ID")
TADDY_API_KEY = os.getenv("TADDY_API_KEY")
TADDY_URL = "https://api.taddy.org"

# All 30 shows from your Spotify export
SHOWS = [
    "Bloomberg Tech",
    "Bloomberg Daybreak Europe Edition",
    "No Priors",
    "The MAD Podcast with Matt Turck",
    "The AI Daily Brief",
    "Forward Future Interviews",
    "Cheeky Pint",
    "Lex Fridman Podcast",
    "The Vergecast",
    "Dwarkesh Podcast",
    "ACCESS",
    "Invest Like the Best",
    "Another Podcast",
    "AI + a16z",
    "Stratechery",
    "Business Breakdowns",
    "In Good Company with Nicolai Tangen",
    "The a16z Show",
    "Money Stuff The Podcast",
    "Pivot",
    "Odd Lots",
    "Decoder with Nilay Patel",
    "The Twenty Minute VC",
    "Unhedged",
    "Behind the Money",
    "Prof G Markets",
    "The Markets Goldman Sachs",
    "Thoughts on the Market",
    "FT News Briefing",
    "Exchanges Goldman Sachs",
]


def taddy_query(query: str) -> dict:
    """Send a GraphQL query to Taddy API."""
    headers = {
        "Content-Type": "application/json",
        "X-USER-ID": TADDY_USER_ID,
        "X-API-KEY": TADDY_API_KEY,
    }
    resp = requests.post(TADDY_URL, json={"query": query}, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def search_show(name: str) -> dict | None:
    """Search Taddy for a podcast by name, return top result."""
    # Escape quotes in show names
    escaped = name.replace('"', '\\"')
    query = f"""
    {{
      search(term: "{escaped}", filterForTypes: PODCASTSERIES, limitPerPage: 3) {{
        searchId
        podcastSeries {{
          uuid
          name
          rssUrl
        }}
      }}
    }}
    """
    result = taddy_query(query)
    series_list = result.get("data", {}).get("search", {}).get("podcastSeries", [])
    if not series_list:
        return None
    # Return the first (most relevant) match
    return series_list[0]


def check_latest_episode(uuid: str) -> dict:
    """Get the latest episode for a podcast and check transcript status."""
    query = f"""
    {{
      getPodcastSeries(uuid: "{uuid}") {{
        uuid
        name
        episodes(limitPerPage: 1) {{
          uuid
          name
          datePublished
          audioUrl
          duration
          taddyTranscribeStatus
        }}
      }}
    }}
    """
    result = taddy_query(query)
    series = result.get("data", {}).get("getPodcastSeries", {})
    episodes = series.get("episodes", [])
    if not episodes:
        return {"episode": None, "status": "NO_EPISODES", "has_audio": False}
    ep = episodes[0]
    return {
        "episode": ep.get("name", "Unknown"),
        "status": ep.get("taddyTranscribeStatus", "UNKNOWN"),
        "has_audio": bool(ep.get("audioUrl")),
        "duration_mins": round(ep.get("duration", 0) / 60, 1) if ep.get("duration") else None,
    }


def main():
    if not TADDY_USER_ID or not TADDY_API_KEY:
        print("ERROR: Set TADDY_USER_ID and TADDY_API_KEY in your .env file")
        print("Get these from https://taddy.org/dashboard")
        return

    print(f"Testing Taddy coverage for {len(SHOWS)} shows...\n")

    results = []
    for i, show_name in enumerate(SHOWS):
        print(f"  [{i+1}/{len(SHOWS)}] Searching: {show_name}...", end=" ", flush=True)

        # Search for the show
        match = search_show(show_name)
        if not match:
            print("NOT FOUND")
            results.append({
                "search_term": show_name,
                "matched_name": "NOT FOUND",
                "uuid": None,
                "episode": None,
                "transcript_status": "N/A",
                "has_audio": False,
                "duration_mins": None,
            })
            time.sleep(0.5)  # Be polite to the API
            continue

        # Check latest episode
        ep_info = check_latest_episode(match["uuid"])
        print(f"{ep_info['status']}")

        results.append({
            "search_term": show_name,
            "matched_name": match["name"],
            "uuid": match["uuid"],
            "episode": ep_info["episode"],
            "transcript_status": ep_info["status"],
            "has_audio": ep_info["has_audio"],
            "duration_mins": ep_info["duration_mins"],
        })

        time.sleep(0.5)  # Rate limit: ~2 requests per show

    # Print summary table
    print("\n" + "=" * 100)
    print(f"{'Show':<40} {'Transcript':<20} {'Audio?':<8} {'Duration':<10} {'Matched Name'}")
    print("-" * 100)

    completed = 0
    has_audio = 0
    not_found = 0

    for r in results:
        status = r["transcript_status"]
        audio = "YES" if r["has_audio"] else "NO"
        dur = f"{r['duration_mins']}m" if r["duration_mins"] else "-"
        matched = r["matched_name"][:35] if r["matched_name"] else "-"

        # Truncate show name for display
        show = r["search_term"][:38]

        print(f"{show:<40} {status:<20} {audio:<8} {dur:<10} {matched}")

        if status == "COMPLETED":
            completed += 1
        if r["has_audio"]:
            has_audio += 1
        if r["matched_name"] == "NOT FOUND":
            not_found += 1

    print("=" * 100)
    print(f"\nSummary:")
    print(f"  Total shows:           {len(SHOWS)}")
    print(f"  Found on Taddy:        {len(SHOWS) - not_found}")
    print(f"  Not found:             {not_found}")
    print(f"  Transcript COMPLETED:  {completed}  (free transcripts available)")
    print(f"  Has audio URL:         {has_audio}  (can use Whisper as fallback)")
    print(f"  No audio URL:          {len(SHOWS) - has_audio}")

    # Save full results as JSON for reference
    output_path = "scripts/taddy_coverage.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nFull results saved to {output_path}")


if __name__ == "__main__":
    main()
