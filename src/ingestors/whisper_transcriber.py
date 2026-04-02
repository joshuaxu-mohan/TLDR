"""
Transcription module — Groq cloud API (whisper-large-v3).

Public interface
----------------
transcribe_pending(max_episodes)
    Batch transcription: processes articles flagged with needs_transcription=1.
    Stops the run early if Groq returns a long rate-limit delay (≥ 60 s).

transcribe_article(article_id)
    On-demand transcription for a single article.
    Returns a dict with 'transcript', 'word_count', 'audio_seconds'.
    Raises RateLimitError if the Groq limit is already exhausted.
    Raises RuntimeError on download or transcription failure.

Rate limits (Groq free tier)
-----------------------------
7 200 audio-seconds per hour / 28 800 per day.
Both limits are tracked in the transcription_log DB table.

Smart 429 handling
------------------
When Groq returns 429, the Retry-After header is inspected:
  • < 60 s  → sleep and retry (up to _GROQ_MAX_RETRIES attempts per file)
  • ≥ 60 s  → raise RateLimitError to stop the current run early

Budget pre-check
----------------
Before downloading each audio file, db.get_groq_usage() is consulted.
If fewer than 5 minutes remain on either the hourly or daily limit, the
episode is skipped and the run continues so subsequent episodes are also
checked.

ffmpeg requirement
------------------
ffmpeg (and ffprobe) must be installed and on PATH.  Required for:
  - Chunking audio files that exceed the 24 MB Groq per-request limit
  - Measuring audio duration for the transcription log
"""

from __future__ import annotations

import logging
import math
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

import requests

from src.config.settings import get_settings
from src.storage import db

logger = logging.getLogger(__name__)

_USER_AGENT = "my-daily-digest/1.0 (groq transcriber)"
_DOWNLOAD_TIMEOUT = 60                       # seconds
_CHUNK_SIZE = 1024 * 1024                    # 1 MB download chunks
_DEFAULT_MAX_EPISODES = 5

# Groq per-request limits
_GROQ_SIZE_LIMIT_BYTES = 24 * 1024 * 1024   # 24 MB (1 MB under the 25 MB API cap)
_GROQ_MAX_RETRIES = 3
_GROQ_RETRY_BASE_DELAY = 5                  # seconds; first retry delay for non-429 errors
_GROQ_BUDGET_MIN_SECONDS = 300              # require 5 min remaining before attempting
_MIN_AUDIO_SIZE_BYTES = 2 * 1024 * 1024     # 2 MB — below this, likely a trailer/promo


class RateLimitError(Exception):
    """Groq returned 429 with Retry-After ≥ 60 s — stop the current run."""


# ---------------------------------------------------------------------------
# Audio download
# ---------------------------------------------------------------------------

def _download_audio(audio_url: str, dest_dir: Path) -> Path:
    """Stream an audio file to dest_dir and return the local path."""
    logger.info("Downloading audio: %s", audio_url)

    response = requests.get(
        audio_url,
        stream=True,
        timeout=_DOWNLOAD_TIMEOUT,
        headers={"User-Agent": _USER_AGENT},
    )
    response.raise_for_status()

    filename = audio_url.rstrip("/").split("/")[-1] or "episode.mp3"
    filename = filename.split("?")[0]
    local_path = dest_dir / filename

    bytes_written = 0
    with local_path.open("wb") as fh:
        for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
            fh.write(chunk)
            bytes_written += len(chunk)

    logger.info("Downloaded %.1f MB to %s", bytes_written / 1024 / 1024, local_path)
    return local_path


# ---------------------------------------------------------------------------
# Audio utilities
# ---------------------------------------------------------------------------

