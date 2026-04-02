"""
Scraper runner — dispatches website transcript scrapers for pending episodes.

Queries the database for podcast episodes where:
  • The source has transcript_priority = 'always'
  • The article content is still NULL (no Taddy transcript was available)

For each article it resolves the correct scraper module by normalising the
source name to a Python module identifier and importing from
src.ingestors.scrapers.<normalised_name>.

If fallback_to_whisper=True and a scraper returns None (transcript not found),
the article is flagged for Whisper transcription instead.

Usage from the pipeline (src/main.py):
    from src.ingestors.scrapers.runner import run_scrapers
    run_scrapers(fallback_to_whisper=True)
"""

import importlib
import logging
from typing import Optional

from src.storage import db

logger = logging.getLogger(__name__)

_TRANSCRIPT_MIN_WORDS = 500


_TRAILING_SUFFIXES = ("_podcast", "_show", "_pod", "_radio", "_audio")

# Explicit overrides for sources whose normalised names don't map cleanly to a
# module file (e.g. names containing punctuation that can't appear in identifiers).
# Keys are lowercased source names; values are module names under this package.
_SCRAPER_MODULE_OVERRIDES: dict[str, str] = {
    "decoder with nilay patel": "decoder",
}


def _normalise_name(source_name: str) -> str:
    """Convert 'My Cool Podcast' → 'my_cool_podcast' for module lookup."""
    return source_name.lower().replace(" ", "_").replace("-", "_").replace("'", "")


def _load_scraper(source_name: str) -> Optional[object]:
    """
    Import and return the scraper module for source_name, or None if not found.

    Checks _SCRAPER_MODULE_OVERRIDES first for sources whose names don't
    normalise to a valid module identifier (e.g. names containing '+').

    Then tries the exact normalised name, then falls back to stripping common
    trailing words so that e.g. 'Lex Fridman Podcast' → 'lex_fridman_podcast'
    (no file) → 'lex_fridman' → src.ingestors.scrapers.lex_fridman (found).

    The module must expose scrape(episode_title, audio_url) -> Optional[str].
    """
    # Check explicit overrides first
    override = _SCRAPER_MODULE_OVERRIDES.get(source_name.lower())
    if override:
        module_path = f"src.ingestors.scrapers.{override}"
        try:
            return importlib.import_module(module_path)
        except ImportError:
            logger.warning("[%s] Override module not found: %s", source_name, module_path)

    base = _normalise_name(source_name)
    candidates = [base]
    for suffix in _TRAILING_SUFFIXES:
        if base.endswith(suffix):
            candidates.append(base[: -len(suffix)])

    for name in candidates:
        module_path = f"src.ingestors.scrapers.{name}"
        logger.debug("[%s] Trying scraper module: %s", source_name, module_path)
        try:
            return importlib.import_module(module_path)
        except ImportError:
            continue

    return None


def get_scraper_for_source(source_name: str) -> Optional[object]:
    """
    Public wrapper around _load_scraper.

    Returns the scraper module for source_name, or None if no scraper exists.
    Used by the on-demand transcription endpoint in api.py to try website
    scraping before falling back to Groq.
    """
    return _load_scraper(source_name)


def run_scrapers(fallback_to_whisper: bool = False) -> int:
    """
    Run all pending scraper-tier episodes through their website scrapers.

    Returns the count of episodes for which a transcript was successfully
    saved to the database.
    """
    articles = db.get_articles_for_scraping()
    if not articles:
        logger.info("No episodes pending website scraping")
        return 0

    logger.info("Running scrapers for %d pending episode(s)", len(articles))
    success_count = 0

    for row in articles:
        article_id: int = row["id"]
        source_name: str = row["source_name"]
        title: str = row["title"]
        audio_url: str = row["audio_url"] or ""

        # Safety guard: description/skip priority sources should never reach
        # the scraper (taddy.py sets content and needs_transcription correctly),
        # but if they do, log a warning and skip rather than wasting a web request.
        priority: str = row["transcript_priority"] if "transcript_priority" in row.keys() else "always"
        if priority != "always":
            logger.warning(
                "[%s] Unexpected priority=%r in scraper queue (article id=%d) — skipping",
                source_name, priority, article_id,
            )
            continue

        scraper = _load_scraper(source_name)
        if scraper is None:
            logger.warning(
                "No scraper module found for %r (tried scrapers.%s) — skipping",
                source_name,
                _normalise_name(source_name),
            )
            # No scraper exists for this source.  If fallback is enabled and the
            # episode has an audio URL, flag it for Groq/Whisper transcription so
            # it isn't silently dropped from the pipeline.
            if fallback_to_whisper and audio_url:
                try:
                    db.update_article_needs_transcription(article_id)
                    logger.info(
                        "[%s] Flagged article %d for Groq transcription (no scraper available): %s",
                        source_name, article_id, title,
                    )
                except RuntimeError as exc:
                    logger.error("Failed to flag article %d for transcription: %s", article_id, exc)
            continue

        logger.info("[%s] Scraping transcript for: %s", source_name, title)

        try:
            transcript: Optional[str] = scraper.scrape(  # type: ignore[attr-defined]
                episode_title=title,
                audio_url=audio_url,
            )
        except Exception as exc:
            logger.error("[%s] Scraper raised for %r: %s", source_name, title, exc, exc_info=True)
            continue

        if transcript and len(transcript.split()) >= _TRANSCRIPT_MIN_WORDS:
            db.save_transcription(article_id, transcript)
            word_count = len(transcript.split())
            logger.info("[%s] Saved %d words for: %s", source_name, word_count, title)
            success_count += 1
        else:
            if transcript:
                logger.warning(
                    "[%s] Transcript too short (%d words, need %d): %s",
                    source_name, len(transcript.split()), _TRANSCRIPT_MIN_WORDS, title,
                )
            else:
                logger.warning("[%s] No transcript found: %s", source_name, title)

            if fallback_to_whisper and audio_url:
                try:
                    db.update_article_needs_transcription(article_id)
                    logger.info("[%s] Flagged for Groq transcription fallback: %s", source_name, title)
                except RuntimeError as exc:
                    logger.error("Failed to flag article %d for Groq transcription: %s", article_id, exc)

    logger.info("Scraper run complete: %d/%d transcripts saved", success_count, len(articles))
    return success_count
