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
    scraped: int,
    transcribed: int,
) -> None:
    """
    Send a pipeline run summary to Telegram.

    No-op (with a DEBUG log) if credentials are absent.
    """
    token, chat_id = _credentials()
    if not token or not chat_id:
        logger.debug("Telegram credentials not configured — skipping notification")
        return

    run_ts = _e(start_time.strftime("%d %b %Y %H:%M UTC"))

    groq_raw = db.get_groq_usage()
    gemini_raw = db.get_gemini_usage()
    groq_hr = round(groq_raw["remaining_seconds_hour"] / 60, 1)
    groq_day = round(groq_raw["remaining_seconds_day"] / 60, 1)
    gem_rem = gemini_raw["remaining_today"]

    new_articles = db.get_articles_ingested_since(start_time)
    n_newsletters = sum(1 for a in new_articles if (a["source_type"] or "") != "podcast")
    n_podcasts    = sum(1 for a in new_articles if (a["source_type"] or "") == "podcast")
    news_new = [a for a in new_articles if (a["content_category"] or "").lower() == "news"]
    info_new = [a for a in new_articles if (a["content_category"] or "").lower() != "news"]

    today_articles = db.get_recent_summarised_articles(hours=25)
    n_news_today = sum(1 for a in today_articles if (a["content_category"] or "").lower() == "news")
    n_info_today = sum(1 for a in today_articles if (a["content_category"] or "").lower() != "news")

    lines: list[str] = []

    lines += [
        f"*Pipeline Run* \u2014 {run_ts}",
        f"Ingested: {_e(str(n_newsletters))} newsletters, {_e(str(n_podcasts))} podcasts",
        f"Scraped: {_e(str(scraped))} · Transcribed: {_e(str(transcribed))}",
        "",
    ]

    lines.append("*News \\(new this run\\)*")
    if news_new:
        for a in news_new[:15]:
            lines.append(f"• {_e((a['title'] or '')[:60])} \u2014 {_e((a['source_name'] or '')[:30])}")
    else:
        lines.append("No new news articles")
    lines.append("")

    lines.append("*Informative \\(new this run\\)*")
    if info_new:
        for a in info_new[:15]:
            lines.append(f"• {_e((a['title'] or '')[:60])} \u2014 {_e((a['source_name'] or '')[:30])}")
    else:
        lines.append("No new informative articles")
    lines.append("")

    lines += [
        "*Feed today*",
        f"{_e(str(n_news_today))} news · {_e(str(n_info_today))} informative",
        "",
        "*Budget*",
        f"Groq: {_e(str(groq_hr))}m\\/hr · {_e(str(groq_day))}m\\/day",
        f"Gemini: {_e(str(gem_rem))}\\/500 RPD",
    ]

    _send_raw(token, chat_id, "\n".join(lines)[:_MAX_LEN])
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
