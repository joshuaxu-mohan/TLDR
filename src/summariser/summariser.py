"""
AI summarisation module — per-article summaries and daily digest assembly.

Two-phase workflow:
  1. summarise_new_articles() — fetches unsummarised articles from the DB,
     sends them to Gemini in batches, writes summary + adjusted topic_tags +
     is_significant flag back to each article row.
  2. generate_daily_digest() — fetches recently summarised articles, groups
     them by topic, assembles a structured plain-text digest, and saves it to
     the digests table.

Gemini API rate limits are handled with exponential backoff.  Large batches are
chunked so the total input tokens per request stay comfortable.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, UTC
from typing import Any, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors

load_dotenv()

from src.storage import db  # noqa: E402 — must come after load_dotenv

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BATCH_SIZE = 5          # articles per Gemini API call
_MAX_RETRIES = 5
_RETRY_BASE_DELAY = 5    # seconds — for rate-limit (429) errors; doubled on each attempt
_SERVER_ERROR_BASE_DELAY = 10  # seconds — for server errors (5xx); longer cooldown needed

# Model selection
# 3.1 Flash Lite preview: 15 RPM, 500 RPD free tier — best throughput on free tier
# (verified via client.models.list(): "models/gemini-3.1-flash-lite-preview")
# PREMIUM_MODEL = "gemini-2.5-flash"  # 5 RPM, 20 RPD free tier — quality-critical use only
_GEMINI_MODEL = "gemini-3.1-flash-lite-preview"

# Proactive inter-request pacing — keeps us safely under the 15 RPM limit
# (5 s between calls = max 12 RPM, giving a 3-request headroom)
_REQUEST_DELAY_SECONDS = 5.0

# Content-aware truncation limits (words)
# Applying the right limit per source type avoids wasting TPM quota on padding
# while still giving transcripts enough context for a useful summary.
_TRUNCATION_LONG = 6000    # podcast transcripts (60+ min episodes)
_TRUNCATION_MEDIUM = 2000  # newsletter / Substack articles
_TRUNCATION_SHORT = 500    # episode descriptions / metadata-only stubs

_CORE_TOPICS = ["AI", "Tech", "Markets", "Macro / Economics", "Startups / VC"]

_TOPIC_ORDER = ["AI", "Tech", "Markets", "Macro / Economics", "Startups / VC"]

_SYSTEM_PROMPT = """\
You are a content digest assistant for a professional who follows tech, AI, markets, \
economics, and startups.

For each article given to you, produce:
1. A 2-3 sentence summary capturing the single most important insight or development.
2. Adjusted topic tags based on the actual content (not just the current tags).
   Core topics — prefer these: Tech, AI, Markets, Macro / Economics, Startups / VC.
   An article may have multiple tags.  Create a new topic only when the content \
genuinely does not fit any core topic.
   Remove tags that do not apply; add tags that do.
3. A boolean indicating whether this is a particularly significant development \
worth highlighting at the top of the digest.

