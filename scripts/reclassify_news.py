"""
One-time migration: set content_category = 'news' for the 7 news sources.
All other sources are set to 'informative' if not already set.

Run once: python scripts/reclassify_news.py
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "digest.db"

NEWS_SOURCES = {
    "bloomberg daybreak: europe edition",
    "bloomberg tech",
    "ft news briefing",
    "thoughts on the market",
    "prof g markets",
    "the ai daily brief",
    "the markets",
}


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT id, name, content_category FROM sources ORDER BY name")
    rows = cur.fetchall()

    for row in rows:
        desired = "news" if row["name"].lower().strip() in NEWS_SOURCES else "informative"
        cur.execute(
            "UPDATE sources SET content_category = ? WHERE id = ?",
            (desired, row["id"]),
        )
        old = row["content_category"] or "NULL"
        marker = " ← changed" if old != desired else ""
        print(f"  {row['name']!r:50s}  {old} → {desired}{marker}")

    conn.commit()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
