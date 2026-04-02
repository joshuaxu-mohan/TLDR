"""Check API responses for transcript/content issues."""
import sqlite3

conn = sqlite3.connect("data/digest.db")
conn.row_factory = sqlite3.Row

# Check the two newly transcribed episodes
print("=== RECENTLY TRANSCRIBED ===")
rows = conn.execute(
    "SELECT a.id, a.title, s.name, s.transcript_priority, "
    "length(a.content) as content_len, "
    "a.summary IS NOT NULL as has_summary, "
    "a.needs_transcription "
    "FROM articles a JOIN sources s ON a.source_id = s.id "
    "WHERE a.id IN (395, 410)"
).fetchall()
for r in rows:
    word_count = len(r["content_len"] and str(r["content_len"]) or "")
    print(f"  id={r['id']} [{r['name']}] content_chars={r['content_len']} "
          f"summary={r['has_summary']} priority={r['transcript_priority']} "
          f"needs_transcription={r['needs_transcription']}")

# Check Prof G - should be description tier
print("\n=== PROF G RECENT ===")
rows = conn.execute(
    "SELECT a.id, a.title, s.transcript_priority, "
    "length(a.content) as content_len, "
    "a.published_at "
    "FROM articles a JOIN sources s ON a.source_id = s.id "
    "WHERE s.name LIKE '%Prof G%' "
    "ORDER BY a.published_at DESC LIMIT 3"
).fetchall()
for r in rows:
    words = 0
    if r["content_len"]:
        raw = conn.execute("SELECT content FROM articles WHERE id=?", (r["id"],)).fetchone()
        if raw["content"]:
            words = len(raw["content"].split())
    print(f"  id={r['id']} priority={r['transcript_priority']} "
          f"content_chars={r['content_len']} words={words} "
          f"title={r['title'][:60]}")

# Check what has_content would return with 500-word threshold
print("\n=== ALWAYS-PRIORITY WITH TRANSCRIPTS ===")
rows = conn.execute(
    "SELECT a.id, s.name, length(a.content) as content_len, a.title "
    "FROM articles a JOIN sources s ON a.source_id = s.id "
    "WHERE s.transcript_priority = 'always' "
    "AND a.content IS NOT NULL AND length(a.content) > 0 "
    "ORDER BY a.published_at DESC LIMIT 10"
).fetchall()
for r in rows:
    raw = conn.execute("SELECT content FROM articles WHERE id=?", (r["id"],)).fetchone()
    words = len(raw["content"].split()) if raw["content"] else 0
    passes_500 = words >= 500
    print(f"  id={r['id']} [{r['name'][:25]}] chars={r['content_len']} "
          f"words={words} passes_500={passes_500} "
          f"title={r['title'][:50]}")

# Check Feed page date filtering
print("\n=== TODAY'S ARTICLES (what Feed page should show) ===")
rows = conn.execute(
    "SELECT a.id, s.name, a.title, a.published_at, a.summary IS NOT NULL as has_summary "
    "FROM articles a JOIN sources s ON a.source_id = s.id "
    "WHERE date(a.published_at) = date('now') "
    "ORDER BY a.published_at DESC LIMIT 10"
).fetchall()
for r in rows:
    print(f"  id={r['id']} [{r['name'][:25]}] summary={r['has_summary']} "
          f"pub={r['published_at']} title={r['title'][:50]}")

conn.close()