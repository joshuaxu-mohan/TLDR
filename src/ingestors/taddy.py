"""
Taddy podcast ingestor — episode discovery (metadata only).

Taddy (https://taddy.org) provides a free-tier GraphQL API covering thousands
of podcasts.  This module:
  1. Makes ONE batched call to getLatestPodcastEpisodes using all known
     taddy_uuids from active podcast sources in the database.
  2. Routes each episode by transcript_priority:
       always      → saves stub with needs_transcription=1 for the Groq pipeline
       on_demand   → saves episode description as content; user can trigger
                     full transcription on demand via the web frontend
       none        → saves episode description as content; no transcription ever
       skip        → saves title-only stub; no summarisation
  3. Exposes search_podcast() for the /sources/discover API endpoint.

Transcript fetching has been removed from this module entirely.  All full
transcription is now handled by src/ingestors/whisper_transcriber.py (Groq)
and src/ingestors/scrapers/ (website scrapers for selected shows).

Environment variables required (add to .env):
  TADDY_USER_ID   — your Taddy user id
  TADDY_API_KEY   — your Taddy API key

Get credentials at https://taddy.org/developers/api
"""

import logging
import os
from datetime import datetime, UTC, timezone
from typing import Any, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

from src.ingestors.base import IngestResult
from src.storage import db

logger = logging.getLogger(__name__)

_TADDY_ENDPOINT = "https://api.taddy.org"
_REQUEST_TIMEOUT = 30           # seconds


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _headers() -> dict[str, str]:
    user_id = os.environ.get("TADDY_USER_ID", "")
    api_key = os.environ.get("TADDY_API_KEY", "")
    if not user_id or not api_key:
        raise RuntimeError(
            "TADDY_USER_ID and TADDY_API_KEY must be set in .env. "
            "Register at https://taddy.org/developers/api"
        )
    return {
        "X-USER-ID": user_id,
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
        "User-Agent": "my-daily-digest/1.0 (Taddy ingestor)",
    }


