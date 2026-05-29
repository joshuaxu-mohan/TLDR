"""
Backfill summaries for articles lost during the gemini-3.1-flash-lite-preview outage.

Finds articles where summary IS NULL or summary = '', normalises empty strings to
NULL so the standard summariser query picks them up, then runs the summariser.

Safe to run multiple times — only touches articles still missing summaries.

Usage:
    python scripts/resummarise_missing.py
"""

import logging
import sys
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.storage import db
from src.summariser.summariser import summarise_new_articles

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _count_missing() -> tuple[int, int]:
    """Return (null_count, empty_string_count) of articles missing summaries."""
    with db._connection() as conn:
        null_count = conn.execute(
            """
            SELECT COUNT(*) FROM articles
            WHERE summary IS NULL
              AND needs_transcription = 0
              AND content IS NOT NULL
            """
        ).fetchone()[0]
        empty_count = conn.execute(
            """
            SELECT COUNT(*) FROM articles
            WHERE summary = ''
              AND needs_transcription = 0
              AND content IS NOT NULL
            """
        ).fetchone()[0]
    return null_count, empty_count


def _normalise_empty_summaries() -> int:
    """Set summary = NULL for any rows where summary = ''. Returns rows updated."""
    with db._connection() as conn:
        cursor = conn.execute(
            "UPDATE articles SET summary = NULL WHERE summary = ''"
        )
        return cursor.rowcount


def main() -> None:
    null_count, empty_count = _count_missing()
    total_missing = null_count + empty_count

    logger.info(
        "Missing summaries: %d NULL + %d empty string = %d total",
        null_count, empty_count, total_missing,
    )

    if total_missing == 0:
        logger.info("Nothing to backfill — all articles already have summaries")
        return

    if empty_count > 0:
        updated = _normalise_empty_summaries()
        logger.info("Normalised %d empty-string summaries to NULL", updated)

    logger.info("Starting summarisation of %d article(s)…", total_missing)
    summarised = summarise_new_articles()
    failed = total_missing - summarised

    logger.info(
        "Backfill complete: %d/%d summarised, %d failed (will retry on next run)",
        summarised, total_missing, failed,
    )


if __name__ == "__main__":
    main()
