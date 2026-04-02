"""
Configuration loader for the digest pipeline.

sources.yaml is the seed file for initial database population.
After seeding, all runtime source discovery goes through src/storage/db.py
(get_active_sources()) — not this module.

seed_from_yaml() is idempotent: it skips any source whose URL is already
in the database, so it is safe to call on every startup.
"""

import logging
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

_SOURCES_PATH = Path(__file__).parent / "sources.yaml"


# ---------------------------------------------------------------------------
# YAML parsing helpers
# ---------------------------------------------------------------------------

def _load_yaml_raw() -> Any:
    """Parse sources.yaml and return the raw Python object, or None."""
    if not _SOURCES_PATH.exists():
        logger.warning("sources.yaml not found at %s — skipping seed", _SOURCES_PATH)
        return None
    with _SOURCES_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _yaml_entries() -> list[dict[str, Any]]:
    """
    Return a flat list of source entry dicts from sources.yaml.

    Handles two formats:
      • Flat list  (current)  — top-level is a YAML sequence of entries
      • Legacy dict (old)     — top-level has "substacks" and/or "podcasts" keys
    """
    raw = _load_yaml_raw()
    if raw is None:
        return []
    if isinstance(raw, list):
        return [e for e in raw if isinstance(e, dict)]
    if isinstance(raw, dict):
        entries: list[dict[str, Any]] = []
        for key in ("substacks", "podcasts"):
            section = raw.get(key, [])
            if isinstance(section, list):
                entries.extend(e for e in section if isinstance(e, dict))
        return entries
    logger.error("Unexpected sources.yaml top-level type: %s", type(raw))
    return []


def _topics_to_csv(topics: Any) -> Optional[str]:
    """Convert a YAML topic list or CSV string to a CSV string, or None."""
    if topics is None:
        return None
    if isinstance(topics, list):
        return ",".join(str(t).strip() for t in topics if str(t).strip())
    if isinstance(topics, str):
        return topics.strip() or None
    return None


# ---------------------------------------------------------------------------
# Database seeding
# ---------------------------------------------------------------------------

def seed_from_yaml() -> int:
    """
    Import sources from sources.yaml into the database, skipping duplicates.

    Matching is done by exact URL.  Fields already in the database are NOT
    overwritten — this is a one-time seed, not a sync.  To update an existing
    source, use db.update_source() directly.

    Returns the count of newly inserted source rows.
    """
    # Import here to avoid a circular import at module level
    from src.storage import db

    entries = _yaml_entries()
    if not entries:
        logger.info("seed_from_yaml: no entries found in sources.yaml")
        return 0

    inserted = 0
    for entry in entries:
        url: str = (entry.get("url") or "").strip()
        name: str = (entry.get("name") or "").strip()
        source_type: str = (entry.get("type") or "").strip()

        if not url or not name or not source_type:
            logger.warning("Skipping incomplete sources.yaml entry: %r", entry)
            continue

        if db.search_source_by_url(url) is not None:
            logger.debug("seed_from_yaml: %s already in DB — skipping", name)
            continue

        default_topics = _topics_to_csv(entry.get("default_topics"))
        taddy_uuid: Optional[str] = entry.get("taddy_uuid") or None
        description: Optional[str] = entry.get("description") or None
        spotify_url: Optional[str] = entry.get("spotify_url") or None
        transcript_priority: Optional[str] = entry.get("transcript_priority") or None
        content_category: Optional[str] = entry.get("content_category") or None

        try:
            db.create_source(
                name=name,
                source_type=source_type,
                url=url,
                default_topics=default_topics,
                description=description,
                taddy_uuid=taddy_uuid,
                spotify_url=spotify_url,
                transcript_priority=transcript_priority,
                content_category=content_category,
            )
            logger.info("seed_from_yaml: inserted %s (%s)", name, source_type)
            inserted += 1
        except Exception as exc:
            logger.error("seed_from_yaml: failed to insert %s: %s", name, exc)

    logger.info("seed_from_yaml: %d new source(s) inserted", inserted)
    return inserted