Return ONLY a single valid JSON object — no preamble, no trailing text — matching \
this schema exactly:
{
  "articles": [
    {
      "id": <integer>,
      "summary": "<2-3 sentence summary>",
      "topic_tags": ["<tag>"],
      "is_significant": <true|false>
    }
  ]
}
"""


# ---------------------------------------------------------------------------
# Gemini API helpers
# ---------------------------------------------------------------------------

def _get_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to .env — get a free key at "
            "https://aistudio.google.com/app/apikey"
        )
    return genai.Client(api_key=api_key)


def _truncate_content(content: str, source_type: str, article_id: int) -> str:
    """
    Apply content-aware word-level truncation.

    Truncation tiers:
    - Under 500 words: pass through unchanged (likely an episode description stub)
    - Substack / newsletter source: cap at TRUNCATION_MEDIUM (2000 words)
    - 2000+ words regardless of source type: cap at TRUNCATION_LONG (6000 words)
      (handles podcast transcripts and unusually long newsletter posts)

    The order of checks means a podcast transcript that is under 500 words
    (e.g. a very short episode description) is also passed through unchanged.
    """
    words = content.split()
    original_count = len(words)

    if original_count <= _TRUNCATION_SHORT:
        return content  # already compact — no truncation needed

    if source_type == "substack":
        limit = _TRUNCATION_MEDIUM
    elif original_count > _TRUNCATION_MEDIUM:
        limit = _TRUNCATION_LONG
    else:
        return content  # between 500 and 2000 words, non-newsletter: send as-is

    if original_count > limit:
        logger.debug(
            "Truncated article id=%d from %d to %d words (source_type=%s)",
            article_id, original_count, limit, source_type,
        )
        return " ".join(words[:limit]) + "\n[truncated]"

    return content


_DESCRIPTION_TIER_NOTE = (
    "[DESCRIPTION ONLY — no transcript available. "
    "Based solely on the title and description, write a 2-3 sentence summary of what the "
    "episode likely covers, naming any guests and the main topic. "
    "If the description is too vague to summarise meaningfully, "
    'set the summary field to exactly: SKIP]'
)

_TRANSCRIPT_TIER_NOTE = (
    "[FULL TRANSCRIPT — produce a fuller 4-6 sentence summary. "
    "Capture the key arguments, notable points, and any specific data, names, or conclusions "
    "that stand out. Go beyond a surface description to convey what was actually said.]"
)


def _build_prompt(rows: list[dict[str, Any]]) -> str:
    """Combine the system instructions and article batch into a single prompt string."""
    parts: list[str] = [
        _SYSTEM_PROMPT,
        "\nSummarise each article and adjust its topic tags.\n",
    ]
    for i, row in enumerate(rows, start=1):
        priority: str = row.get("transcript_priority") or "always"
        content = _truncate_content(
            content=row.get("content") or "",
            source_type=row.get("source_type") or "unknown",
            article_id=row["id"],
        )
        word_count = len(content.split())
        # Per-article instruction overrides —
        # description-tier note takes precedence; otherwise add transcript note
        # for long content (real transcripts / full newsletter articles).
        if priority == "description":
            tier_note = f"\n{_DESCRIPTION_TIER_NOTE}\n"
        elif word_count < _TRUNCATION_SHORT:
            # Short content (description stub saved for always-priority episodes):
            # treat the same as description-tier for prompt purposes.
            tier_note = f"\n{_DESCRIPTION_TIER_NOTE}\n"
        else:
            tier_note = f"\n{_TRANSCRIPT_TIER_NOTE}\n"
        parts.append(
            f"Article {i} (id={row['id']}):{tier_note}\n"
            f"Title: {row['title']}\n"
            f"Current tags: {row.get('topic_tags') or 'none'}\n"
            f"Content:\n{content}\n"
        )
    return "\n---\n".join(parts)


def _call_gemini(prompt: str) -> str:
    """
    Send prompt to Gemini and return the raw response text.

    Retries on rate-limit (HTTP 429) and server errors (5xx) with
    exponential backoff.  Other API errors are re-raised immediately.
    """
    client = _get_client()

    for attempt in range(_MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=_GEMINI_MODEL,
                contents=prompt,
            )
            text = response.text
            try:
                db.log_gemini_call()
            except Exception:
                pass  # logging failure must never interrupt the call
            return text
        except genai_errors.ClientError as exc:
            # ClientError covers all 4xx responses; 429 = rate limited
            code = getattr(exc, "code", None) or getattr(exc, "status_code", 0)
            is_rate_limit = code == 429 or "429" in str(exc) or "quota" in str(exc).lower()
            if is_rate_limit and attempt < _MAX_RETRIES - 1:
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "Rate limited by Gemini API — retrying in %ds (attempt %d)",
                    delay, attempt + 1,
                )
                time.sleep(delay)
            else:
                raise
        except genai_errors.ServerError as exc:
            if attempt < _MAX_RETRIES - 1:
                delay = _SERVER_ERROR_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "Gemini API server error — retrying in %ds (attempt %d/%d): %s",
                    delay, attempt + 1, _MAX_RETRIES, exc,
                )
                time.sleep(delay)
            else:
                raise

    raise RuntimeError("Exceeded max retries calling Gemini API")  # unreachable but satisfies type checker


def _parse_gemini_response(raw: str) -> list[dict[str, Any]]:
    """
    Extract the JSON payload from Gemini's response.

    Gemini may wrap JSON in a markdown code fence; this strips it before
    parsing.  Returns an empty list on any parse failure so a bad response for
    one batch doesn't abort the entire run.
    """
    # Strip optional ```json ... ``` wrapper
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)

    try:
        data = json.loads(cleaned)
        return data.get("articles", [])
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse Gemini JSON response: %s\nRaw: %.200s", exc, raw)
        return []


# ---------------------------------------------------------------------------
# Summarisation
# ---------------------------------------------------------------------------

def summarise_new_articles() -> int:
    """
    Summarise all unsummarised articles and write results back to the database.

    Returns the count of articles successfully summarised.  Articles that fail
    (e.g. API error for that batch) are left with summary=NULL and will be
    retried on the next run.
    """
    rows = db.get_unsummarised_articles()
    if not rows:
        logger.info("No articles to summarise")
        return 0

    total_batches = (len(rows) + _BATCH_SIZE - 1) // _BATCH_SIZE
    logger.info(
        "Summarising %d article(s) in %d batch(es) of up to %d using %s",
        len(rows), total_batches, _BATCH_SIZE, _GEMINI_MODEL,
    )
    rows_as_dicts = [dict(r) for r in rows]

    success_count = 0
    requests_made = 0

    for batch_num, batch_start in enumerate(range(0, len(rows_as_dicts), _BATCH_SIZE), start=1):
        batch = rows_as_dicts[batch_start : batch_start + _BATCH_SIZE]
        batch_ids = [r["id"] for r in batch]

        # Proactive pacing: sleep before every batch except the very first.
        # This keeps throughput safely under 15 RPM without relying on
        # reactive retry backoff for the common case.
        if requests_made > 0:
            logger.info(
                "Pacing: waiting %.1fs before next batch (%d/%d RPD used)",
                _REQUEST_DELAY_SECONDS, requests_made, total_batches,
            )
            time.sleep(_REQUEST_DELAY_SECONDS)

        logger.info(
            "Summarising batch %d of %d: article ids %s with %s",
            batch_num, total_batches, batch_ids, _GEMINI_MODEL,
        )

        try:
            prompt = _build_prompt(batch)
            raw = _call_gemini(prompt)
            requests_made += 1
            results = _parse_gemini_response(raw)
        except Exception as exc:
            requests_made += 1  # count even failed requests against the budget
            logger.error(
                "Batch failed after all retries — %d article(s) lost (ids=%s): %s",
                len(batch), batch_ids, exc,
            )
            continue

        # Index results by id for O(1) lookup
        results_by_id = {r["id"]: r for r in results}

        for row in batch:
            article_id = row["id"]
            result = results_by_id.get(article_id)
            if result is None:
                logger.warning("Gemini did not return a result for article id=%d", article_id)
                continue

            summary: str = result.get("summary", "").strip()
            tags: list[str] = result.get("topic_tags", [])
            is_sig: bool = bool(result.get("is_significant", False))

            # Gemini returns "SKIP" for description-tier episodes that are too
            # vague to summarise.  Store a placeholder so the article is not
            # re-queued on the next run (summary IS NOT NULL will exclude it).
            if summary.upper() == "SKIP":
                summary = "[No transcript available — description too brief to summarise]"
                is_sig = False

            if not summary:
                logger.warning("Empty summary returned for article id=%d", article_id)
                continue

            try:
                db.save_summary(
                    article_id=article_id,
                    summary=summary,
                    topic_tags=tags,
                    is_significant=is_sig,
                )
                success_count += 1
                logger.info(
                    "Summarised article id=%d (significant=%s): %s",
                    article_id, is_sig, row["title"],
                )
            except RuntimeError as exc:
                logger.error("Failed to save summary for article id=%d: %s", article_id, exc)

    logger.info(
        "Summarisation complete: %d/%d articles summarised (%d API calls made)",
        success_count, len(rows), requests_made,
    )
    return success_count


# ---------------------------------------------------------------------------
# Digest generation
# ---------------------------------------------------------------------------

def _sort_topics(topics: list[str]) -> list[str]:
    """Return topics sorted with core topics first in canonical order."""
    ordered = [t for t in _TOPIC_ORDER if t in topics]
    extras = sorted(t for t in topics if t not in _TOPIC_ORDER)
    return ordered + extras


def generate_daily_digest(
    since: Optional[datetime] = None,
    category: Optional[str] = None,
) -> Optional[int]:
    """
    Assemble a plain-text digest from recently summarised articles.

    since        — look-back window start (default: 25 h ago)
    category     — 'news' or 'informative'; None means all categories.
                   Affects article filtering and the digest title.

    Returns the new digest row id, or None if there are no articles to digest.
    """
    if since is None:
        since = datetime.now(UTC) - timedelta(hours=25)

    rows = db.get_articles_since(since)
    # Only include articles that have been summarised
    rows = [r for r in rows if r["summary"]]

    # Filter by content_category when requested
    if category is not None:
        rows = [r for r in rows if r["content_category"] == category]

    if not rows:
        logger.info(
            "No summarised articles since %s (category=%s) — skipping digest generation",
            since, category,
        )
        return None

    logger.info("Generating %s digest from %d article(s)", category or "all", len(rows))

    # Collect highlights (significant articles)
    highlights = [r for r in rows if r["is_significant"]]

    # Group articles by topic — each article appears under all its tags
    by_topic: dict[str, list[Any]] = {}
    for row in rows:
        tags = [t.strip() for t in (row["topic_tags"] or "General").split(",") if t.strip()]
        for tag in tags:
            by_topic.setdefault(tag, []).append(row)

    _now = datetime.now(UTC)
    date_str = _now.strftime("%A, %d %B %Y").replace(" 0", " ")
    title = "News Briefing" if category == "news" else "Daily Digest"
    lines: list[str] = [
        f"{title} — {date_str}",
        "=" * len(f"{title} — {date_str}"),
        "",
    ]

    if highlights:
        lines.append("HIGHLIGHTS")
        for row in highlights:
            lines.append(f"• {row['title']}: {row['summary']}")
        lines.append("")

    for topic in _sort_topics(list(by_topic.keys())):
        topic_articles = by_topic[topic]
        lines.append(topic.upper())
        for row in topic_articles:
            lines.append(f"\n{row['title']}")
            lines.append(row["summary"])
            if row["url"]:
                lines.append(f"[{row['url']}]")
        lines.append("")

    lines.append(f"— {len(rows)} article(s) from {len(by_topic)} topic(s)")

    content = "\n".join(lines)
    digest_id = db.save_digest(content, category=category or "all")
    logger.info("Digest saved as id=%d (category=%s)", digest_id, category or "all")
    return digest_id


# ---------------------------------------------------------------------------
# Pre-computed feed summary (two-segment: news + informative)
# ---------------------------------------------------------------------------

_FEED_SUMMARY_MAX_WORDS = 4_000   # per segment
_FEED_SUMMARY_MIN_ARTICLES = 2

_NEWS_PROMPT_HEADER = """\
You are a financial news wire editor producing a morning briefing for a reader who works \
in equity research and follows markets, macro, tech, and geopolitics.

