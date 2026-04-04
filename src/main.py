"""
Main pipeline orchestrator.

One-shot modes (exit after completion):
  python -m src.main --run-now         Run full ingestion cycle once and exit.
                                       Suitable for Task Scheduler / cron jobs.
  python -m src.main --ingest-only     Run ingestion only (no digest) and exit.
  python -m src.main --run-now --since 24h   Run with a time-window filter.

Daemon mode (runs indefinitely):
  python -m src.main --daemon          Start the blocking scheduler:
                                         • News cycle    daily at 09:00 UTC
                                         • Informative   12:00 and 18:00 UTC

Graceful shutdown (daemon mode only):
  Press Ctrl+C (SIGINT) or send SIGTERM to stop the scheduler cleanly.
"""

import argparse
import logging
import signal
import sys
from datetime import datetime, timedelta, UTC
from typing import Optional

from apscheduler.schedulers.blocking import BlockingScheduler

from src.storage import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_since(value: str) -> datetime:
    """
    Parse a duration string into a UTC cutoff datetime.

    Accepted formats: "24h", "48h", "7d" etc.
    Raises argparse.ArgumentTypeError on invalid input so argparse can surface
    a clean error message before the pipeline starts.
    """
    value = value.strip().lower()
    try:
        if value.endswith("h"):
            return datetime.now(UTC) - timedelta(hours=int(value[:-1]))
        if value.endswith("d"):
            return datetime.now(UTC) - timedelta(days=int(value[:-1]))
    except ValueError:
        pass
    raise argparse.ArgumentTypeError(
        f"Invalid --since value {value!r}. Use a number followed by 'h' or 'd', e.g. '24h' or '7d'."
    )


# ---------------------------------------------------------------------------
# Pipeline cycles
# ---------------------------------------------------------------------------

def _summarise_and_digest() -> None:
    """
    Run summarise_new_articles() then regenerate both category digests.

    Called twice per ingestion cycle: once after scrapers (catches newsletters,
    description-tier and scraper-tier content immediately) and again after
    Groq transcription (adds freshly transcribed episodes to the digest, replacing
    the earlier version via save_digest's same-date delete-before-insert logic).
    """
    from src.summariser.summariser import (
        summarise_new_articles, generate_daily_digest, generate_and_save_feed_summary,
    )

    try:
        count = summarise_new_articles()
        logger.info("Summariser: %d article(s) summarised", count)
    except Exception as exc:
        logger.error("Summariser failed: %s", exc, exc_info=True)
        return  # do not generate a digest from a partial summarisation run

    for cat in ("news", "informative"):
        try:
            digest_id = generate_daily_digest(category=cat)
            if digest_id is None:
                logger.info("No %s content — %s digest not generated", cat, cat)
            else:
                logger.info("%s digest saved: id=%d", cat.capitalize(), digest_id)
        except Exception as exc:
            logger.error("%s digest generation failed: %s", cat.capitalize(), exc, exc_info=True)

    try:
        generate_and_save_feed_summary()
    except Exception as exc:
        logger.error("Feed summary generation failed: %s", exc, exc_info=True)


