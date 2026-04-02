"""
Telegram notification delivery for pipeline run summaries.

Sends a MarkdownV2-formatted message to a single configured chat after each
ingestion cycle.  If TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID are absent from
.env, all public functions are silent no-ops — the pipeline is never blocked.

Public API
----------
send_pipeline_notification(...)  — full run summary (ingestion + articles + budget)
send_error_notification(exc)     — short critical-error alert
"""

import logging
import re
from datetime import datetime, UTC
from typing import Optional

import requests

from src.config.settings import get_settings
from src.storage import db

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_MAX_LEN = 4096
_REQUEST_TIMEOUT = 10


# ---------------------------------------------------------------------------
# MarkdownV2 helpers
# ---------------------------------------------------------------------------

def _e(text: str) -> str:
    """
    Escape a plain-text string for Telegram MarkdownV2.

    All special characters outside formatting entities must be escaped with a
    backslash.  Special chars: _ * [ ] ( ) ~ ` > # + - = | { } . ! \\
    """
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!\\])', r'\\\1', str(text))  # noqa: W605


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _send_raw(token: str, chat_id: str, text: str) -> bool:
    """POST one message to the Telegram Bot API. Returns True on success."""
    url = _TELEGRAM_API.format(token=token)
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "MarkdownV2",
                "disable_web_page_preview": True,
            },
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("ok"):
            logger.warning("Telegram API returned ok=false: %s", payload)
            return False
        return True
    except requests.RequestException as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False


def _send_parts(token: str, chat_id: str, parts: list[str]) -> None:
    """
    Combine message parts into ≤4096-char messages, sending sequentially.

    Parts are joined with a blank line.  If a single part exceeds the limit
    it is truncated with an ellipsis marker.
    """
    buf = ""
    for part in parts:
        sep = "\n\n" if buf else ""
        candidate = buf + sep + part
        if len(candidate) <= _MAX_LEN:
            buf = candidate
        else:
            if buf:
                _send_raw(token, chat_id, buf)
            if len(part) > _MAX_LEN:
                part = part[:_MAX_LEN - 5] + "\n\\.\\.\\."
            buf = part
    if buf:
        _send_raw(token, chat_id, buf)


# ---------------------------------------------------------------------------
# Content helpers
# ---------------------------------------------------------------------------