You are given AI summaries of news articles published in the last 24 hours. Produce a \
structured briefing with three sections:

DAILY BRIEFING: A 2-3 sentence overview of the day's news landscape. What defined today? \
Set the scene.

KEY THEMES: Group the most important stories by theme (e.g. trade policy, central bank \
action, tech regulation). For each theme, state what happened and why it matters in 2-3 \
sentences. Use specific data points, names, and figures where available. Only include \
themes that genuinely matter — do not force themes to fill space.

NOTABLE ITEMS: 2-4 individual stories that do not fit neatly into a theme but are worth \
flagging. One sentence each.

Rules:
- Short, declarative sentences. No hedging, no editorialising, no "it remains to be seen"
- If a story has direct market implications, state them plainly
- Do not pad with background context the reader already knows
- UK English throughout
- Output as markdown. Use ## for the three section headers. Use **bold** for key names and \
figures. Write in flowing paragraphs within each section, no bullet points
- Scale length to what happened. Quiet day = short briefing. Busy day = longer. \
Target 200-400 words total.

ARTICLES:\
"""

_INFORMATIVE_PROMPT_HEADER = """\
You are a research curator producing a daily digest for a reader who follows long-form \
podcasts and newsletters on tech, AI, markets, startups, and macro/economics.

You are given AI summaries of informative articles and podcast episodes published in the \
last 24 hours. Produce a highlights digest that helps the reader decide what to read or \
listen to in full.