def _graphql(query: str) -> dict[str, Any]:
    """Execute a GraphQL query against the Taddy API and return the data dict."""
    try:
        response = requests.post(
            _TADDY_ENDPOINT,
            json={"query": query},
            headers=_headers(),
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(f"Taddy API connection error: {exc}") from exc
    except requests.exceptions.Timeout:
        raise RuntimeError(f"Taddy API timed out after {_REQUEST_TIMEOUT}s")
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(f"Taddy API HTTP error: {exc}") from exc

    errors = payload.get("errors")
    if errors:
        messages = "; ".join(e.get("message", str(e)) for e in errors)
        raise RuntimeError(f"Taddy GraphQL error: {messages}")

    return payload.get("data", {})


def _parse_date(date_published: Any) -> Optional[datetime]:
    """Convert a Unix timestamp integer or ISO string to a datetime."""
    if date_published is None:
        return None
    try:
        if isinstance(date_published, (int, float)):
            return datetime.utcfromtimestamp(date_published)
        if isinstance(date_published, str):
            return datetime.fromisoformat(date_published.rstrip("Z"))
    except (ValueError, OSError):
        pass
    return None


# ---------------------------------------------------------------------------
# Batch episode fetch
# ---------------------------------------------------------------------------

def _batch_fetch_episodes(uuids: list[str]) -> list[dict[str, Any]]:
    """
    Fetch the latest episode for each podcast series UUID in one GraphQL call.

    Returns a flat list of episode dicts, each augmented with podcastSeries info.
    """
    uuid_list = ", ".join(f'"{u}"' for u in uuids)
    query = f"""
    {{
      getLatestPodcastEpisodes(uuids: [{uuid_list}]) {{
        uuid
        name
        datePublished
        audioUrl
        duration
        description(shouldStripHtmlTags: true)
        taddyTranscribeStatus
        podcastSeries {{
          uuid
          name
        }}
      }}
    }}
    """
    data = _graphql(query)
    return data.get("getLatestPodcastEpisodes") or []


# ---------------------------------------------------------------------------
# Main ingest function
# ---------------------------------------------------------------------------

def ingest_podcasts(since_dt: Optional[datetime] = None) -> list[IngestResult]:
    """
    Discover and save the latest episode for every active Taddy-indexed podcast.

    since_dt — when provided, episodes with a known datePublished before this
    datetime are skipped.  Episodes with no datePublished are always kept (we
    cannot know how old they are).  Pass None to ingest everything.

    Sources without a taddy_uuid are silently skipped — they are handled by
    separate ingestors (RSS, custom scrapers).

    Returns one IngestResult per episode processed, success=False for API errors.
    """
    active_sources = db.get_active_sources()
    podcast_sources = [s for s in active_sources if s["type"] == "podcast"]

    # Build uuid → source mapping (only sources with a taddy_uuid)
    uuid_to_source: dict[str, Any] = {}
    for source in podcast_sources:
        tid = source["taddy_uuid"]
        if tid:
            uuid_to_source[tid] = source

    if not uuid_to_source:
        logger.info("No active podcast sources with taddy_uuid configured")
        return []

    logger.info("Fetching latest episodes for %d Taddy source(s)", len(uuid_to_source))

    try:
        episodes = _batch_fetch_episodes(list(uuid_to_source.keys()))
    except RuntimeError as exc:
        logger.error("Taddy batch fetch failed: %s", exc)
        return [IngestResult(
            source_name="taddy", source_type="podcast",
            title="", content="", url=_TADDY_ENDPOINT,
            published_at=datetime.now(UTC), success=False,
            error_message=str(exc),
        )]

    logger.info("Taddy returned %d episode(s)", len(episodes))

    # Per-priority counters for logging
    counts = {"always": 0, "on_demand": 0, "skip": 0}
    results: list[IngestResult] = []

    for ep in episodes:
        title: str = ep.get("name") or "Untitled episode"
        audio_url: str = ep.get("audioUrl") or ""
        date_published = _parse_date(ep.get("datePublished"))
        description: str = ep.get("description") or ""

        series = ep.get("podcastSeries") or {}
        series_uuid: str = series.get("uuid") or ""
        series_name: str = series.get("name") or ""

        source = uuid_to_source.get(series_uuid)
        if source is None:
            logger.debug("No source matched for series uuid=%s — skipping", series_uuid)
            counts["skip"] += 1
            continue

        # Canary: warn if Taddy's series name doesn't match our source name.
        # A mismatch means the taddy_uuid stored in the DB is assigned to the
        # wrong source — articles will be saved under the wrong source name.
        db_lower = source["name"].lower()
        taddy_lower = series_name.lower()
        if series_name and db_lower not in taddy_lower and taddy_lower not in db_lower:
            logger.warning(
                "Taddy series name mismatch: our DB says %r but Taddy returned "
                "%r for uuid=%s — check that taddy_uuid is correctly assigned "
                "on the '%s' source record",
                source["name"], series_name, series_uuid, source["name"],
            )

        # Date filter: skip episodes published before the cutoff.
        # Episodes with no datePublished are kept — we cannot tell how old they are.
        # Normalise to UTC if the parsed datetime is timezone-naive so that
        # comparing against the always-aware since_dt doesn't raise TypeError.
        if date_published is not None and date_published.tzinfo is None:
            date_published = date_published.replace(tzinfo=timezone.utc)
        if since_dt is not None and date_published is not None and date_published < since_dt:
            logger.debug(
                "Skipping old episode (published %s < cutoff %s): %s",
                date_published.isoformat(), since_dt.isoformat(), title,
            )
            counts["skip"] += 1
            continue

        source_name: str = source["name"]
        priority: str = (
            source["transcript_priority"]
            if "transcript_priority" in source.keys()
            else "always"
        )
        default_topics: Optional[str] = source["default_topics"]

        if not audio_url:
            logger.warning("[%s] Episode has no audioUrl: %s", source_name, title)
            counts["skip"] += 1
            continue

        # Use audioUrl as the unique dedup key (it never changes for an episode)
        source_id = db.get_or_create_source(
            name=source_name,
            source_type="podcast",
            url=source["url"],
            default_topics=default_topics,
        )

        # --- Route by priority ---
        content: Optional[str] = None
        needs_transcription = False

        if priority == "skip":
            # Title/URL stub only — no transcript, no summarisation.
            counts["skip"] += 1
            logger.info("[%s] Saving title stub (priority=skip): %s", source_name, title)

        elif priority == "always":
            # Save the episode description as initial content so the frontend
            # can render a card immediately and the early summariser has
            # something to work with.  The scraper runner / Groq transcriber
            # will overwrite this with a full transcript (get_articles_for_scraping
            # picks up articles where content is NULL or a short stub ≤ 2500 chars).
            content = description if description else None
            counts["always"] += 1
            logger.info(
                "[%s] Queued for scraper/Groq, saving description stub (priority=always): %s",
                source_name, title,
            )

        else:
            # priority == "on_demand" or "none" (and legacy "description"):
            # Use the episode description as content so the summariser can produce
            # a short metadata-only summary.  Full transcription is either
            # user-triggered (on_demand) or never attempted (none).
            content = description if description else None
            counts["on_demand"] += 1
            logger.info(
                "[%s] Using description as content (priority=%s): %s",
                source_name, priority, title,
            )

        article_id = db.save_article(
            source_id=source_id,
            title=title,
            url=audio_url,
            content=content,
            published_at=date_published,
            audio_url=audio_url,
            needs_transcription=needs_transcription,
            topic_tags=default_topics,
        )

        if article_id is None:
            logger.debug("[%s] Duplicate episode skipped: %s", source_name, title)
        else:
            logger.info(
                "[%s] Saved episode id=%d (priority=%s): %s",
                source_name, article_id, priority, title,
            )

        results.append(IngestResult(
            source_name=source_name,
            source_type="podcast",
            title=title,
            content=content or "",
            url=audio_url,
            published_at=date_published or datetime.now(UTC),
            success=True,
            error_message=None,
        ))

    logger.info(
        "Taddy ingest complete — always:%d on_demand:%d skipped:%d",
        counts["always"], counts["on_demand"], counts["skip"],
    )
    return results


# ---------------------------------------------------------------------------
# Search (for /sources/discover endpoint)
# ---------------------------------------------------------------------------

def search_podcast(query: str) -> list[dict[str, Any]]:
    """
    Search Taddy for podcast series matching query and return structured results.

    Checks taddyTranscribeStatus on the latest episode for each result to
    indicate whether transcripts are available.  Used by the discover API endpoint.
    """
    gql_query = f"""
    {{
      search(term: "{query}", filterForTypes: PODCASTSERIES) {{
        searchId
        podcastSeries {{
          uuid
          name
          description
          rssUrl
        }}
      }}
    }}
    """
    try:
        data = _graphql(gql_query)
    except RuntimeError as exc:
        logger.error("Taddy search failed for %r: %s", query, exc)
        return []

    series_list = (data.get("search") or {}).get("podcastSeries") or []
    results: list[dict[str, Any]] = []

    for series in series_list[:3]:
        uuid = series.get("uuid") or ""
        transcript_available = False

        # Check transcript status via latest episode
        if uuid:
            try:
                episodes = _batch_fetch_episodes([uuid])
                if episodes:
                    status = episodes[0].get("taddyTranscribeStatus", "")
                    transcript_available = status == "AVAILABLE"
            except RuntimeError:
                pass

        results.append({
            "name": series.get("name") or "",
            "description": series.get("description") or "",
            "rss_url": series.get("rssUrl") or "",
            "taddy_uuid": uuid,
            "transcript_available": transcript_available,
        })

    return results
