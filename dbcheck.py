"""One-off DB sanity check - safe to delete after use."""
import sqlite3

conn = sqlite3.connect("data/digest.db")
r = conn.execute("SELECT count(*) FROM articles WHERE summary IS NOT NULL").fetchone()
print(f"Summarised articles: {r[0]}")
r = conn.execute("SELECT count(*) FROM articles WHERE content IS NOT NULL AND length(content) > 200").fetchone()
print(f"Articles with transcripts: {r[0]}")
r = conn.execute("SELECT count(*) FROM digests WHERE category IN ('news','informative')").fetchone()
print(f"Valid digests: {r[0]}")
r = conn.execute("SELECT count(*) FROM digests WHERE category = 'all'").fetchone()
print(f"Stale digests (should be 0): {r[0]}")
cols = [row[1] for row in conn.execute("PRAGMA table_info(sources)").fetchall()]
print(f"transcript_tier column exists (should be False): {'transcript_tier' in cols}")
conn.close()