"""
SQLite storage layer for the digest pipeline.

All database access for the application goes through this module.
The DB file path is read from the DB_PATH environment variable,
defaulting to ./data/digest.db.
"""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, UTC
from pathlib import Path
from typing import Generator, Optional

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path("data") / "digest.db"


def _db_path() -> Path:
    """Resolve the DB file path from the environment, with a default fallback."""
    import os
    raw = os.environ.get("DB_PATH")
    return Path(raw) if raw else _DEFAULT_DB_PATH


@contextmanager
def _connection() -> Generator[sqlite3.Connection, None, None]:
    """
    Yield a SQLite connection with sensible defaults, then commit or roll back.

    row_factory lets callers treat rows as dicts via sqlite3.Row.
    WAL journal mode is set per-connection so concurrent reads do not block writes.
    foreign_keys enforcement is also set per-connection.
    """
    path = _db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

def _run_migrations(conn: sqlite3.Connection) -> None:
    """
    Apply additive schema migrations to an existing database.

    SQLite does not support ALTER TABLE ADD COLUMN IF NOT EXISTS, so each
    statement is attempted individually and OperationalError (column already
    exists) is silently ignored. New columns must always be nullable or have
    a DEFAULT so existing rows are valid.
    """
    migrations = [
        # Articles columns
        "ALTER TABLE articles ADD COLUMN needs_transcription INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE articles ADD COLUMN audio_url TEXT",
        "ALTER TABLE articles ADD COLUMN is_significant INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE articles ADD COLUMN topic_tags TEXT",
        "ALTER TABLE articles ADD COLUMN summarised_at TEXT",
        # Sources columns (added when moving config from yaml to DB)
        "ALTER TABLE sources ADD COLUMN default_topics TEXT",
        "ALTER TABLE sources ADD COLUMN description TEXT",
        "ALTER TABLE sources ADD COLUMN transcript_tier TEXT",
        "ALTER TABLE sources ADD COLUMN active INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE sources ADD COLUMN taddy_uuid TEXT",
        "ALTER TABLE sources ADD COLUMN spotify_url TEXT",
        "ALTER TABLE sources ADD COLUMN transcript_priority TEXT NOT NULL DEFAULT 'always'",
        "ALTER TABLE sources ADD COLUMN content_category TEXT NOT NULL DEFAULT 'informative'",
        "ALTER TABLE digests ADD COLUMN category TEXT NOT NULL DEFAULT 'all'",
        "ALTER TABLE articles ADD COLUMN extended_summary TEXT",
        # transcript_tier is superseded by transcript_priority; drop it.
        # SQLite 3.35.0+ supports DROP COLUMN; this project runs on 3.50.4.
        "ALTER TABLE sources DROP COLUMN transcript_tier",
        # Transcription audit log — records every Groq API call for budget tracking
        """CREATE TABLE IF NOT EXISTS transcription_log (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id     INTEGER NOT NULL,
            audio_seconds  REAL    NOT NULL,
            provider       TEXT    NOT NULL DEFAULT 'groq',
            transcribed_at TEXT    NOT NULL,
            FOREIGN KEY (article_id) REFERENCES articles(id)
        )""",
        # Gemini audit log — one row per successful API call for daily budget tracking
        """CREATE TABLE IF NOT EXISTS gemini_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            called_at TEXT    NOT NULL
        )""",
        # Pre-computed feed summary — generated at end of each pipeline run
        """CREATE TABLE IF NOT EXISTS feed_summaries (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            date         TEXT    NOT NULL,
            summary_json TEXT    NOT NULL,
            generated_at TEXT    NOT NULL
        )""",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already present — nothing to do

    # One-time migrations: rename legacy priority values.
    # 'description' was the old name for 'on_demand'.
    # 'none' has been retired — all sources are now either 'always', 'on_demand', or 'skip'.
    for old, new in (("description", "on_demand"), ("none", "on_demand")):
        try:
            conn.execute(
                f"UPDATE sources SET transcript_priority = '{new}' "
                f"WHERE transcript_priority = '{old}'"
            )
        except sqlite3.OperationalError:
            pass

    conn.execute(
        "UPDATE articles SET needs_transcription = 0 "
        "WHERE source_id IN (SELECT id FROM sources WHERE transcript_priority != 'always')"
    )

    # Remove legacy 'all'-category digests generated before the news/informative
    # split was introduced.  These rows are orphaned and no longer queried.
    conn.execute("DELETE FROM digests WHERE category = 'all'")

    # Backfill: give already-summarised articles an approximate summarised_at
    # using the most recent digest's generated_at.  The WHERE guard makes this
    # idempotent — rows that already have a value are untouched.
    conn.execute(
        """
        UPDATE articles
        SET summarised_at = (
            SELECT generated_at FROM digests ORDER BY id DESC LIMIT 1
        )
        WHERE summary IS NOT NULL
          AND summarised_at IS NULL
        """
    )


def init_db() -> None:
    """
    Create the database file and all tables on first run.

    Safe to call on every startup: all statements use CREATE TABLE IF NOT EXISTS
    so an already-initialised database is left untouched.
    The data/ directory is created if it does not exist.
    """
    db_file = _db_path()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Initialising database at %s", db_file)

    with _connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sources (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT    NOT NULL,
                type            TEXT    NOT NULL,
                url             TEXT    NOT NULL UNIQUE,
                config_json     TEXT,
                default_topics  TEXT,
                description     TEXT,
                active          INTEGER NOT NULL DEFAULT 1,
                taddy_uuid      TEXT
            );

            CREATE TABLE IF NOT EXISTS articles (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id           INTEGER NOT NULL REFERENCES sources(id),
                title               TEXT    NOT NULL,
                url                 TEXT    NOT NULL UNIQUE,
                content             TEXT,
                published_at        TEXT,
                ingested_at         TEXT    NOT NULL,
                summary             TEXT,
                topic_tags          TEXT,
                needs_transcription INTEGER NOT NULL DEFAULT 0,
                audio_url           TEXT,
                is_significant      INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS digests (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at      TEXT NOT NULL,
                content           TEXT NOT NULL,
                delivered_whatsapp INTEGER NOT NULL DEFAULT 0,
                delivered_web      INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS transcription_log (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id     INTEGER NOT NULL,
                audio_seconds  REAL    NOT NULL,
                provider       TEXT    NOT NULL DEFAULT 'groq',
                transcribed_at TEXT    NOT NULL,
                FOREIGN KEY (article_id) REFERENCES articles(id)
            );
        """)
        _run_migrations(conn)

    logger.info("Database ready")


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def get_or_create_source(
    name: str,
    source_type: str,
    url: str,
    config_json: Optional[str] = None,
    default_topics: Optional[str] = None,
) -> int:
    """
    Return the id of an existing source row, inserting one if it does not exist.

    Called by ingestors so they can resolve a source_id before saving articles.
    Uses INSERT OR IGNORE so a pre-existing row (matched on url) is left alone.
    default_topics is stored only on insert; it is not updated if the row already exists.
    """
    with _connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO sources (name, type, url, config_json, default_topics)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, source_type, url, config_json, default_topics),
        )
        row = conn.execute("SELECT id FROM sources WHERE url = ?", (url,)).fetchone()
        if row is None:
            raise RuntimeError(f"Could not resolve source id for url: {url}")
        return int(row["id"])


# ---------------------------------------------------------------------------
# Sources — CRUD and queries
# ---------------------------------------------------------------------------

def create_source(
    name: str,
    source_type: str,
    url: str,
    default_topics: Optional[str] = None,
    description: Optional[str] = None,
    taddy_uuid: Optional[str] = None,
    spotify_url: Optional[str] = None,
    config_json: Optional[str] = None,
    transcript_priority: Optional[str] = None,
    content_category: Optional[str] = None,
) -> int:
    """
    Insert a new source row and return its id.

    Raises sqlite3.IntegrityError if a source with the same url already exists.
    Callers that want upsert behaviour should use get_or_create_source() instead.
    """
    with _connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO sources
                (name, type, url, config_json, default_topics, description,
                 taddy_uuid, spotify_url, active, transcript_priority, content_category)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (name, source_type, url, config_json, default_topics,
             description, taddy_uuid, spotify_url,
             transcript_priority, content_category),
        )
        logger.debug("Created source id=%d: %s", cursor.lastrowid, name)
        return cursor.lastrowid


def update_source(source_id: int, **fields: object) -> None:
    """
    Update any combination of source columns by id.

    Only known column names are accepted; unknown keys raise ValueError so
    callers get an immediate error rather than a silent no-op.
    Raises RuntimeError if no row with that id exists.
    """
    _ALLOWED = {
        "name", "type", "url", "config_json", "default_topics",
        "description", "active", "taddy_uuid", "spotify_url",
        "transcript_priority", "content_category",
    }
    unknown = set(fields) - _ALLOWED
    if unknown:
        raise ValueError(f"Unknown source field(s): {unknown!r}")
    if not fields:
        return
    assignments = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [source_id]
    with _connection() as conn:
        cursor = conn.execute(
            f"UPDATE sources SET {assignments} WHERE id = ?",
            values,
        )
        if cursor.rowcount == 0:
            raise RuntimeError(f"No source found with id={source_id}")
        logger.debug("Updated source id=%d fields=%s", source_id, list(fields))


def delete_source(source_id: int) -> None:
    """
    Delete a source row by id.

    Raises RuntimeError if no row with that id exists.  Note that SQLite
    FOREIGN KEY constraints (with ON DELETE CASCADE not set) will block
    deletion if the source has associated article rows.
    """
    with _connection() as conn:
        cursor = conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        if cursor.rowcount == 0:
            raise RuntimeError(f"No source found with id={source_id}")
        logger.debug("Deleted source id=%d", source_id)


def search_source_by_url(url: str) -> Optional[sqlite3.Row]:
    """
    Return the source row whose url matches exactly, or None if not found.

    Used by seed_from_yaml() to check for duplicates before inserting.
    """
    with _connection() as conn:
        return conn.execute(
            "SELECT * FROM sources WHERE url = ?",
            (url,),
        ).fetchone()


def get_active_sources() -> list[sqlite3.Row]:
    """
    Return all source rows where active = 1, ordered by name.

    This is the primary function ingestors call to discover what to fetch.
    It replaces reading sources.yaml at runtime.
    """
    with _connection() as conn:
        return conn.execute(
            "SELECT * FROM sources WHERE active = 1 ORDER BY name"
        ).fetchall()


def get_source_by_id(source_id: int) -> Optional[sqlite3.Row]:
    """
    Return a single source row by id, augmented with last_ingested_at and
    article_count, or None if the id does not exist.

    Used by PATCH /sources/{id} to return the updated record.
    """
    with _connection() as conn:
        return conn.execute(
            """
            SELECT s.*,
                   MAX(a.ingested_at) AS last_ingested_at,
                   COUNT(a.id)        AS article_count
            FROM sources s
            LEFT JOIN articles a ON a.source_id = s.id
            WHERE s.id = ?
            GROUP BY s.id
            """,
            (source_id,),
        ).fetchone()


def get_all_sources() -> list[sqlite3.Row]:
    """
    Return all sources with their last ingestion time and article count.

    JOINs articles to compute last_ingested_at and article_count so the web
    API and Sources page can show status without a second round-trip.
    """
    with _connection() as conn:
        return conn.execute(
            """
            SELECT
                s.*,
                MAX(a.ingested_at) AS last_ingested_at,
                COUNT(a.id)        AS article_count
            FROM sources s
            LEFT JOIN articles a ON a.source_id = s.id
            GROUP BY s.id
            ORDER BY s.name
            """
        ).fetchall()


# ---------------------------------------------------------------------------
# Articles
# ---------------------------------------------------------------------------

def save_article(
    source_id: int,
    title: str,
    url: str,
    content: Optional[str],
    published_at: Optional[datetime],
    audio_url: Optional[str] = None,
    needs_transcription: bool = False,
    topic_tags: Optional[str] = None,
) -> Optional[int]:
    """
    Persist a single ingested article and return its new row id.

    Returns None (without raising) when the URL already exists in the database,
    so the caller can treat duplicates as a normal no-op rather than an error.
    The ingested_at timestamp is always set to the current UTC time.

    topic_tags should be the source's default_topics CSV string so the
    summariser has a starting point to adjust from.

    For podcast episodes that require Whisper transcription, pass
    needs_transcription=True and audio_url pointing to the enclosure.
    content may be None in that case and will be filled in later by
    whisper_transcriber.
    """
    ingested_at = datetime.now(UTC).isoformat()
    published_str = published_at.isoformat() if published_at else None

    with _connection() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO articles
                (source_id, title, url, content, published_at, ingested_at,
                 audio_url, needs_transcription, topic_tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source_id, title, url, content, published_str, ingested_at,
             audio_url, int(needs_transcription), topic_tags),
        )
        if cursor.lastrowid == 0:
            logger.debug("Duplicate article skipped: %s", url)
            return None
        logger.debug("Saved article id=%d: %s", cursor.lastrowid, title)
        return cursor.lastrowid


def get_unsummarised_articles() -> list[sqlite3.Row]:
    """
    Return all article rows that have not yet been summarised (summary IS NULL).

    JOINs the sources table so callers have access to source_type (used by the
    summariser for content-aware truncation — newsletters are truncated more
    aggressively than podcast transcripts).

    Ordered oldest-first so the summariser works through the backlog in
    chronological order. Each row is a sqlite3.Row and can be accessed by
    column name (e.g. row['title']) or converted to a dict via dict(row).
    """
    with _connection() as conn:
        return conn.execute(
            """
            SELECT a.*, s.name AS source_name, s.type AS source_type,
                   s.transcript_priority, s.content_category
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            WHERE a.summary IS NULL
              AND a.needs_transcription = 0
              AND a.content IS NOT NULL
            ORDER BY a.published_at ASC
            """
        ).fetchall()


def save_summary(
    article_id: int,
    summary: str,
    topic_tags: list[str],
    is_significant: bool = False,
) -> None:
    """
    Write an AI-generated summary, topic tags, and significance flag to an article row.

    Kept as a separate function from save_article so the ingestor and
    summariser pipelines remain independent and can run at different cadences.
    topic_tags is stored as a comma-separated string (e.g. "Tech,AI").
    Raises RuntimeError if no row with that id exists.
    """
    tags_str = ",".join(topic_tags)
    summarised_at = datetime.now(UTC).isoformat()
    with _connection() as conn:
        cursor = conn.execute(
            """
            UPDATE articles
            SET summary = ?, topic_tags = ?, is_significant = ?, summarised_at = ?
            WHERE id = ?
            """,
            (summary, tags_str, int(is_significant), summarised_at, article_id),
        )
        if cursor.rowcount == 0:
            raise RuntimeError(f"No article found with id={article_id}")
        logger.debug("Saved summary for article id=%d", article_id)


def save_extended_summary(article_id: int, text: str) -> None:
    """
    Persist a Gemini-generated extended analysis for a single article.

    Overwrites any existing value so a failed partial write can be retried.
    Raises RuntimeError if no row with that id exists.
    """
    with _connection() as conn:
        cursor = conn.execute(
            "UPDATE articles SET extended_summary = ? WHERE id = ?",
            (text, article_id),
        )
        if cursor.rowcount == 0:
            raise RuntimeError(f"No article found with id={article_id}")
        logger.debug("Saved extended summary for article id=%d", article_id)


def get_articles_since(since: datetime) -> list[sqlite3.Row]:
    """
    Return all articles published at or after the given datetime.

    Used when assembling a digest to gather the relevant time window of content
    (e.g. everything since yesterday's digest run). Rows are ordered by
    published_at ascending so digest assembly sees content in chronological order.
    """
    with _connection() as conn:
        return conn.execute(
            """
            SELECT a.*, s.name AS source_name, s.type AS source_type,
                   s.content_category
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            WHERE a.published_at >= ?
            ORDER BY a.published_at ASC
            """,
            (since.isoformat(),),
        ).fetchall()


def get_article_by_id(article_id: int) -> Optional[sqlite3.Row]:
    """
    Return a single article row by id, including source name and type.

    Returns None rather than raising when the id is not found so API handlers
    can return a clean 404 response.
    """
    with _connection() as conn:
        return conn.execute(
            """
            SELECT a.*, s.name AS source_name, s.type AS source_type,
                   s.spotify_url AS source_spotify_url, s.content_category,
                   s.transcript_priority
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            WHERE a.id = ?
            """,
            (article_id,),
        ).fetchone()


def get_articles_by_ids(article_ids: list[int]) -> list[sqlite3.Row]:
    """
    Return multiple article rows by id, preserving the caller's ordering.

    Only ids that exist in the database are returned — missing ids are silently
    skipped.  Used by the page-summary endpoint to batch-fetch articles without
    N+1 individual queries.
    """
    if not article_ids:
        return []
    placeholders = ", ".join("?" for _ in article_ids)
    with _connection() as conn:
        rows = conn.execute(
            f"""
            SELECT a.id, a.title, a.summary, a.published_at, s.name AS source_name
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            WHERE a.id IN ({placeholders})
            ORDER BY a.published_at DESC
            """,
            article_ids,
        ).fetchall()
    return rows


def get_articles_filtered(
    source_name: Optional[str] = None,
    source_type: Optional[str] = None,
    topic: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    summarised_only: bool = False,
    summarised_since: Optional[datetime] = None,
    summarised_until: Optional[datetime] = None,
    category: Optional[str] = None,
    transcribed: Optional[bool] = None,
    limit: Optional[int] = None,
    q: Optional[str] = None,
) -> list[sqlite3.Row]:
    """
    Return articles matching optional source, topic, and date filters.

    Topic matching handles CSV storage correctly: the expression
    (',' || topic_tags || ',') LIKE '%,<topic>,%' matches the tag regardless
    of whether it appears first, last, or in the middle of the CSV string.

    since / until bound the published_at window (both inclusive) — used by
    the "today" home view where recency of publication is what matters.

    summarised_since / summarised_until bound the summarised_at window — used
    by the archive digest view to associate articles with the run that
    summarised them, regardless of their original publication date.

    summarised_only restricts to rows where summary IS NOT NULL.
    """
    clauses: list[str] = []
    params: list[object] = []

    if source_name is not None:
        clauses.append("s.name = ?")
        params.append(source_name)

    if source_type is not None:
        clauses.append("s.type = ?")
        params.append(source_type)

    if topic is not None:
        clauses.append("(',' || a.topic_tags || ',') LIKE ?")
        params.append(f"%,{topic},%")

    if since is not None:
        clauses.append("a.published_at >= ?")
        params.append(since.isoformat())

    if until is not None:
        clauses.append("a.published_at <= ?")
        params.append(until.isoformat())

    if summarised_since is not None:
        clauses.append("a.summarised_at >= ?")
        params.append(summarised_since.isoformat())

    if summarised_until is not None:
        clauses.append("a.summarised_at <= ?")
        params.append(summarised_until.isoformat())

    if summarised_only:
        clauses.append("a.summary IS NOT NULL")

    if category is not None:
        clauses.append("s.content_category = ?")
        params.append(category)

    if transcribed is True:
        # Approximation: real transcripts are always >> 2 500 chars; descriptions are shorter
        clauses.append("length(a.content) > 2500")
    elif transcribed is False:
        clauses.append("(a.content IS NULL OR length(a.content) <= 2500)")

    if q is not None and q.strip():
        term = f"%{q.strip()}%"
        clauses.append("(a.title LIKE ? OR a.summary LIKE ? OR s.name LIKE ?)")
        params.extend([term, term, term])

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    with _connection() as conn:
        return conn.execute(
            f"""
            SELECT a.*, s.name AS source_name, s.type AS source_type,
                   s.content_category, s.spotify_url AS source_spotify_url,
                   s.transcript_priority
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            {where}
            ORDER BY a.published_at DESC
            {"LIMIT " + str(limit) if limit else ""}
            """,
            params,
        ).fetchall()


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def get_articles_for_scraping() -> list[sqlite3.Row]:
    """
    Return all 'always'-priority podcast articles that do not yet have a full
    transcript.

    Matches both NULL content (bare stub from Taddy) and short description stubs
    (≤ 2500 chars) saved by taddy.py so the frontend has something to display
    before a real transcript arrives.  Real transcripts are always > 2500 chars,
    so this threshold cleanly separates stubs from transcripts.

    Called by scrapers/runner.py.  The runner attempts to load a website
    scraper for each source; if none is found it falls back to Whisper by
    calling update_article_needs_transcription().  This covers both
    scraper-tier and whisper-tier shows without needing a separate DB column.
    """
    with _connection() as conn:
        return conn.execute(
            """
            SELECT a.*, s.name AS source_name, s.type AS source_type,
                   s.taddy_uuid, s.default_topics, s.transcript_priority
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            WHERE s.transcript_priority = 'always'
              AND (a.content IS NULL OR length(a.content) <= 2500)
              AND a.needs_transcription = 0
            ORDER BY a.ingested_at ASC
            """
        ).fetchall()


def update_article_needs_transcription(article_id: int) -> None:
    """
    Flag an article for Whisper transcription as a fallback when a scraper fails.

    Sets needs_transcription = 1 so whisper_transcriber picks it up on the
    next run, provided audio_url is already populated.
    Raises RuntimeError if no row with that id exists.
    """
    with _connection() as conn:
        cursor = conn.execute(
            "UPDATE articles SET needs_transcription = 1 WHERE id = ?",
            (article_id,),
        )
        if cursor.rowcount == 0:
            raise RuntimeError(f"No article found with id={article_id}")
        logger.debug("Flagged article id=%d for Groq transcription", article_id)


def clear_needs_transcription(article_id: int) -> None:
    """
    Clear the needs_transcription flag without setting content.

    Used when an episode is skipped (e.g. audio too short to be a real episode)
    so the transcriber does not retry it on the next pipeline run.
    Raises RuntimeError if no row with that id exists.
    """
    with _connection() as conn:
        cursor = conn.execute(
            "UPDATE articles SET needs_transcription = 0 WHERE id = ?",
            (article_id,),
        )
        if cursor.rowcount == 0:
            raise RuntimeError(f"No article found with id={article_id}")
        logger.debug("Cleared transcription flag for article id=%d (skipped)", article_id)


def get_articles_needing_transcription() -> list[sqlite3.Row]:
    """
    Return all article rows flagged for Groq transcription.

    These are always-priority podcast episodes where neither a website scraper
    nor a transcript was available at ingest time. Each row includes audio_url
    so the transcriber knows what to download.
    """
    with _connection() as conn:
        return conn.execute(
            """
            SELECT a.*, s.name AS source_name
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            WHERE a.needs_transcription = 1
            ORDER BY a.ingested_at ASC
            """
        ).fetchall()


def save_transcription(article_id: int, transcript: str) -> None:
    """
    Write a completed transcript to an article row and clear the transcription flag.

    Once content is set and needs_transcription is cleared to 0, the article
    becomes eligible for AI summarisation on the next summariser run.
    Raises RuntimeError if no row with that id exists.
    """
    with _connection() as conn:
        cursor = conn.execute(
            """
            UPDATE articles
            SET content = ?, needs_transcription = 0
            WHERE id = ?
            """,
            (transcript, article_id),
        )
        if cursor.rowcount == 0:
            raise RuntimeError(f"No article found with id={article_id}")
        logger.debug("Saved transcription for article id=%d", article_id)


def mark_for_transcription(article_id: int) -> None:
    """
    Flag an article for automatic Groq transcription on the next pipeline run.

    Alias for update_article_needs_transcription() with a cleaner name for
    use by the on-demand transcription endpoint.
    Raises RuntimeError if no row with that id exists.
    """
    update_article_needs_transcription(article_id)


_GROQ_LIMIT_SECONDS_HOUR: int = 7_200   # Groq free tier: 7 200 audio-seconds/hour
_GROQ_LIMIT_SECONDS_DAY: int = 28_800   # Groq free tier: 28 800 audio-seconds/day


def log_transcription(
    article_id: int,
    audio_seconds: float,
    provider: str = "groq",
) -> None:
    """
    Record a completed transcription in the audit log.

    Called by whisper_transcriber after each successful Groq API call so that
    get_groq_usage() can compute remaining budget without hitting the API.
    """
    transcribed_at = datetime.now(UTC).isoformat()
    with _connection() as conn:
        conn.execute(
            """
            INSERT INTO transcription_log (article_id, audio_seconds, provider, transcribed_at)
            VALUES (?, ?, ?, ?)
            """,
            (article_id, audio_seconds, provider, transcribed_at),
        )
    logger.debug(
        "Logged transcription: article id=%d, %.0fs audio, provider=%s",
        article_id, audio_seconds, provider,
    )


def get_groq_usage() -> dict:
    """
    Return Groq audio-second usage and remaining budget for the current window.

    Reads from transcription_log, counting only rows with provider='groq'.
    The hour window is the rolling 60 minutes; the day window is since midnight UTC.

    Returns a dict with keys:
        used_seconds_hour, used_seconds_day,
        remaining_seconds_hour, remaining_seconds_day,
        limit_seconds_hour, limit_seconds_day
    """
    from datetime import timedelta

    now = datetime.now(UTC)
    one_hour_ago = (now - timedelta(hours=1)).isoformat()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    with _connection() as conn:
        used_hour = conn.execute(
            "SELECT COALESCE(SUM(audio_seconds), 0) FROM transcription_log "
            "WHERE provider = 'groq' AND transcribed_at >= ?",
            (one_hour_ago,),
        ).fetchone()[0] or 0.0

        used_day = conn.execute(
            "SELECT COALESCE(SUM(audio_seconds), 0) FROM transcription_log "
            "WHERE provider = 'groq' AND transcribed_at >= ?",
            (today_start,),
        ).fetchone()[0] or 0.0

    return {
        "used_seconds_hour": used_hour,
        "used_seconds_day": used_day,
        "remaining_seconds_hour": max(0.0, _GROQ_LIMIT_SECONDS_HOUR - used_hour),
        "remaining_seconds_day": max(0.0, _GROQ_LIMIT_SECONDS_DAY - used_day),
        "limit_seconds_hour": float(_GROQ_LIMIT_SECONDS_HOUR),
        "limit_seconds_day": float(_GROQ_LIMIT_SECONDS_DAY),
    }


_GEMINI_LIMIT_DAY: int = 500  # gemini-3.1-flash-lite-preview free tier: 500 RPD


def log_gemini_call() -> None:
    """
    Record a successful Gemini API call in the audit log.

    Called by _call_gemini() in summariser.py after every successful response
    so that get_gemini_usage() can track daily consumption without hitting
    the Gemini quota API.
    """
    called_at = datetime.now(UTC).isoformat()
    with _connection() as conn:
        conn.execute(
            "INSERT INTO gemini_log (called_at) VALUES (?)",
            (called_at,),
        )


def get_gemini_usage() -> dict:
    """
    Return Gemini API call count and remaining daily budget.

    The day window resets at midnight UTC, matching Gemini's quota reset.

    Returns a dict with keys:
        used_today, remaining_today, limit_today
    """
    today_start = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()

    with _connection() as conn:
        used_today: int = conn.execute(
            "SELECT COUNT(*) FROM gemini_log WHERE called_at >= ?",
            (today_start,),
        ).fetchone()[0] or 0

    return {
        "used_today": used_today,
        "remaining_today": max(0, _GEMINI_LIMIT_DAY - used_today),
        "limit_today": _GEMINI_LIMIT_DAY,
    }


def get_recent_transcriptions(hours: int = 24) -> list[sqlite3.Row]:
    """
    Return transcription log entries from the past N hours, newest first.

    Joined with articles and sources so callers receive article title, source
    name, audio duration, provider, timestamp, and whether a summary exists.
    Used by the GET /api/transcription-log endpoint.
    """
    from datetime import timedelta

    since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    with _connection() as conn:
        return conn.execute(
            """
            SELECT tl.id, tl.article_id, tl.audio_seconds, tl.provider,
                   tl.transcribed_at, a.title,
                   a.summary IS NOT NULL AS has_summary,
                   s.name AS source_name
            FROM transcription_log tl
            JOIN articles a ON a.id = tl.article_id
            JOIN sources  s ON s.id = a.source_id
            WHERE tl.transcribed_at >= ?
            ORDER BY tl.transcribed_at DESC
            """,
            (since,),
        ).fetchall()


# ---------------------------------------------------------------------------
# Digests
# ---------------------------------------------------------------------------

def save_digest(content: str, category: str = "all") -> int:
    """
    Persist a newly generated digest and return its row id.

    category should be 'news', 'informative', or 'all'.
    Both delivery flags (delivered_whatsapp, delivered_web) default to 0
    (False) so the delivery layer can mark them independently after
    successful dispatch.

    If a digest already exists for today with the same category it is
    deleted first, so regenerating the digest after Whisper transcription
    replaces the earlier version rather than creating a duplicate.
    """
    generated_at = datetime.now(UTC).isoformat()
    today = generated_at[:10]  # YYYY-MM-DD
    with _connection() as conn:
        # Remove any earlier digest for today with the same category so
        # the post-Whisper regeneration replaces rather than duplicates.
        conn.execute(
            "DELETE FROM digests WHERE date(generated_at) = ? AND category = ?",
            (today, category),
        )
        cursor = conn.execute(
            """
            INSERT INTO digests (generated_at, content, delivered_whatsapp, delivered_web, category)
            VALUES (?, ?, 0, 0, ?)
            """,
            (generated_at, content, category),
        )
        logger.info("Saved digest id=%d (category=%s)", cursor.lastrowid, category)
        return cursor.lastrowid


def get_digest_by_date(date_str: str, category: Optional[str] = None) -> Optional[sqlite3.Row]:
    """
    Return the digest generated on a specific calendar date (YYYY-MM-DD).

    Uses a LIKE match on generated_at (stored as ISO 8601) so the time
    component is ignored.  Returns the latest digest on that date when
    multiple entries exist (edge case: manual re-runs or two categories).
    category filters by digest category when provided.
    """
    with _connection() as conn:
        if category is not None:
            return conn.execute(
                "SELECT * FROM digests WHERE generated_at LIKE ? AND category = ? "
                "ORDER BY generated_at DESC LIMIT 1",
                (f"{date_str}%", category),
            ).fetchone()
        return conn.execute(
            "SELECT * FROM digests WHERE generated_at LIKE ? ORDER BY generated_at DESC LIMIT 1",
            (f"{date_str}%",),
        ).fetchone()


def get_digests_list(category: Optional[str] = None) -> list[sqlite3.Row]:
    """
    Return all digest rows in reverse-chronological order.

    category filters by digest category ('news', 'informative', 'all') when provided.
    Only id, generated_at, category, and delivery flags are returned — not content —
    so the archive listing stays lightweight.
    """
    with _connection() as conn:
        where = "WHERE d.category = ?" if category is not None else ""
        params: list[object] = [category] if category is not None else []
        return conn.execute(
            f"""
            SELECT d.id, d.generated_at, d.delivered_whatsapp, d.delivered_web,
                   d.category, COUNT(a.id) AS article_count
            FROM digests d
            LEFT JOIN articles a
                ON a.summarised_at >= datetime(d.generated_at, '-25 hours')
               AND a.summarised_at <= d.generated_at
               AND a.summary IS NOT NULL
            {where}
            GROUP BY d.id
            ORDER BY d.generated_at DESC
            """,
            params,
        ).fetchall()


def mark_digest_delivered(digest_id: int, channel: str) -> None:
    """
    Set the delivery flag for 'whatsapp' or 'web' on a digest row.

    Raises ValueError for unknown channels, RuntimeError if the row is
    not found.
    """
    if channel not in ("whatsapp", "web"):
        raise ValueError(f"Unknown delivery channel: {channel!r}")

    column = f"delivered_{channel}"
    with _connection() as conn:
        cursor = conn.execute(
            f"UPDATE digests SET {column} = 1 WHERE id = ?",
            (digest_id,),
        )
        if cursor.rowcount == 0:
            raise RuntimeError(f"No digest found with id={digest_id}")
        logger.debug("Marked digest id=%d delivered via %s", digest_id, channel)


def get_latest_digest(category: Optional[str] = None) -> Optional[sqlite3.Row]:
    """
    Return the most recently generated digest row, or None if none exist yet.

    category filters by digest category ('news', 'informative', 'all') when provided.
    Used by the web API to serve the current digest and by the delivery layer
    to check whether the latest digest has already been dispatched.
    """
    with _connection() as conn:
        if category is not None:
            return conn.execute(
                "SELECT * FROM digests WHERE category = ? ORDER BY generated_at DESC LIMIT 1",
                (category,),
            ).fetchone()
        return conn.execute(
            "SELECT * FROM digests ORDER BY generated_at DESC LIMIT 1"
        ).fetchone()


# ---------------------------------------------------------------------------
# Feed summary (pre-computed structured summary stored by pipeline)
# ---------------------------------------------------------------------------

def save_feed_summary(date: str, summary_json: str) -> int:
    """
    Persist a pre-computed feed summary for the given date (YYYY-MM-DD).

    Replaces any existing summary for the same date so pipeline re-runs
    always keep the most recent version.  Returns the new row id.
    """
    generated_at = datetime.now(UTC).isoformat()
    with _connection() as conn:
        conn.execute(
            "DELETE FROM feed_summaries WHERE date = ?",
            (date,),
        )
        cursor = conn.execute(
            "INSERT INTO feed_summaries (date, summary_json, generated_at) VALUES (?, ?, ?)",
            (date, summary_json, generated_at),
        )
        logger.debug("Saved feed summary for %s (id=%d)", date, cursor.lastrowid)
        return cursor.lastrowid


def get_feed_summary(date: str) -> Optional[sqlite3.Row]:
    """
    Return the stored feed summary for a given date (YYYY-MM-DD), or None.

    Called by the GET /api/feed-summary endpoint so the frontend can display
    the pre-computed summary without triggering a new Gemini call.
    """
    with _connection() as conn:
        return conn.execute(
            "SELECT * FROM feed_summaries WHERE date = ? ORDER BY generated_at DESC LIMIT 1",
            (date,),
        ).fetchone()


def get_articles_ingested_since(since_dt: datetime) -> list[sqlite3.Row]:
    """
    Return all articles ingested at or after since_dt, with source info.

    Used by the Telegram notifier to list articles new to a specific pipeline run.
    """
    with _connection() as conn:
        return conn.execute(
            """
            SELECT a.id, a.title, s.name AS source_name, s.type AS source_type, s.content_category
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            WHERE a.ingested_at >= ?
            ORDER BY s.content_category ASC, a.ingested_at ASC
            """,
            (since_dt.isoformat(),),
        ).fetchall()


def get_recent_summarised_articles(hours: int = 25) -> list[sqlite3.Row]:
    """
    Return recently summarised articles for feed summary generation.

    Returns articles with non-null summaries published in the last `hours`
    hours, ordered newest first.  Used by the pipeline to build the feed
    summary without needing the frontend to pass article IDs.
    """
    from datetime import timedelta
    since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    with _connection() as conn:
        return conn.execute(
            """
            SELECT a.id, a.title, a.summary, a.published_at,
                   s.name AS source_name, s.type AS source_type, s.content_category
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            WHERE a.summary IS NOT NULL
              AND a.published_at >= ?
            ORDER BY a.published_at DESC
            """,
            (since,),
        ).fetchall()