def _get_audio_duration(audio_path: Path) -> float:
    """Return the duration of an audio file in seconds via ffprobe."""
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe not on PATH — install ffmpeg")
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def _chunk_audio(audio_path: Path, chunk_dir: Path) -> list[Path]:
    """
    Split audio_path into ≤24 MB chunks and return an ordered list of paths.

    Calculates chunk count from file size, then uses ffmpeg to extract
    equal-duration segments.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not on PATH — install ffmpeg")

    file_size = audio_path.stat().st_size
    num_chunks = math.ceil(file_size / _GROQ_SIZE_LIMIT_BYTES)
    duration = _get_audio_duration(audio_path)
    chunk_duration = duration / num_chunks

    chunk_paths: list[Path] = []
    for i in range(num_chunks):
        start = i * chunk_duration
        chunk_path = chunk_dir / f"chunk{i:03d}.mp3"
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(audio_path),
                "-ss", str(start),
                "-t", str(chunk_duration),
                "-acodec", "copy",
                str(chunk_path),
            ],
            capture_output=True,
            check=True,
        )
        chunk_paths.append(chunk_path)

    logger.info(
        "Split %s (%.1f MB) into %d chunk(s) of ~%.0f s each",
        audio_path.name, file_size / 1024 / 1024, num_chunks, chunk_duration,
    )
    return chunk_paths


# ---------------------------------------------------------------------------
# Groq transcription
# ---------------------------------------------------------------------------

def _parse_retry_after(exc: Exception) -> float:
    """
    Extract the Retry-After value (seconds) from a Groq exception.

    Falls back to _GROQ_RETRY_BASE_DELAY if the header is absent or unreadable.
    """
    try:
        headers = exc.response.headers  # type: ignore[attr-defined]
        for header in ("retry-after", "x-ratelimit-reset-requests"):
            val = headers.get(header)
            if val:
                return float(val)
    except (AttributeError, ValueError, TypeError):
        pass
    return _GROQ_RETRY_BASE_DELAY


def _groq_transcribe_file(client: object, audio_path: Path) -> str:
    """
    Send one file to the Groq API and return the transcript text.

    On 429 with Retry-After < 60 s: sleeps and retries.
    On 429 with Retry-After ≥ 60 s: raises RateLimitError immediately.
    On other errors: retries with exponential back-off.
    Raises the last exception if all retries are exhausted.
    """
    last_exc: Optional[Exception] = None

    for attempt in range(_GROQ_MAX_RETRIES):
        try:
            with audio_path.open("rb") as fh:
                response = client.audio.transcriptions.create(  # type: ignore[attr-defined]
                    model="whisper-large-v3",
                    file=fh,
                    response_format="text",
                )
            return response  # type: ignore[return-value]  # response_format="text" → str
        except Exception as exc:  # noqa: BLE001
            exc_str = str(exc).lower()
            is_rate_limit = (
                "429" in exc_str
                or "rate limit" in exc_str
                or "rate_limit" in exc_str
                or "ratelimit" in exc_str
            )
            if is_rate_limit:
                retry_after = _parse_retry_after(exc)
                if retry_after >= 60:
                    raise RateLimitError(
                        f"Groq rate-limited — Retry-After={retry_after:.0f}s, stopping run"
                    ) from exc
                logger.info(
                    "Groq rate-limited on attempt %d/%d — waiting %.0f s",
                    attempt + 1, _GROQ_MAX_RETRIES, retry_after,
                )
                time.sleep(retry_after)
            else:
                delay = _GROQ_RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "Groq attempt %d/%d failed for %s — retrying in %d s: %s",
                    attempt + 1, _GROQ_MAX_RETRIES, audio_path.name, delay, exc,
                )
                if attempt < _GROQ_MAX_RETRIES - 1:
                    time.sleep(delay)
            last_exc = exc

    raise last_exc  # type: ignore[misc]


def _transcribe_with_groq(audio_path: Path, article_id: int) -> tuple[str, float]:
    """
    Transcribe via Groq.  Returns (transcript_text, audio_seconds).

    Chunks files that exceed the 24 MB per-request limit and concatenates
    the results.  Raises RateLimitError on long 429s; RuntimeError if Groq
    is misconfigured or the groq package is missing.
    """
    try:
        from groq import Groq  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError("groq package not installed — run: pip install groq") from exc

    settings = get_settings()
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not set in .env")

    client = Groq(api_key=settings.groq_api_key)
    file_size = audio_path.stat().st_size

    try:
        audio_seconds = _get_audio_duration(audio_path)
    except Exception:
        audio_seconds = 0.0

    logger.info(
        "Groq transcription article id=%d: %.1f MB, %.0f s audio",
        article_id, file_size / 1024 / 1024, audio_seconds,
    )

    if file_size <= _GROQ_SIZE_LIMIT_BYTES:
        text = _groq_transcribe_file(client, audio_path)
    else:
        logger.info("Audio exceeds 24 MB — chunking")
        chunk_dir = audio_path.parent / "chunks"
        chunk_dir.mkdir(exist_ok=True)
        try:
            chunks = _chunk_audio(audio_path, chunk_dir)
            parts: list[str] = []
            for i, chunk in enumerate(chunks, start=1):
                logger.info("Transcribing chunk %d/%d: %s", i, len(chunks), chunk.name)
                parts.append(_groq_transcribe_file(client, chunk).strip())
            text = " ".join(parts)
        finally:
            for chunk_file in chunk_dir.glob("*"):
                try:
                    chunk_file.unlink()
                except OSError:
                    pass
            try:
                chunk_dir.rmdir()
            except OSError:
                pass

    return (text.strip() if text and text.strip() else ""), audio_seconds


# ---------------------------------------------------------------------------
# Budget check
# ---------------------------------------------------------------------------

def _has_budget() -> bool:
    """Return True if enough Groq quota remains for another transcription."""
    usage = db.get_groq_usage()
    if (
        usage["remaining_seconds_hour"] < _GROQ_BUDGET_MIN_SECONDS
        or usage["remaining_seconds_day"] < _GROQ_BUDGET_MIN_SECONDS
    ):
        logger.warning(
            "Groq budget low: %.0f s/hr remaining, %.0f s/day remaining",
            usage["remaining_seconds_hour"], usage["remaining_seconds_day"],
        )
        return False
    return True


# ---------------------------------------------------------------------------
# On-demand transcription (single article)
# ---------------------------------------------------------------------------

def transcribe_article(article_id: int) -> dict:
    """
    Transcribe a single article on demand and return the result.

    Fetches the article from the DB, downloads the audio, transcribes via
    Groq, saves the transcript and logs the usage.

    Returns:
        dict with 'transcript' (str), 'word_count' (int), 'audio_seconds' (float)

    Raises:
        RateLimitError  — Groq rate-limited with a long delay or budget exhausted
        RuntimeError    — article not found, no audio URL, download failed, etc.
    """
    row = db.get_article_by_id(article_id)
    if row is None:
        raise RuntimeError(f"Article {article_id} not found")

    article = dict(row)
    audio_url = article.get("audio_url") or ""
    if not audio_url:
        raise RuntimeError("No audio URL for this article")

    if not _has_budget():
        raise RateLimitError("Groq budget exhausted — try again later")

    tmp_dir = Path(tempfile.mkdtemp(prefix="digest_groq_"))
    audio_path: Optional[Path] = None

    try:
        audio_path = _download_audio(audio_url, tmp_dir)

        # Reject trailers and promos — real podcast episodes are always > 2 MB
        file_size = audio_path.stat().st_size
        if file_size < _MIN_AUDIO_SIZE_BYTES:
            size_mb = file_size / 1024 / 1024
            raise RuntimeError(
                f"Audio too short ({size_mb:.1f} MB) — likely a trailer or promo, skipping"
            )

        transcript, audio_seconds = _transcribe_with_groq(audio_path, article_id)
    except RateLimitError:
        raise
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Audio download failed: {exc}") from exc
    finally:
        if audio_path and audio_path.exists():
            try:
                audio_path.unlink()
            except OSError:
                pass
        try:
            tmp_dir.rmdir()
        except OSError:
            pass

    if not transcript:
        raise RuntimeError("Groq returned an empty transcript")

    db.save_transcription(article_id, transcript)
    db.log_transcription(article_id, audio_seconds)

    word_count = len(transcript.split())
    logger.info(
        "On-demand transcription complete: article id=%d, %d words, %.0f s audio",
        article_id, word_count, audio_seconds,
    )
    return {"transcript": transcript, "word_count": word_count, "audio_seconds": audio_seconds}


# ---------------------------------------------------------------------------
# Batch transcription (pipeline run)
# ---------------------------------------------------------------------------

def transcribe_pending(max_episodes: int = _DEFAULT_MAX_EPISODES) -> list[int]:
    """
    Transcribe articles flagged with needs_transcription=1, up to max_episodes.

    Uses the Groq cloud API exclusively.  Stops the run early if Groq returns
    a long rate-limit delay (Retry-After ≥ 60 s) or if the budget is exhausted.

    Each audio file is downloaded to a temporary directory and deleted
    immediately after transcription to avoid filling disk.

    Returns the list of article ids that were successfully transcribed.
    """
    settings = get_settings()
    if not settings.groq_api_key:
        logger.error("GROQ_API_KEY is not set — transcription unavailable")
        return []

    all_pending = db.get_articles_needing_transcription()
    if not all_pending:
        logger.info("No articles pending transcription")
        return []

    pending = all_pending[:max_episodes]
    if len(all_pending) > max_episodes:
        logger.info(
            "Found %d article(s) pending transcription — processing first %d, "
            "%d will carry over",
            len(all_pending), max_episodes, len(all_pending) - max_episodes,
        )
    else:
        logger.info("Found %d article(s) pending transcription", len(pending))

    transcribed_ids: list[int] = []

    for row in pending:
        article_id: int = row["id"]
        audio_url: Optional[str] = row["audio_url"]
        source_name: str = row["source_name"]

        if not audio_url:
            logger.warning(
                "[%s] Article id=%d has needs_transcription=1 but no audio_url — skipping",
                source_name, article_id,
            )
            continue

        if not _has_budget():
            logger.info("Groq budget exhausted — stopping batch run")
            break

        tmp_dir = Path(tempfile.mkdtemp(prefix="digest_groq_"))
        audio_path: Optional[Path] = None

        try:
            try:
                audio_path = _download_audio(audio_url, tmp_dir)
            except requests.exceptions.ConnectionError as exc:
                logger.error(
                    "[%s] Article id=%d download connection error: %s",
                    source_name, article_id, exc,
                )
                continue
            except requests.exceptions.Timeout:
                logger.error(
                    "[%s] Article id=%d download timed out after %d s",
                    source_name, article_id, _DOWNLOAD_TIMEOUT,
                )
                continue
            except requests.exceptions.HTTPError as exc:
                logger.error(
                    "[%s] Article id=%d download HTTP error: %s",
                    source_name, article_id, exc,
                )
                continue

            # Skip trailers / promos — real podcast episodes are always > 2 MB
            if audio_path.stat().st_size < _MIN_AUDIO_SIZE_BYTES:
                size_mb = audio_path.stat().st_size / 1024 / 1024
                logger.info(
                    "[%s] Skipping article id=%d: audio too short (%.1f MB) — likely a trailer",
                    source_name, article_id, size_mb,
                )
                try:
                    db.clear_needs_transcription(article_id)
                except RuntimeError:
                    pass
                continue

            try:
                transcript, audio_seconds = _transcribe_with_groq(audio_path, article_id)
            except RateLimitError as exc:
                logger.warning(
                    "[%s] Groq rate-limit hit — stopping batch run: %s",
                    source_name, exc,
                )
                break

            if not transcript:
                logger.warning(
                    "[%s] Article id=%d: Groq returned empty transcript",
                    source_name, article_id,
                )
                continue

            db.save_transcription(article_id, transcript)
            db.log_transcription(article_id, audio_seconds)
            transcribed_ids.append(article_id)
            logger.info(
                "[%s] Article id=%d transcribed: %d words, %.0f s audio",
                source_name, article_id, len(transcript.split()), audio_seconds,
            )

        except Exception as exc:
            logger.error(
                "[%s] Unexpected error transcribing article id=%d: %s",
                source_name, article_id, exc,
                exc_info=True,
            )

        finally:
            if audio_path and audio_path.exists():
                try:
                    audio_path.unlink()
                except OSError as exc:
                    logger.warning("Could not delete temp file %s: %s", audio_path, exc)
            try:
                tmp_dir.rmdir()
            except OSError:
                pass

    logger.info(
        "Transcription run complete: %d/%d succeeded",
        len(transcribed_ids), len(pending),
    )
    return transcribed_ids
