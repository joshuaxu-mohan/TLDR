"""
RSS ingestor — currently covers Substack sources.

Content extraction hierarchy per entry (matches CLAUDE.md):
  1. content:encoded  (entry.content list, type text/html)
  2. summary          (feedparser's parsed <description>)
  3. description      (alias for summary in most RSS 2.0 feeds)

Each public function returns a list[IngestResult] so the pipeline can collect
results across all sources and handle failures without aborting the run.
"""

import logging
import time
from datetime import datetime, UTC, timezone
from typing import Any, Optional

import feedparser
import requests
from bs4 import BeautifulSoup

from src.ingestors.base import IngestResult
from src.storage import db

logger = logging.getLogger(__name__)

_USER_AGENT = "my-daily-digest/1.0 (RSS ingestor)"
_REQUEST_TIMEOUT = 15  # seconds


# ---------------------------------------------------------------------------
# HTML utilities
# ---------------------------------------------------------------------------

def _strip_html(html: str) -> str:
    """Return plain text from an HTML string, collapsing whitespace."""
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator=" ", strip=True)


# ---------------------------------------------------------------------------
# Entry parsing
# ---------------------------------------------------------------------------

def _extract_content(entry: Any) -> str:
    """
    Pull the richest available text from a feedparser entry.

    feedparser maps <content:encoded> to entry.content as a list of dicts
    with 'type' and 'value' keys.  We prefer that over the shorter summary.
    """
    # 1. content:encoded
    for block in getattr(entry, "content", []):
        value = block.get("value", "").strip()
        if value:
            return _strip_html(value)

    # 2. summary (feedparser already parses <description> into this)
    summary = getattr(entry, "summary", "").strip()
    if summary:
        return _strip_html(summary)

    # 3. description (older or non-standard feeds)
    description = getattr(entry, "description", "").strip()
    if description:
        return _strip_html(description)

    return ""


def _parse_published(entry: Any) -> Optional[datetime]:
    """Convert feedparser's published_parsed (time.struct_time) to a datetime."""
    struct = getattr(entry, "published_parsed", None)
    if struct is None:
        return None
    try:
        return datetime(*struct[:6])
    except (TypeError, ValueError):
        return None


def _parse_entry(entry: Any, source_name: str) -> Optional[IngestResult]:
    """
    Convert a single feedparser entry into an IngestResult.

    Returns None when the entry lacks the minimum required fields (title, link)
    so the caller can skip it cleanly without raising.
    """
    title = getattr(entry, "title", "").strip()
    url = getattr(entry, "link", "").strip()

    if not title or not url:
        logger.warning("[%s] Entry missing title or link — skipping", source_name)
        return None

    content = _extract_content(entry)
    published_at = _parse_published(entry)

    return IngestResult(
        source_name=source_name,
        source_type="substack",
        title=title,
        content=content,
        url=url,
        published_at=published_at or datetime.now(UTC),
        success=True,
        error_message=None,
    )


# ---------------------------------------------------------------------------
# Feed fetching (no DB dependency — usable standalone)
# ---------------------------------------------------------------------------

def fetch_feed(feed_url: str, source_name: str) -> list[IngestResult]:
    """
    Download and parse one RSS feed, returning a list of IngestResult objects.

    Does NOT interact with the database, so it can be called from test scripts
    without any DB setup.  Network and parse errors produce a single failure
    IngestResult rather than raising.
    """
    logger.info("[%s] Fetching feed: %s", source_name, feed_url)

    try:
        response = requests.get(
            feed_url,
            timeout=_REQUEST_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError as exc:
        logger.error("[%s] Connection failed: %s", source_name, exc)
        return [IngestResult(
            source_name=source_name, source_type="substack",
            title="", content="", url=feed_url,
            published_at=datetime.now(UTC), success=False,
            error_message=f"Connection error: {exc}",
        )]
    except requests.exceptions.Timeout:
        logger.error("[%s] Feed request timed out after %ds", source_name, _REQUEST_TIMEOUT)
        return [IngestResult(
            source_name=source_name, source_type="substack",
            title="", content="", url=feed_url,
            published_at=datetime.now(UTC), success=False,
            error_message="Request timed out",
        )]
    except requests.exceptions.HTTPError as exc:
        logger.error("[%s] HTTP error: %s", source_name, exc)
        return [IngestResult(
            source_name=source_name, source_type="substack",
            title="", content="", url=feed_url,
            published_at=datetime.now(UTC), success=False,
            error_message=f"HTTP error: {exc}",
        )]

    feed = feedparser.parse(response.text)

    if feed.bozo and not feed.entries:
        msg = str(getattr(feed, "bozo_exception", "malformed feed"))
        logger.warning("[%s] Feed parse warning: %s", source_name, msg)
        return [IngestResult(
            source_name=source_name, source_type="substack",
            title="", content="", url=feed_url,
            published_at=datetime.now(UTC), success=False,
            error_message=f"Malformed feed: {msg}",
        )]

    results: list[IngestResult] = []
    for entry in feed.entries:
        result = _parse_entry(entry, source_name)
        if result is not None:
            results.append(result)

    logger.info("[%s] Parsed %d entries", source_name, len(results))
    return results


# ---------------------------------------------------------------------------
# Main ingestor (with DB deduplication)
# ---------------------------------------------------------------------------

def ingest_substacks(since_dt: Optional[datetime] = None) -> list[IngestResult]:
    """
    Fetch all enabled Substack sources and persist new articles to the database.

    since_dt — when provided, entries published before this datetime are skipped.
    Pass None (the default) to ingest everything available.

    Articles whose URL already exists in the database are silently skipped
    (INSERT OR IGNORE handles this in db.save_article).  Returns all IngestResult
    objects including failures, so the pipeline can log or alert on them.
    """
    all_active = db.get_active_sources()
    sources = [s for s in all_active if s["type"] == "substack"]
    if not sources:
        logger.warning("No active Substack sources found in the database. Run seed_from_yaml() first.")
        return []

    all_results: list[IngestResult] = []

    for source in sources:
        name = source["name"]
        feed_url = source["url"]   # url column IS the feed URL for substacks
        source_url = feed_url

        if not feed_url:
            logger.error("[%s] No feed_url configured — skipping", name)
            continue

        source_id = db.get_or_create_source(
            name=name,
            source_type="substack",
            url=source_url,
            default_topics=source["default_topics"],
        )

        results = fetch_feed(feed_url, name)

        for result in results:
            if not result.success:
                all_results.append(result)
                continue

            # Date filter: skip entries published before the cutoff.
            # Normalise to UTC if the parsed datetime is timezone-naive so that
            # comparing against the always-aware since_dt doesn't raise TypeError.
            published_at = result.published_at
            if since_dt is not None and published_at is not None:
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
            if since_dt is not None and published_at is not None and published_at < since_dt:
                logger.debug(
                    "[%s] Skipping old entry (published %s < cutoff %s): %s",
                    name, published_at.isoformat(), since_dt.isoformat(), result.title,
                )
                continue

            article_id = db.save_article(
                source_id=source_id,
                title=result.title,
                url=result.url,
                content=result.content,
                published_at=result.published_at,
                topic_tags=source["default_topics"],
            )
            if article_id is None:
                logger.debug("[%s] Duplicate skipped: %s", name, result.url)
            else:
                logger.info("[%s] Saved article id=%d: %s", name, article_id, result.title)
            all_results.append(result)

        # Polite delay between sources
        time.sleep(1)

    return all_results