def ingestion_cycle(since_dt: Optional[datetime] = None) -> None:
    """
    Full ingestion cycle: fetch content, transcribe audio, summarise new articles,
    and generate both category digests.

    Pipeline order:
      1. RSS ingestor      (Substack newsletters)
      2. Taddy ingestor    (podcast episode discovery; routes by transcript_priority)
      3. Scraper runner    (website transcript scrapers for selected shows)
      4. Early summarise + digest  (newsletters + on_demand/none/scraper content ready immediately)
      5. Groq transcription        (cloud API; runs while digests are already live)
      6. Post-Groq summarise + digest  (adds newly transcribed episodes; replaces earlier digest)

    Each step is run regardless of whether the previous one produced output —
    there may already be queued items from a previous run.

    since_dt — optional cutoff: ingestors skip content published before this
    datetime.  None means no filter (used by the scheduler on every normal run).
    """
    start = datetime.now(UTC)
    if since_dt:
        logger.info(
            "=== Ingestion cycle starting at %s (since %s) ===",
            start.isoformat(), since_dt.isoformat(),
        )
    else:
        logger.info("=== Ingestion cycle starting at %s ===", start.isoformat())

    # Per-step counters collected for the post-run Telegram notification
    _scraped = _transcribed = 0

    try:
        # 1. RSS / Substack
        try:
            from src.ingestors.rss import ingest_substacks
            results = ingest_substacks(since_dt=since_dt)
            logger.info("RSS ingestor: %d/%d succeeded", sum(1 for r in results if r.success), len(results))
        except Exception as exc:
            logger.error("RSS ingestor failed: %s", exc, exc_info=True)

        # 2a. Taddy podcast ingestor (episode discovery + taddy/scraper/whisper routing)
        try:
            from src.ingestors.taddy import ingest_podcasts as taddy_ingest
            results = taddy_ingest(since_dt=since_dt)
            logger.info("Taddy ingestor: %d/%d succeeded", sum(1 for r in results if r.success), len(results))
        except Exception as exc:
            logger.error("Taddy ingestor failed: %s", exc, exc_info=True)

        # 2b. Website scrapers — fetch transcripts for scraper-tier episodes
        try:
            from src.ingestors.scrapers.runner import run_scrapers
            _scraped = run_scrapers(fallback_to_whisper=True)
            logger.info("Scraper runner: %d episode(s) scraped", _scraped)
        except Exception as exc:
            logger.error("Scraper runner failed: %s", exc, exc_info=True)

        # 3. Early summarise + digest — newsletters, on_demand/none, and scraper transcripts
        #    are ready now; no point waiting for Groq (~1-3 min per episode).
        logger.info("--- Early summarisation starting ---")
        _summarise_and_digest()
        logger.info("--- Early summarisation complete, starting Groq transcription ---")

        # 4. Groq transcription (always-priority episodes that need a full transcript)
        try:
            from src.ingestors.whisper_transcriber import transcribe_pending
            transcribed_list = transcribe_pending(max_episodes=5)
            _transcribed = len(transcribed_list)
            logger.info("Groq transcriber: %d episode(s) transcribed", _transcribed)
        except Exception as exc:
            logger.error("Groq transcriber failed: %s", exc, exc_info=True)

        # 5. Post-Groq summarise + digest — newly transcribed episodes are now
        #    summarised and the digest is regenerated (replacing the earlier version).
        logger.info("--- Post-Groq summarisation starting ---")
        _summarise_and_digest()
        logger.info("--- Post-Groq summarisation complete, digest updated ---")

    except Exception as exc:
        logger.error("Ingestion cycle critical error: %s", exc, exc_info=True)
        try:
            from src.delivery.telegram import send_error_notification
            send_error_notification(exc)
        except Exception:
            pass
        raise

    # Send Telegram notification — failure must never delay or crash the pipeline
    try:
        from src.delivery.telegram import send_pipeline_notification
        send_pipeline_notification(
            start_time=start,
            scraped=_scraped,
            transcribed=_transcribed,
        )
    except Exception as exc:
        logger.warning("Telegram notification failed: %s", exc)

    elapsed = (datetime.now(UTC) - start).total_seconds()
    logger.info("=== Ingestion cycle complete (%.1fs) ===", elapsed)


def news_cycle() -> None:
    """
    News cycle (runs once daily at 09:00 UTC):
    Ingestion → early summarise/digest → Whisper → post-Whisper summarise/digest
    → WhatsApp delivery of the final news digest.
    """
    start = datetime.now(UTC)
    logger.info("=== News cycle starting at %s ===", start.isoformat())

    ingestion_cycle()

    # WhatsApp delivery of the news digest (already generated inside ingestion_cycle)
    try:
        from src.delivery.whatsapp import send_daily_digest
        send_daily_digest()
    except RuntimeError as exc:
        logger.warning("WhatsApp delivery skipped: %s", exc)
    except Exception as exc:
        logger.error("WhatsApp delivery failed: %s", exc, exc_info=True)

    elapsed = (datetime.now(UTC) - start).total_seconds()
    logger.info("=== News cycle complete (%.1fs) ===", elapsed)


