"""
Twilio WhatsApp delivery module.

Responsibilities:
  • Format the daily digest as a WhatsApp-friendly plain-text message.
  • Send it to MY_WHATSAPP_NUMBER via the Twilio WhatsApp API.
  • Handle incoming on-demand commands:
      "digest"            → send the latest digest
      "summary <topic>"   → send just that topic's articles
      anything else       → send a help message
  • Split messages that exceed WhatsApp's practical per-message limit.
  • Log delivery status (sent, failed, delivered via callback).

Twilio configuration:
  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM, MY_WHATSAPP_NUMBER
  must all be set in .env for delivery to work.
"""

import logging
from datetime import datetime, UTC
from typing import Optional

from src.config.settings import get_settings
from src.storage import db

logger = logging.getLogger(__name__)

# WhatsApp messages are capped at 4096 chars by the spec; stay slightly under
# so splitting doesn't produce a stub fragment.
_MAX_MESSAGE_CHARS = 4000

_TOPIC_EMOJI: dict[str, str] = {
    "AI": "🤖",
    "Tech": "💻",
    "Markets": "📈",
    "Macro / Economics": "🌍",
    "Startups / VC": "🚀",
}
_DEFAULT_EMOJI = "📌"

_TOPIC_ORDER = ["AI", "Tech", "Markets", "Macro / Economics", "Startups / VC"]


# ---------------------------------------------------------------------------
# Twilio client
# ---------------------------------------------------------------------------

def _get_twilio_client():
    """Return an authenticated Twilio REST client."""
    try:
        from twilio.rest import Client  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError("twilio is not installed. Run: pip install twilio") from exc

    s = get_settings()
    if not all([s.twilio_account_sid, s.twilio_auth_token]):
        raise RuntimeError(
            "Twilio credentials not configured. "
            "Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN in .env"
        )
    return Client(s.twilio_account_sid, s.twilio_auth_token)


def _whatsapp_number(raw: str) -> str:
    """Ensure number is prefixed with whatsapp:."""
    return raw if raw.startswith("whatsapp:") else f"whatsapp:{raw}"


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def _format_digest_for_whatsapp(digest_content: str, date_label: Optional[str] = None) -> str:
    """
    Reformat the stored digest text into WhatsApp-friendly plain text.

    The digest content is already structured plain text.  We reformat it
    lightly: use emoji headers, replace section dividers, and strip markdown.
    """
    if date_label is None:
        date_label = datetime.now(UTC).strftime("%d %b %Y").lstrip("0")

    lines = digest_content.splitlines()
    out_lines: list[str] = [f"📰 Daily Digest — {date_label}", ""]

    skip_next = False
    for line in lines:
        if skip_next:
            skip_next = False
            continue

        # Skip the plain-text title and its underline (we replaced them above)
        stripped = line.strip()
        if stripped.startswith("Daily Digest —") or set(stripped) == {"="}:
            skip_next = (set(stripped) != {"="})
            continue

        # Topic headers: convert "AI" → "🤖 AI"
        upper = stripped.upper()
        matched_topic = next(
            (t for t in _TOPIC_EMOJI if t.upper() == upper), None
        )
        if matched_topic:
            emoji = _TOPIC_EMOJI[matched_topic]
            out_lines.append(f"\n{emoji} {matched_topic}")
            continue

        out_lines.append(line)

    return "\n".join(out_lines).strip()


def _format_topic_for_whatsapp(topic: str, since_hours: int = 25) -> str:
    """Format a single topic's articles as a WhatsApp message."""
    from datetime import timedelta

    since = datetime.now(UTC) - timedelta(hours=since_hours)
    rows = db.get_articles_filtered(topic=topic, since=since)
    summarised = [r for r in rows if r["summary"]]

    if not summarised:
        return f"No recent articles found for topic: {topic}"

    emoji = _TOPIC_EMOJI.get(topic, _DEFAULT_EMOJI)
    lines = [f"{emoji} {topic}\n"]

    for row in summarised:
        lines.append(f"• {row['title']}")
        lines.append(f"  {row['summary']}")
        if row["url"]:
            lines.append(f"  {row['url']}")
        lines.append("")

    return "\n".join(lines).strip()


