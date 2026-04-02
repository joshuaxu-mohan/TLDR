"""One-off verification script - safe to delete after use."""
import sqlite3

conn = sqlite3.connect("data/digest.db")
conn.row_factory = sqlite3.Row

# Recent articles (last 48 hours)
print("=== RECENT ARTICLES (last 48h) ===")
rows = conn.execute(
    "SELECT a.title, s.name as source, a.published_at, "
    "CASE WHEN a.content IS NOT NULL AND length(a.content) > 200 "
    "THEN 'yes' ELSE 'no' END as has_transcript, "
    "CASE WHEN a.summary IS NOT NULL THEN 'yes' ELSE 'no' END as has_summary "
    "FROM articles a JOIN sources s ON a.source_id = s.id "
    "WHERE a.published_at >= datetime('now', '-2 days') "
    "ORDER BY a.published_at DESC"
).fetchall()
for r in rows:
    title = r["title"][:60] if r["title"] else "(no title)"
    print(f"  [{r['source']}] {title}  transcript={r['has_transcript']}  summary={r['has_summary']}")
print(f"Total: {len(rows)}")

# Colossus sources (last 7 days)
print("\n=== COLOSSUS SOURCES (last 7 days) ===")
rows = conn.execute(
    "SELECT a.title, a.published_at, "
    "CASE WHEN a.content IS NOT NULL AND length(a.content) > 200 "
    "THEN length(a.content) ELSE 0 END as content_len "
    "FROM articles a JOIN sources s ON a.source_id = s.id "
    "WHERE s.name IN ('Invest Like the Best', 'Business Breakdowns') "
    "AND a.published_at >= datetime('now', '-7 days') "
    "ORDER BY a.published_at DESC"
).fetchall()
for r in rows:
    title = r["title"][:60] if r["title"] else "(no title)"
    print(f"  {title}  content_chars={r['content_len']}")
if not rows:
    print("  (none found)")

# No Priors (last 7 days)
print("\n=== NO PRIORS (last 7 days) ===")
rows = conn.execute(
    "SELECT a.title, a.published_at, "
    "CASE WHEN a.content IS NOT NULL AND length(a.content) > 200 "
    "THEN length(a.content) ELSE 0 END as content_len "
    "FROM articles a JOIN sources s ON a.source_id = s.id "
    "WHERE s.name = 'No Priors' "
    "AND a.published_at >= datetime('now', '-7 days') "
    "ORDER BY a.published_at DESC"
).fetchall()
for r in rows:
    title = r["title"][:60] if r["title"] else "(no title)"
    print(f"  {title}  content_chars={r['content_len']}")
if not rows:
    print("  (none found)")

# Recent digests
print("\n=== RECENT DIGESTS ===")
rows = conn.execute(
    "SELECT id, category, generated_at, length(content) as len "
    "FROM digests "
    "WHERE generated_at >= datetime('now', '-3 days') "
    "ORDER BY generated_at DESC"
).fetchall()
for r in rows:
    print(f"  id={r['id']} cat={r['category']} at={r['generated_at']} chars={r['len']}")
if not rows:
    print("  (none found)")

# Check if transcript_tier is referenced anywhere meaningful
print("\n=== TRANSCRIPT_PRIORITY VALUES ===")
rows = conn.execute(
    "SELECT name, transcript_tier, transcript_priority, content_category, active "
    "FROM sources ORDER BY transcript_priority, name"
).fetchall()
for r in rows:
    print(f"  {r['name'][:35]:<35} tier={str(r['transcript_tier']):<12} "
          f"priority={str(r['transcript_priority']):<12} cat={str(r['content_category']):<12} active={r['active']}")

conn.close()