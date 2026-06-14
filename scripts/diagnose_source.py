"""
Diagnose why a single source is or isn't ingesting.

Run on whichever host owns the LIVE database (e.g. the Oracle VM), since that is
where the real answer lives:

    python -m scripts.diagnose_source "20VC"
    python -m scripts.diagnose_source "Behind the Money"

Reports the source's config, its most recent articles and how stale they are,
and a live Taddy check: does the uuid resolve, does the returned show name match,
and is Taddy's current latest episode already in the DB or missing (a gap).
"""

from __future__ import annotations

import argparse
from datetime import datetime, UTC
from typing import Optional

from src.storage import db


def _age_hours(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return (datetime.now(UTC) - dt).total_seconds() / 3600.0


def _taddy_check(conn, name: str, uuid: str) -> None:
    from src.ingestors import taddy
    print("  --- live Taddy check ---")
    try:
        eps = taddy._batch_fetch_episodes([uuid])
    except Exception as exc:
        print(f"    Taddy fetch ERROR: {exc}")
        return
    if not eps:
        print("    Taddy returned NO episodes for this uuid (stale/invalid uuid?)")
        return
    tname = (eps[0].get("podcastSeries") or {}).get("name")
    print(f"    Taddy series name : {tname!r}  ({len(eps)} recent episodes)")
    if tname and name.lower() not in tname.lower() and tname.lower() not in name.lower():
        print(f"    !! NAME MISMATCH — uuid points to a different show than '{name}'")
    latest_url = eps[0].get("audioUrl")
    if latest_url:
        in_db = conn.execute("SELECT 1 FROM articles WHERE url = ?", (latest_url,)).fetchone()
        print(f"    Taddy's latest episode already in DB? {'yes' if in_db else 'NO  <-- ingestion gap'}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Diagnose ingestion for one source")
    ap.add_argument("name", help="Source name or substring to inspect")
    args = ap.parse_args()

    with db._connection() as conn:
        srcs = conn.execute(
            "SELECT * FROM sources WHERE name LIKE ?", (f"%{args.name}%",)
        ).fetchall()
        if not srcs:
            print(f"No source matches {args.name!r}")
            return

        for s in srcs:
            d = dict(s)
            print("=" * 72)
            print(f"Source: {d['name']}  (id={d['id']})")
            print(f"  active={d.get('active')}  type={d.get('type')}  "
                  f"priority={d.get('transcript_priority')}  category={d.get('content_category')}")
            print(f"  taddy_uuid={d.get('taddy_uuid')}")
            print(f"  url={d.get('url')}")

            total = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE source_id = ?", (d["id"],)
            ).fetchone()[0]
            arts = conn.execute(
                "SELECT title, published_at, ingested_at, length(content) AS clen "
                "FROM articles WHERE source_id = ? ORDER BY ingested_at DESC LIMIT 5",
                (d["id"],),
            ).fetchall()
            print(f"  articles: {total} total")
            if arts:
                age = _age_hours(arts[0]["ingested_at"])
                age_str = f" ({age:.1f}h ago)" if age is not None else ""
                print(f"  newest ingested: {arts[0]['ingested_at']}{age_str}")
                for a in arts:
                    print(f"     pub={str(a['published_at'])[:10]} "
                          f"ing={str(a['ingested_at'])[:19]} clen={a['clen']}  {a['title'][:48]}")
            else:
                print("  NO ARTICLES ingested for this source")

            if d.get("type") == "podcast" and d.get("taddy_uuid"):
                _taddy_check(conn, d["name"], d["taddy_uuid"])


if __name__ == "__main__":
    main()