def _first_paragraph(text: str, max_chars: int = 420) -> str:
    """
    Extract the first substantive paragraph from a markdown feed summary.

    Skips heading lines (###), strips bold/italic markers, and truncates at a
    word boundary if the paragraph exceeds max_chars.  Returns an empty string
    if no qualifying paragraph is found.
    """
    for block in re.split(r'\n{2,}', text.strip()):
        # Skip heading lines
        clean = re.sub(r'^#{1,6}\s+', '', block.strip())
        # Strip markdown bold/italic markers
        clean = re.sub(r'\*\*([^*]+)\*\*', r'\1', clean)
        clean = re.sub(r'\*([^*]+)\*', r'\1', clean)
        clean = clean.strip()
        if len(clean) < 60:
            continue  # too short to be substantive
        if len(clean) <= max_chars:
            return clean
        cut = clean[:max_chars]
        last_space = cut.rfind(' ')
        return (cut[:last_space] if last_space > max_chars // 2 else cut) + '…'
    return ''


def _credentials() -> tuple[Optional[str], Optional[str]]:
    """Return (token, chat_id) from settings, or (None, None) if not configured."""
    s = get_settings()
    return s.telegram_bot_token, s.telegram_chat_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_pipeline_notification(
    start_time: datetime,
    rss_ok: int,
    rss_total: int,
    taddy_ok: int,
    taddy_total: int,
    scraped: int,
    transcribed: int,
) -> None:
    """
    Send a full pipeline run summary to Telegram.

    Fetches feed summary, transcription log, recent articles, and budget data
    from the database itself — the caller only needs to supply the per-step
    counts accumulated during the run.

    No-op (with a DEBUG log) if credentials are absent.
    """
    token, chat_id = _credentials()
    if not token or not chat_id:
        logger.debug("Telegram credentials not configured — skipping notification")
        return

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    run_ts = _e(start_time.strftime("%d %b %Y %H:%M UTC"))
    elapsed_s = int((datetime.now(UTC) - start_time).total_seconds())
    elapsed = _e(f"{elapsed_s // 60}m {elapsed_s % 60}s")

    # DB lookups — all failures caught by the caller's try/except
    groq_raw = db.get_groq_usage()
    gemini_raw = db.get_gemini_usage()
    groq_hr = round(groq_raw["remaining_seconds_hour"] / 60, 1)
    groq_day = round(groq_raw["remaining_seconds_day"] / 60, 1)
    gem_rem = gemini_raw["remaining_today"]
    gem_lim = gemini_raw["limit_today"]

    feed_row = db.get_feed_summary(today)
    tx_rows = db.get_recent_transcriptions(hours=4)
    articles = db.get_recent_summarised_articles(hours=25)

    # ── Part 1: header + ingestion stats ─────────────────────────────────────

    part1 = "\n".join([
        f"*🗞 Daily Digest \u2014 {run_ts}*",
        "",
        f"📥 *Ingested* \\({elapsed}\\)",
        f"Newsletters: *{rss_ok}*\\/{_e(str(rss_total))}  ·  "
        f"Podcasts: *{taddy_ok}*\\/{_e(str(taddy_total))}",
        f"Scraped: *{scraped}*  ·  Transcribed: *{transcribed}*",
    ])

    # ── Part 2: feed summary excerpt ─────────────────────────────────────────

    part2_lines: list[str] = []
    if feed_row:
        news_text: Optional[str] = (
            feed_row["news_summary"] if "news_summary" in feed_row.keys() else None
        )
        informative_text: Optional[str] = (
            feed_row["informative_summary"]
            if "informative_summary" in feed_row.keys()
            else None
        )
        if news_text or informative_text:
            part2_lines.append("📋 *Feed Summary*")
        if news_text:
            excerpt = _first_paragraph(news_text, max_chars=400)
            if excerpt:
                part2_lines.append(_e(excerpt))
        if informative_text:
            excerpt = _first_paragraph(informative_text, max_chars=280)
            if excerpt:
                part2_lines.append("_Informative:_ " + _e(excerpt))

    part2 = "\n".join(part2_lines)

    # ── Part 3: article list (grouped by content_category) ───────────────────

    news_arts = [a for a in articles if (a["content_category"] or "").lower() == "news"]
    info_arts = [a for a in articles if (a["content_category"] or "").lower() != "news"]
    total = len(articles)
    limit = 15

    part3_lines: list[str] = []
    shown = 0

    if news_arts:
        part3_lines.append(f"📰 *News* \\({len(news_arts)}\\)")
        for art in news_arts[:min(8, limit)]:
            src = _e((art["source_name"] or "")[:30])
            title = _e((art["title"] or "")[:55])
            part3_lines.append(f"• {src}: {title}")
            shown += 1

    remaining = limit - shown
    if info_arts and remaining > 0:
        if part3_lines:
            part3_lines.append("")
        part3_lines.append(f"💡 *Informative* \\({len(info_arts)}\\)")
        for art in info_arts[:remaining]:
            src = _e((art["source_name"] or "")[:30])
            title = _e((art["title"] or "")[:55])
            part3_lines.append(f"• {src}: {title}")
            shown += 1

    if total > limit:
        part3_lines.append(f"_\\+{total - limit} more_")

    part3 = "\n".join(part3_lines)

    # ── Part 4: transcription log + budget ───────────────────────────────────

    part4_lines: list[str] = []

    if tx_rows:
        part4_lines.append("🎙 *Transcribed this run*")
        for tx in tx_rows[:5]:
            mins = int(tx["audio_seconds"] // 60)
            secs = int(tx["audio_seconds"] % 60)
            dur = _e(f"{mins}m {secs}s" if mins else f"{secs}s")
            src = _e((tx["source_name"] or "")[:28])
            title = _e((tx["title"] or "")[:48])
            part4_lines.append(f"• {src}: {title} \\({dur}\\)")
        part4_lines.append("")

    part4_lines += [
        "💰 *Budget*",
        f"Groq: {_e(str(groq_hr))}m\\/hr  ·  {_e(str(groq_day))}m\\/day remaining",
        f"Gemini: {_e(str(gem_rem))}\\/{_e(str(gem_lim))} calls remaining",
    ]

    part4 = "\n".join(part4_lines)

    # ── Send ─────────────────────────────────────────────────────────────────

    _send_parts(token, chat_id, [p for p in [part1, part2, part3, part4] if p])
    logger.info("Telegram pipeline notification sent")


def send_error_notification(exc: BaseException) -> None:
    """
    Send a short critical-error alert to Telegram.

    No-op if credentials are absent.  Never raises.
    """
    token, chat_id = _credentials()
    if not token or not chat_id:
        return

    ts = _e(datetime.now(UTC).strftime("%d %b %Y %H:%M UTC"))
    err_type = _e(type(exc).__name__)
    err_msg = _e(str(exc)[:350])

    text = (
        f"*🚨 Pipeline Error \u2014 {ts}*\n\n"
        f"*{err_type}*\n"
        f"{err_msg}"
    )
    try:
        _send_raw(token, chat_id, text)
    except Exception as inner:
        logger.warning("Could not send Telegram error notification: %s", inner)
