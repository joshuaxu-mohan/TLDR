import requests, os, json, sqlite3
from dotenv import load_dotenv
load_dotenv()

headers = {
    "Content-Type": "application/json",
    "X-USER-ID": os.getenv("TADDY_USER_ID"),
    "X-API-KEY": os.getenv("TADDY_API_KEY"),
}

uuids = {
    "60fabbea-f51e-4c8b-82b4-1cbd57fe8c02": "The AI Daily Brief",
    "5c21ed4d-a754-420d-980c-1b5dc510e639": "Bloomberg Daybreak",
    "7740f569-b86e-4eb7-af87-25c7014738fd": "No Priors",
}

uuid_list = ", ".join(f'"{u}"' for u in uuids.keys())
query = "{getLatestPodcastEpisodes(uuids: [" + uuid_list + "]) { uuid name datePublished podcastSeries { uuid name } }}"
resp = requests.post("https://api.taddy.org", json={"query": query}, headers=headers)
eps = resp.json().get("data", {}).get("getLatestPodcastEpisodes", [])

from datetime import datetime, timezone
for ep in eps:
    ts = ep["datePublished"]
    dt = datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else None
    sname = ep["podcastSeries"]["name"]
    print(f"  [{sname}] {ep['name'][:60]} -- {dt}")

print("\nLatest in DB:")
conn = sqlite3.connect("data/digest.db")
for name in ["The AI Daily Brief", "Bloomberg Daybreak: Europe Edition", "No Priors"]:
    row = conn.execute("SELECT a.title, a.published_at FROM articles a JOIN sources s ON s.id = a.source_id WHERE s.name = ? ORDER BY a.published_at DESC LIMIT 1", (name,)).fetchone()
    if row:
        print(f"  [{name}] {row[0][:60]} -- {row[1]}")
    else:
        print(f"  [{name}] NO ARTICLES IN DB")
