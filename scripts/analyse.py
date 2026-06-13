"""
Ad-hoc analysis over the digest database.

A convenience entry point showing that the podcast/newsletter corpus is now
query-able for analysis.  Uses only the stdlib sqlite3 driver, so it runs with
no extra dependencies.

For richer exploration, two zero-setup options work against the same file:

  Datasette — browsable UI, arbitrary SQL, facets, CSV/JSON export:
      pip install datasette
      datasette serve data/digest.db
      # then open the `v_articles` view or run SQL in the browser

  DuckDB — fast analytical SQL / pandas:
      import duckdb
      con = duckdb.connect()
      con.execute("ATTACH 'data/digest.db' AS d (TYPE sqlite)")
      con.execute("SELECT content_category, count(*) FROM d.v_articles GROUP BY 1").df()

Run:
    python -m scripts.analyse
    python -m scripts.analyse --search "inference scaling"
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path


def _connect() -> sqlite3.Connection:
    """Open the digest DB read-only-ish (honours DB_PATH, defaults to data/digest.db)."""
    path = os.environ.get("DB_PATH") or str(Path("data") / "digest.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _print_table(title: str, rows: list[sqlite3.Row], cols: list[str]) -> None:
    print(f"\n=== {title} ===")
    if not rows:
        print("  (no rows)")
        return
    widths = {c: max([len(c)] + [len(str(r[c])) for r in rows]) for c in cols}
    print("  " + "  ".join(c.ljust(widths[c]) for c in cols))
    print("  " + "  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  " + "  ".join(str(r[c]).ljust(widths[c]) for c in cols))


def category_split(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT content_category, COUNT(*) AS articles, SUM(is_transcribed) AS transcribed "
        "FROM v_articles GROUP BY content_category ORDER BY articles DESC"
    ).fetchall()
    _print_table("By content category", rows, ["content_category", "articles", "transcribed"])


def topic_distribution(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT topic, COUNT(*) AS articles FROM article_topics "
        "GROUP BY topic ORDER BY articles DESC"
    ).fetchall()
    _print_table("Articles per topic", rows, ["topic", "articles"])


def transcripts_by_source(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT source_name,
               COUNT(*)                                AS episodes,
               SUM(is_transcribed)                     AS transcribed,
               ROUND(AVG(NULLIF(word_count, 0)))       AS avg_words,
               ROUND(SUM(duration_seconds) / 3600.0, 1) AS audio_hours
        FROM v_articles
        WHERE source_type = 'podcast'
        GROUP BY source_name
        ORDER BY episodes DESC
        LIMIT 15
        """
    ).fetchall()
    _print_table(
        "Podcast coverage by source", rows,
        ["source_name", "episodes", "transcribed", "avg_words", "audio_hours"],
    )


def search(conn: sqlite3.Connection, query: str, limit: int = 10) -> None:
    match = " ".join('"' + t.replace('"', '""') + '"' for t in query.split() if t)
    try:
        rows = conn.execute(
            """
            SELECT s.name AS source_name, a.title
            FROM articles_fts f
            JOIN articles a ON a.id = f.rowid
            JOIN sources  s ON s.id = a.source_id
            WHERE articles_fts MATCH ?
            ORDER BY bm25(articles_fts)
            LIMIT ?
            """,
            (match, limit),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        print("Full-text search unavailable (FTS5 not in this SQLite build):", exc)
        return
    _print_table(f"Full-text search: {query!r}", rows, ["source_name", "title"])


def main() -> None:
    ap = argparse.ArgumentParser(description="Analyse the digest database")
    ap.add_argument("--search", metavar="QUERY", help="run a full-text search and exit")
    args = ap.parse_args()

    conn = _connect()
    try:
        if args.search:
            search(conn, args.search)
            return
        category_split(conn)
        topic_distribution(conn)
        transcripts_by_source(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