Rules:
- Lead with the most intellectually interesting or surprising item
- For podcast interviews, name the guest and their key argument or thesis in one sentence
- Flag contrarian viewpoints, novel frameworks, or data that challenges consensus
- If multiple sources discuss the same theme (e.g. AI infrastructure, rate policy), group \
them and note the convergence
- End with a brief "Worth your time" line naming 1-2 items the reader should prioritise \
if they only have 30 minutes
- Do not summarise every article — prioritise what is genuinely interesting over \
comprehensiveness
- UK English throughout
- Always name the source (podcast or newsletter title) when discussing its content — \
never describe a source's argument or findings without explicitly mentioning its name
- Output as markdown. Use **bold** for source/guest names on first mention. Use short \
paragraphs. No bullet points, no numbered lists — write in flowing prose
- Target 200-400 words depending on volume. Quality over quantity.

ARTICLES:\
"""


def _build_article_block(rows: list, max_words: int = _FEED_SUMMARY_MAX_WORDS) -> str:
    """Build a truncated newline-delimited article block from DB rows."""
    lines: list[str] = []
    total = 0
    for row in rows:
        line = f"- [{row['source_name']}] {row['title']}: {row['summary']}"
        wc = len(line.split())
        if total + wc > max_words and lines:
            break
        lines.append(line)
        total += wc
    return "\n".join(lines)


def _normalise_name(text: str) -> str:
    """
    Normalise a source name or bold span for fuzzy matching.

    Strips apostrophes, smart quotes, backticks, and similar punctuation that
    Gemini may omit or replace when writing source names (e.g. "Vik's Newsletter"
    becomes "viks newsletter" for matching).  Lowercases and strips whitespace.
    """
    return re.sub(r"['''\u2018\u2019\u201b`\u00b4\"]", "", text.lower().strip())


def _add_article_links(text: str, rows: list) -> str:
    """
    Post-process Gemini markdown by converting ``**Name**`` bold spans into
    React Router article links ``[**Name**](/article/{id}?st=<type>)`` when
    the name matches a known source name from the input rows.

    Matching is case-insensitive, apostrophe-insensitive, and allows either the
    bold text to contain the source name or the source name to contain the bold
    text (handles abbreviations and truncated show names).

    The ``?st=`` query parameter carries the source type (``podcast`` or
    ``newsletter``) so the React link renderer can colour each link accordingly.

    When multiple articles share the same source, links to the most-recently
    published one (rows are ordered newest-first by the DB query).
    """
    # Build lookups: normalised name → (article_id, source_type)
    source_map: dict[str, tuple[int, str]] = {}
    title_map:  dict[str, int] = {}
    for row in rows:
        src_key   = _normalise_name(row["source_name"])
        title_key = row["title"].lower().strip()
        if src_key not in source_map:
            source_map[src_key] = (row["id"], row.get("source_type") or "")
        if title_key not in title_map:
            title_map[title_key] = row["id"]

    def _st_suffix(source_type: str) -> str:
        if source_type == "podcast":
            return "?st=podcast"
        if source_type in ("substack",):
            return "?st=newsletter"
        return ""

    def replace_match(m: re.Match) -> str:
        bold_text = m.group(1)
        key = _normalise_name(bold_text)

        # 1. Exact source-name match
        if key in source_map:
            art_id, src_type = source_map[key]
            return f"[**{bold_text}**](/article/{art_id}{_st_suffix(src_type)})"

        # 2. Substring source-name match (abbreviations / truncated show names)
        for src_name, (art_id, src_type) in source_map.items():
            if (src_name in key and len(src_name) >= 4) or (key in src_name and len(key) >= 4):
                return f"[**{bold_text}**](/article/{art_id}{_st_suffix(src_type)})"

        # 3. Title match — bold phrase appears inside an article title (guest names,
        #    episode subjects).  Require ≥6 chars to avoid spurious hits on
        #    common short words.  Source type unknown at this tier, omit ?st=.
        if len(key) >= 6:
            for title_key, art_id in title_map.items():
                if key in title_key:
                    return f"[**{bold_text}**](/article/{art_id})"

        return m.group(0)  # No match — leave unchanged

    return re.sub(r"\*\*([^*\n]+)\*\*", replace_match, text)


def generate_and_save_feed_summary() -> bool:
    """
    Generate a two-segment feed summary (news + informative) from today's
    summarised articles and persist it to the feed_summaries table.

    Sends one Gemini call per non-empty category segment.  If a category has
    no articles its segment is stored as null, saving Gemini budget.

    Called at the end of each pipeline run (both early and post-Groq phases).
    Returns True on success, False if skipped (not enough articles) or on error.
    """
    rows = db.get_recent_summarised_articles(hours=25)
    if len(rows) < _FEED_SUMMARY_MIN_ARTICLES:
        logger.info(
            "Feed summary: only %d summarised article(s) in last 25h — skipping",
            len(rows),
        )
        return False

    news_rows        = [r for r in rows if r["content_category"] == "news"]
    informative_rows = [r for r in rows if r["content_category"] != "news"]

    news_summary:        Optional[str] = None
    informative_summary: Optional[str] = None

    if news_rows:
        block = _build_article_block(news_rows)
        try:
            news_summary = _call_gemini(f"{_NEWS_PROMPT_HEADER}\n{block}")
            logger.info("Feed summary: news segment generated (%d articles)", len(news_rows))
        except Exception as exc:
            logger.error("Feed summary: news Gemini call failed: %s", exc)

    if informative_rows:
        block = _build_article_block(informative_rows)
        try:
            raw_informative = _call_gemini(f"{_INFORMATIVE_PROMPT_HEADER}\n{block}")
            # Post-process: convert **Source Name** bold spans to article links
            informative_summary = _add_article_links(raw_informative, informative_rows)
            logger.info(
                "Feed summary: informative segment generated (%d articles)",
                len(informative_rows),
            )
        except Exception as exc:
            logger.error("Feed summary: informative Gemini call failed: %s", exc)

    if news_summary is None and informative_summary is None:
        logger.warning("Feed summary: both segments failed or were empty — nothing saved")
        return False

    result = {
        "news_summary":        news_summary,
        "informative_summary": informative_summary,
        "generated_at":        datetime.now(UTC).isoformat(),
    }
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    try:
        db.save_feed_summary(date=today, summary_json=json.dumps(result))
        logger.info(
            "Feed summary saved for %s (news=%s, informative=%s)",
            today,
            "yes" if news_summary else "null",
            "yes" if informative_summary else "null",
        )
        return True
    except Exception as exc:
        logger.error("Failed to save feed summary: %s", exc)
        return False
