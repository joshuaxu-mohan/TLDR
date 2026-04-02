"""
Test which podcast shows provide their own transcripts (free on Taddy).

Uses the batch getLatestPodcastEpisodes call (which worked in the coverage
test), then checks transcript content. Falls back to individual queries
if the batch call fails.

Usage:
    python scripts/test_taddy_transcripts.py

Requires: TADDY_USER_ID and TADDY_API_KEY in .env
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

COVERAGE_FILE = "scripts/taddy_coverage.json"


def taddy_query(query: str) -> dict:
    """Send a GraphQL query to Taddy API. Logs error body on failure."""
    headers = {
        "Content-Type": "application/json",
        "X-USER-ID": TADDY_USER_ID,
        "X-API-KEY": TADDY_API_KEY,
    }
    resp = requests.post(TADDY_URL, json={"query": query}, headers=headers, timeout=30)
    if resp.status_code != 200:
        print(f"\n    API error {resp.status_code}: {resp.text[:300]}")
        return {"errors": [{"message": f"HTTP {resp.status_code}"}]}
    return resp.json()


def test_single_show(series_uuid: str) -> dict:
    """Test transcript access for a single show's latest episode."""
    query = f"""
    {{
      getPodcastSeries(uuid: "{series_uuid}") {{
        uuid
        name
        episodes(limitPerPage: 1) {{
          uuid
          name
          audioUrl
          taddyTranscribeStatus
          transcript
        }}
      }}
    }}
    """
    result = taddy_query(query)
    series = result.get("data", {}).get("getPodcastSeries")
    if not series or not series.get("episodes"):
        return None

    ep = series["episodes"][0]
    ep["podcastSeries"] = {"uuid": series["uuid"], "name": series["name"]}
    return ep


def main():
    if not TADDY_USER_ID or not TADDY_API_KEY:
        print("ERROR: Set TADDY_USER_ID and TADDY_API_KEY in your .env file")
        return

    if not os.path.exists(COVERAGE_FILE):
        print(f"ERROR: Run test_taddy_coverage.py first to generate {COVERAGE_FILE}")
        return

    with open(COVERAGE_FILE) as f:
        coverage = json.load(f)

    shows_with_uuid = [s for s in coverage if s.get("uuid")]
    print(f"Testing transcript access for {len(shows_with_uuid)} shows...\n")

    results = []
    for i, show in enumerate(shows_with_uuid):
        name = show["search_term"]
        status = show["transcript_status"]
        print(f"  [{i+1}/{len(shows_with_uuid)}] {name} ({status})...", end=" ", flush=True)

        ep = test_single_show(show["uuid"])
        if not ep:
            print("NO EPISODES")
            results.append({
                "show": name,
                "taddy_status": status,
                "free_transcript": False,
                "word_count": None,
                "has_audio": False,
            })
            time.sleep(1)
            continue

        transcript = ep.get("transcript", [])
        has_transcript = bool(transcript and len(transcript) > 0 and transcript[0])

        word_count = None
        if has_transcript:
            word_count = sum(len(t.split()) for t in transcript if t)

        label = f"FREE ({word_count} words)" if has_transcript else "PAID/NONE"
        print(label)

        results.append({
            "show": name,
            "taddy_status": status,
            "free_transcript": has_transcript,
            "word_count": word_count,
            "has_audio": bool(ep.get("audioUrl")),
        })
        time.sleep(1)  # Conservative rate limiting

    # Print summary table
    print("\n" + "=" * 90)
    print(f"{'Show':<40} {'Taddy Status':<20} {'Free?':<8} {'Words':<10} {'Audio?'}")
    print("-" * 90)

    free_count = 0
    for r in results:
        show = r["show"][:38]
        status = r["taddy_status"]
        free = "YES" if r["free_transcript"] else "NO"
        words = str(r["word_count"]) if r["word_count"] else "-"
        audio = "YES" if r["has_audio"] else "NO"
        print(f"{show:<40} {status:<20} {free:<8} {words:<10} {audio}")
        if r["free_transcript"]:
            free_count += 1

    print("=" * 90)
    print(f"\nSummary:")
    print(f"  Shows tested:          {len(results)}")
    print(f"  FREE transcripts:      {free_count}  (podcast-provided, free tier)")
    print(f"  Need Whisper:          {len(results) - free_count}  (use audioUrl from Taddy)")
    print(f"  With audio URL:        {sum(1 for r in results if r['has_audio'])}")

    output_path = "scripts/taddy_transcript_access.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nFull results saved to {output_path}")


if __name__ == "__main__":
    main()