def _split_message(text: str) -> list[str]:
    """
    Split a long message into chunks that fit within _MAX_MESSAGE_CHARS.

    Splits preferentially at double-newlines (paragraph/section boundaries)
    to avoid cutting in the middle of an article summary.
    """
    if len(text) <= _MAX_MESSAGE_CHARS:
        return [text]

    parts: list[str] = []
    current = ""

    # Split at paragraph boundaries first
    paragraphs = text.split("\n\n")
    for para in paragraphs:
        candidate = current + ("\n\n" if current else "") + para
        if len(candidate) <= _MAX_MESSAGE_CHARS:
            current = candidate
        else:
            if current:
                parts.append(current.strip())
            # Handle a single paragraph that itself exceeds the limit
            if len(para) > _MAX_MESSAGE_CHARS:
                # Hard-split at the char limit
                for i in range(0, len(para), _MAX_MESSAGE_CHARS):
                    parts.append(para[i : i + _MAX_MESSAGE_CHARS])
                current = ""
            else:
                current = para

    if current:
        parts.append(current.strip())

    # Label multi-part messages
    if len(parts) > 1:
        parts = [f"({i}/{len(parts)}) {p}" for i, p in enumerate(parts, start=1)]

    return parts


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def send_message(to_number: str, text: str) -> None:
    """
    Send one or more WhatsApp messages to to_number.

    Long messages are split automatically.  Each part is sent separately;
    Twilio delivers them in order but the user may see them as separate
    bubbles.
    """
    s = get_settings()
    if not s.twilio_whatsapp_from:
        raise RuntimeError("TWILIO_WHATSAPP_FROM is not set in .env")

    client = _get_twilio_client()
    from_wa = _whatsapp_number(s.twilio_whatsapp_from)
    to_wa = _whatsapp_number(to_number)

    parts = _split_message(text)
    for i, part in enumerate(parts, start=1):
        try:
            msg = client.messages.create(from_=from_wa, to=to_wa, body=part)
            logger.info(
                "WhatsApp sent to %s (part %d/%d) — SID: %s",
                to_number, i, len(parts), msg.sid,
            )
        except Exception as exc:
            logger.error("WhatsApp send failed (part %d/%d): %s", i, len(parts), exc)
            raise


def send_daily_digest() -> None:
    """
    Fetch the latest digest and send it to MY_WHATSAPP_NUMBER.

    Marks the digest as delivered on success.
    """
    s = get_settings()
    if not s.my_whatsapp_number:
        raise RuntimeError("MY_WHATSAPP_NUMBER is not set in .env")

    row = db.get_latest_digest()
    if row is None:
        logger.warning("No digest available to send")
        return

    text = _format_digest_for_whatsapp(row["content"])
    send_message(s.my_whatsapp_number, text)

    db.mark_digest_delivered(row["id"], "whatsapp")
    logger.info("Daily digest (id=%d) delivered via WhatsApp", row["id"])


# ---------------------------------------------------------------------------
# On-demand incoming message handler
# ---------------------------------------------------------------------------

def handle_incoming_message(from_number: str, body: str) -> None:
    """
    Respond to an inbound WhatsApp command.

    Supported commands (case-insensitive):
      "digest"            → send the latest digest
      "summary <topic>"   → send the last 25h of articles for that topic
      anything else       → send a help message
    """
    command = body.strip().lower()

    if command == "digest":
        row = db.get_latest_digest()
        if row is None:
            send_message(from_number, "No digest available yet. Check back later.")
            return
        text = _format_digest_for_whatsapp(row["content"])
        send_message(from_number, text)
        return

    if command.startswith("summary "):
        topic_raw = body.strip()[8:].strip()  # preserve original casing
        # Fuzzy-match against known topics (case-insensitive)
        topic = next(
            (t for t in _TOPIC_ORDER if t.lower() == topic_raw.lower()),
            topic_raw,  # fall back to whatever the user typed
        )
        text = _format_topic_for_whatsapp(topic)
        send_message(from_number, text)
        return

    # Help response
    topics_list = ", ".join(_TOPIC_ORDER)
    help_text = (
        "📰 Daily Digest Bot\n\n"
        "Commands:\n"
        "• digest — get today's full digest\n"
        f"• summary <topic> — get recent articles for a topic\n\n"
        f"Topics: {topics_list}"
    )
    send_message(from_number, help_text)