def informative_cycle() -> None:
    """
    Informative cycle (runs at 12:00 and 18:00 UTC):
    Ingestion → early summarise/digest → Whisper → post-Whisper summarise/digest.
    No WhatsApp delivery.
    """
    start = datetime.now(UTC)
    logger.info("=== Informative cycle starting at %s ===", start.isoformat())

    ingestion_cycle()

    elapsed = (datetime.now(UTC) - start).total_seconds()
    logger.info("=== Informative cycle complete (%.1fs) ===", elapsed)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def health_check() -> None:
    """Log a heartbeat so it is clear the process is still alive."""
    digest_row = db.get_latest_digest()
    last_digest = digest_row["generated_at"] if digest_row else "never"
    logger.info("Health check OK — last digest: %s", last_digest)


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

def _setup_signal_handlers(scheduler: BlockingScheduler) -> None:
    def _handle(signum: int, frame: object) -> None:
        logger.info("Signal %d received — shutting down scheduler", signum)
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle)
    # SIGTERM is not available on Windows; ignore the AttributeError
    try:
        signal.signal(signal.SIGTERM, _handle)
    except (AttributeError, OSError):
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Daily Digest pipeline orchestrator")
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Run the full ingestion cycle immediately and exit (suitable for Task Scheduler / cron)",
    )
    parser.add_argument(
        "--ingest-only",
        action="store_true",
        help="Run the ingestion cycle once and exit (no digest, no scheduler)",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Start the blocking scheduler (news at 09:00 UTC, informative at 12:00 and 18:00 UTC)",
    )
    parser.add_argument(
        "--since",
        default=None,
        metavar="DURATION",
        help=(
            "Skip content published before this cutoff (e.g. '24h', '48h', '7d'). "
            "Defaults to '24h' when --run-now is used. No filter when running on the scheduler."
        ),
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Disable the date filter entirely — ingest all available content regardless of publish date.",
    )
    args = parser.parse_args()

    logger.info("Initialising database")
    db.init_db()

    # Seed sources from yaml on first run (idempotent — skips existing rows)
    from src.config.loader import seed_from_yaml
    seeded = seed_from_yaml()
    if seeded:
        logger.info("Seeded %d new source(s) from sources.yaml", seeded)

    # Resolve the since_dt for manual runs.
    # --backfill overrides everything (no filter).
    # --since sets an explicit cutoff.
    # --run-now / --ingest-only default to the last 24 hours when neither flag is given.
    since_dt: Optional[datetime] = None
    if not args.backfill:
        raw_since = args.since
        if raw_since is None and (args.run_now or args.ingest_only):
            raw_since = "24h"
        if raw_since is not None:
            try:
                since_dt = _parse_since(raw_since)
            except argparse.ArgumentTypeError as exc:
                parser.error(str(exc))

    if args.ingest_only:
        ingestion_cycle(since_dt=since_dt)
        logger.info("Ingest-only run complete — exiting")
        return

    if args.run_now:
        # Full pipeline run: ingestion + summarisation + both category digests.
        # Returns immediately after — does NOT fall through to the scheduler.
        ingestion_cycle(since_dt=since_dt)
        logger.info("--run-now complete — exiting")
        return

    if not args.daemon:
        parser.error(
            "No action specified. Use --run-now to run once, "
            "--ingest-only to ingest without a digest, or --daemon to start the scheduler."
        )

    scheduler = BlockingScheduler(timezone="UTC")
    _setup_signal_handlers(scheduler)

    # News cycle: once daily at 09:00 UTC (ingestion + news digest + WhatsApp)
    scheduler.add_job(
        news_cycle,
        trigger="cron",
        hour=9,
        minute=0,
        id="news",
        max_instances=1,
        coalesce=True,
    )

    # Informative cycle: twice daily at 12:00 and 18:00 UTC
    scheduler.add_job(
        informative_cycle,
        trigger="cron",
        hour=12,
        minute=0,
        id="informative_noon",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        informative_cycle,
        trigger="cron",
        hour=18,
        minute=0,
        id="informative_evening",
        max_instances=1,
        coalesce=True,
    )

    # Health check: every hour
    scheduler.add_job(
        health_check,
        trigger="interval",
        hours=1,
        id="health",
    )

    logger.info(
        "Scheduler started — news cycle daily 09:00 UTC, "
        "informative cycles at 12:00 and 18:00 UTC. Press Ctrl+C to stop."
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    main()
