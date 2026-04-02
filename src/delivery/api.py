"""
FastAPI web API for the digest frontend.

Endpoints (all prefixed /api):
  GET    /api/digest/latest              — most recent digest
  GET    /api/digest/{date}              — digest for a specific YYYY-MM-DD
  GET    /api/digests                    — list of all digests (for archive page)
  GET    /api/articles                   — filtered article list (?source=X&topic=Y&since=DATE)
  GET    /api/articles/{id}              — full article with content, summary, extended_summary
  GET    /api/articles/{id}/content      — raw stored content (transcript / newsletter body)
  POST   /api/articles/{id}/extend       — generate or return cached extended AI analysis
  POST   /api/summarise-page             — synthesise visible article summaries into a structured briefing
  GET    /api/sources                    — all configured sources with status
  POST   /api/sources/discover           — validate a URL or search for a podcast by name
  POST   /api/sources                    — create a new source
  PATCH  /api/sources/{id}               — update an existing source
  DELETE /api/sources/{id}               — remove a source
  POST   /api/whatsapp/webhook           — Twilio incoming message webhook (on-demand delivery)

Non-API:
  GET    /assets/*                   — compiled frontend static assets (production only)
  GET    /*                          — SPA catch-all: serves index.html for React Router

All database access goes through src/storage/db.py.
CORS is enabled for origins listed in settings.cors_origins.
"""

import json
import logging
import re
from datetime import datetime
from typing import Any, Optional

import feedparser
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path

from src.config.settings import get_settings
from src.storage import db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Daily Digest API",
    description="Personal content digest pipeline API",
    version="1.0.0",
)

# Run DB migrations on startup so the server works when launched directly
# via uvicorn (without going through main.py which also calls init_db).
db.init_db()

_settings = get_settings()
_origins = [o.strip() for o in _settings.cors_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# All API routes are grouped under /api so the SPA catch-all can serve every
# other path without conflicting with backend endpoints.
router = APIRouter(prefix="/api")

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class DiscoverRequest(BaseModel):
    type: str  # "substack" or "podcast"
    query: str  # URL for substack, search term for podcast


class CreateSourceRequest(BaseModel):
    name: str
    type: str
    url: str
    default_topics: Optional[str] = None
    description: Optional[str] = None
    taddy_uuid: Optional[str] = None
    spotify_url: Optional[str] = None
    transcript_priority: Optional[str] = None   # always | on_demand
    content_type: Optional[str] = None           # news | informative


class ValidateUrlRequest(BaseModel):
    url: str


class UpdateSourceRequest(BaseModel):
    name: Optional[str] = None
    default_topics: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None
    taddy_uuid: Optional[str] = None
    spotify_url: Optional[str] = None
    transcript_priority: Optional[str] = None
    content_type: Optional[str] = None   # news | informative → stored as content_category


class PageSummaryRequest(BaseModel):
    article_ids: list[int]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: Any) -> dict[str, Any]:
    """Convert a sqlite3.Row to a plain dict for JSON serialisation."""
    return dict(row)


# Minimum word count for "Copy transcript" and "Deep dive" features to be
# available.  500 words reliably excludes short podcast description stubs
# (typically < 200 words) while capturing all real transcripts (1 000+).
_CONTENT_MIN_WORDS = 500


def _article_dict(row: Any, include_content: bool = False) -> dict[str, Any]:
    """
    Convert an article row to a JSON-serialisable dict.

    Computes derived boolean fields so the frontend knows which buttons to show:
      has_content    — content word count exceeds _CONTENT_MIN_WORDS
                       (true for real transcripts; false for description stubs)
      is_transcribed — same threshold; alias used by the Transcribe button logic
    Also preserves audio_url and transcript_priority from the source join so
    the frontend can decide whether to offer the on-demand Transcribe button.

    By default full transcripts are stripped from list payloads to keep
    responses lightweight.  Short podcast description stubs
    (≤ _CONTENT_MIN_WORDS words) are kept so ArticleCard can render them
    under a "SHOW DESCRIPTION" label without a separate API call.
    Pass include_content=True for single-article detail views where the
    full content is always needed.
    """
    d = dict(row)
    content = d.get("content") or ""
    word_count = len(content.split())
    d["has_content"] = word_count > _CONTENT_MIN_WORDS
    d["is_transcribed"] = word_count > _CONTENT_MIN_WORDS
    if not include_content:
        # Strip full transcripts (large) but keep short podcast description stubs
        if word_count > _CONTENT_MIN_WORDS or d.get("source_type") != "podcast":
            d.pop("content", None)
    return d


def _parse_date(date_str: str, param_name: str) -> datetime:
    """Parse a YYYY-MM-DD string or raise a 422 HTTPException."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"'{param_name}' must be in YYYY-MM-DD format",
        )


_CORE_TOPICS = ["Tech", "AI", "Markets", "Macro / Economics", "Startups / VC"]


def _suggest_topics_via_gemini(name: str, description: str) -> list[str]:
    """
    Ask Gemini to pick relevant topic tags from the core taxonomy for a source.

    Returns a list of strings, e.g. ["Tech", "AI"].
    Falls back to an empty list if the Gemini call fails (user can pick manually).
    """
    import os
    import re

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        logger.warning("GEMINI_API_KEY not set — skipping topic suggestion")
        return []

    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        prompt = (
            f"You are categorising a content source for a personal news digest.\n\n"
            f"Source name: {name}\n"
            f"Description: {description or '(none provided)'}\n\n"
            f"Available topic tags: {', '.join(_CORE_TOPICS)}\n\n"
            f"Return a JSON array of the most relevant tags from the list above "
            f"(1–3 tags). Only use tags from the list. Example: [\"Tech\", \"AI\"]"
        )
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",  # 15 RPM, 500 RPD free tier
            contents=prompt,
        )
        text = response.text.strip()
        # Strip optional ```json ... ``` wrapper
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
        return json.loads(text)
    except Exception as exc:
        logger.warning("Gemini topic suggestion failed for %r: %s", name, exc)
        return []


def _discover_substack(url: str) -> list[dict[str, Any]]:
    """
    Validate a Substack (or any RSS) URL via feedparser.

    Returns a single-item list on success, empty list on failure.
    """
    url = url.strip()
    # Normalise: ensure it ends with /feed for Substack URLs
    if "substack.com" in url and not url.endswith("/feed"):
        url = url.rstrip("/") + "/feed"

    feed = feedparser.parse(url)
    if feed.bozo and not feed.entries:
        logger.warning("Discover substack: feed parse failed for %s", url)
        return []

    title = feed.feed.get("title", "").strip() or url
    description = feed.feed.get("description", "").strip() or feed.feed.get("subtitle", "").strip()
    suggested_topics = _suggest_topics_via_gemini(title, description)

    return [{
        "name": title,
        "type": "substack",
        "url": url,
        "description": description,
        "suggested_topics": suggested_topics,
        "taddy_uuid": None,
    }]


def _discover_podcast(query: str) -> list[dict[str, Any]]:
    """
    Search for a podcast by name using Taddy first, falling back to iTunes.

    Returns up to 5 candidate dicts, each with name/url/description/
    suggested_topics/taddy_uuid.
    """
    candidates: list[dict[str, Any]] = []

    # --- Taddy search ---
    try:
        from src.ingestors import taddy
        results = taddy.search_podcast(query)
        for r in results[:5]:
            suggested_topics = _suggest_topics_via_gemini(
                r.get("name", ""), r.get("description", "")
            )
            candidates.append({
                "name": r.get("name", ""),
                "type": "podcast",
                "url": r.get("rss_url", ""),
                "description": r.get("description", ""),
                "suggested_topics": suggested_topics,
                "taddy_uuid": r.get("taddy_uuid"),
            })
    except Exception as exc:
        logger.warning("Discover podcast — Taddy search failed: %s", exc)

    if candidates:
        return candidates

    # --- iTunes fallback ---
    try:
        import requests
        resp = requests.get(
            "https://itunes.apple.com/search",
            params={"term": query, "media": "podcast", "limit": 5},
            timeout=10,
            headers={"User-Agent": "my-daily-digest/1.0"},
        )
        resp.raise_for_status()
        for item in resp.json().get("results", []):
            name = item.get("collectionName", "").strip()
            rss_url = item.get("feedUrl", "").strip()
            description = item.get("artistName", "").strip()
            if not name or not rss_url:
                continue
            suggested_topics = _suggest_topics_via_gemini(name, description)
            candidates.append({
                "name": name,
                "type": "podcast",
                "url": rss_url,
                "description": description,
                "suggested_topics": suggested_topics,
                "taddy_uuid": None,
            })
    except Exception as exc:
        logger.warning("Discover podcast — iTunes search failed: %s", exc)

    return candidates


# ---------------------------------------------------------------------------
# Digest endpoints
# ---------------------------------------------------------------------------

@router.get("/digest/latest")
def get_latest_digest(
    category: Optional[str] = Query(default=None, description="Filter by digest category: news, informative, all"),
) -> dict[str, Any]:
    """Return the most recently generated digest, optionally filtered by category."""
    row = db.get_latest_digest(category=category)
    if row is None:
        raise HTTPException(status_code=404, detail="No digest found. Run the pipeline first.")
    return _row_to_dict(row)


@router.get("/digest/{date}")
def get_digest_by_date(
    date: str,
    category: Optional[str] = Query(default=None, description="Filter by digest category"),
) -> dict[str, Any]:
    """Return the digest for a specific calendar date (YYYY-MM-DD), optionally filtered by category."""
    _parse_date(date, "date")
    row = db.get_digest_by_date(date, category=category)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No digest found for {date}")
    return _row_to_dict(row)


@router.get("/digests")
def list_digests(
    category: Optional[str] = Query(default=None, description="Filter by digest category: news, informative, all"),
) -> list[dict[str, Any]]:
    """Return all digest records in reverse-chronological order (for the archive page)."""
    return [_row_to_dict(r) for r in db.get_digests_list(category=category)]


# ---------------------------------------------------------------------------
# Article endpoints
# ---------------------------------------------------------------------------

@router.get("/articles")
def list_articles(
    source: Optional[str] = Query(default=None, description="Filter by source name"),
    type: Optional[str] = Query(default=None, description="Filter by source type: podcast or substack"),
    topic: Optional[str] = Query(default=None, description="Filter by topic tag (exact match)"),
    since: Optional[str] = Query(default=None, description="Lower bound on published_at (YYYY-MM-DD)"),
    until: Optional[str] = Query(default=None, description="Upper bound on published_at (YYYY-MM-DD)"),
    summarised_since: Optional[str] = Query(default=None, description="Lower bound on summarised_at (YYYY-MM-DD or ISO datetime)"),
    summarised_until: Optional[str] = Query(default=None, description="Upper bound on summarised_at (YYYY-MM-DD or ISO datetime)"),
    summarised_only: bool = Query(default=False, description="If true, return only articles that have been summarised"),
    category: Optional[str] = Query(default=None, description="Filter by source content_category: news or informative"),
    transcribed: Optional[bool] = Query(default=None, description="If true, return only fully transcribed articles; false = not transcribed"),
    limit: Optional[int] = Query(default=None, ge=1, le=500, description="Maximum articles to return"),
    q: Optional[str] = Query(default=None, description="Keyword search across title, summary, and source name"),
) -> list[dict[str, Any]]:
    """
    Return articles matching optional filters. Filters are ANDed together.

    published_at filtering: since / until (YYYY-MM-DD).
    summarised_at filtering: summarised_since / summarised_until (YYYY-MM-DD or ISO datetime).
    topic matching is case-sensitive and exact (e.g. 'AI', not 'ai').
    """
    since_dt: Optional[datetime] = None
    if since is not None:
        since_dt = _parse_date(since, "since")

    until_dt: Optional[datetime] = None
    if until is not None:
        until_dt = _parse_date(until, "until")

    sum_since_dt: Optional[datetime] = None
    if summarised_since is not None:
        try:
            sum_since_dt = datetime.fromisoformat(summarised_since)
        except ValueError:
            sum_since_dt = _parse_date(summarised_since, "summarised_since")

    sum_until_dt: Optional[datetime] = None
    if summarised_until is not None:
        try:
            sum_until_dt = datetime.fromisoformat(summarised_until)
        except ValueError:
            sum_until_dt = _parse_date(summarised_until, "summarised_until")

    search_q = q.strip() if q and q.strip() else None

    rows = db.get_articles_filtered(
        source_name=source,
        source_type=type,
        topic=topic,
        since=since_dt,
        until=until_dt,
        summarised_since=sum_since_dt,
        summarised_until=sum_until_dt,
        summarised_only=summarised_only,
        category=category,
        transcribed=transcribed,
        limit=limit,
        q=search_q,
    )
    return [_article_dict(r) for r in rows]


@router.get("/digest/{date}/articles")
def get_digest_articles(
    date: str,
    category: Optional[str] = Query(default=None, description="Filter by content category: news or informative"),
) -> list[dict[str, Any]]:
    """
    Return all summarised articles that fall within a digest's 25-hour lookback window.

    Looks up the digest by date (YYYY-MM-DD), then returns articles with
    summarised_at in [generated_at − 25 h, generated_at].  This is the
    canonical set of articles that belong to that digest, regardless of when
    they were actually ingested or how many days old they are.

    Optionally filtered by content category (news or informative).
    Returns 404 if no digest exists for the given date.
    """
    _parse_date(date, "date")
    digest = db.get_digest_by_date(date)
    if digest is None:
        raise HTTPException(status_code=404, detail=f"No digest found for {date}")

    from datetime import timedelta
    generated_at = datetime.fromisoformat(digest["generated_at"])
    summarised_since = generated_at - timedelta(hours=25)

    rows = db.get_articles_filtered(
        summarised_since=summarised_since,
        summarised_until=generated_at,
        summarised_only=True,
        category=category,
    )
    return [_article_dict(r) for r in rows]


@router.get("/articles/{article_id}")
def get_article(article_id: int) -> dict[str, Any]:
    """Return a single article including full content, summary, and extended_summary."""
    row = db.get_article_by_id(article_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Article {article_id} not found")
    return _article_dict(row, include_content=True)


@router.get("/articles/{article_id}/content")
def get_article_content(article_id: int) -> dict[str, Any]:
    """
    Return the full stored content for an article (transcript or newsletter body).

    Used by the frontend "Copy transcript" button so list responses can remain
    lightweight.  Returns 404 if the article has no stored content.
    """
    row = db.get_article_by_id(article_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Article {article_id} not found")
    content = dict(row).get("content") or ""
    if not content:
        raise HTTPException(status_code=404, detail="No content stored for this article")
    return {"content": content}


@router.post("/articles/{article_id}/extend")
def extend_article(article_id: int) -> dict[str, Any]:
    """
    Generate a detailed 300-500 word AI analysis for a single article.

    Returns the cached result immediately if already generated.
    Returns 400 if the article content is too short to analyse meaningfully
    (fewer than 200 words — likely a description stub).
    Returns 503 on a transient Gemini API failure.
    """
    row = db.get_article_by_id(article_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Article {article_id} not found")

    article = dict(row)

    # Return cached result without an API call
    if article.get("extended_summary"):
        return {"extended_summary": article["extended_summary"]}

    content = article.get("content") or ""
    if len(content.split()) <= _CONTENT_MIN_WORDS:
        raise HTTPException(
            status_code=400,
            detail="No detailed content available for this article",
        )

    # Lazy import to avoid loading Gemini SDK unless this endpoint is actually called
    from src.summariser.summariser import _call_gemini, _truncate_content

    truncated = _truncate_content(
        content=content,
        source_type=article.get("source_type") or "unknown",
        article_id=article_id,
    )

    source_label = (
        "podcast transcript" if article.get("source_type") == "podcast" else "newsletter"
    )
    content_category = article.get("content_category") or "news"
    if content_category == "news":
        prompt = (
            f"You are analysing a {source_label} for a finance and tech professional. "
            f"Provide a detailed 300-500 word analysis covering:\n"
            f"1. Key arguments and evidence presented\n"
            f"2. Notable data points or statistics\n"
            f"3. Implications for investors or the industry\n"
            f"4. Any contrarian or surprising viewpoints\n\n"
            f"Title: {article.get('title', '')}\n\n"
            f"Content:\n{truncated}"
        )
    else:
        prompt = (
            f"You are analysing a {source_label} for an intellectually curious reader "
            f"who follows long-form podcasts and newsletters on tech, AI, markets, "
            f"startups, and economics.\n\n"
            f"Provide a detailed 400-600 word analysis written in flowing prose "
            f"(no bullet points or numbered lists). Cover:\n"
            f"1. The central thesis or argument — what is the guest/author's core claim "
            f"or insight?\n"
            f"2. Key evidence, examples, or data points that support or challenge this "
            f"thesis\n"
            f"3. The most surprising, contrarian, or counterintuitive ideas presented\n"
            f"4. Implications — what does this mean for practitioners, investors, or the "
            f"industry?\n"
            f"5. A brief critical assessment — what assumptions are made, what is left "
            f"unaddressed?\n\n"
            f"Write in the style of a thoughtful analyst's note. Use short paragraphs. "
            f"Bold key names, concepts, and data points on first mention. "
            f"UK English throughout.\n\n"
            f"Title: {article.get('title', '')}\n\n"
            f"Content:\n{truncated}"
        )

    try:
        raw = _call_gemini(prompt)
    except Exception as exc:
        logger.error("Extend article id=%d failed: %s", article_id, exc)
        raise HTTPException(
            status_code=503,
            detail="AI analysis temporarily unavailable — try again shortly",
        )

    # Strip any markdown code fences the model may wrap around plain prose
    extended = re.sub(r"^```\w*\s*", "", raw.strip(), flags=re.MULTILINE)
    extended = re.sub(r"\s*```$", "", extended, flags=re.MULTILINE).strip()

    db.save_extended_summary(article_id, extended)
    return {"extended_summary": extended}


@router.get("/feed")
def get_feed(
    since: Optional[str] = Query(default=None, description="Return articles published after this ISO datetime or YYYY-MM-DD date"),
    limit: int = Query(default=50, ge=1, le=200, description="Maximum number of articles to return"),
) -> list[dict[str, Any]]:
    """
    Rolling feed of informative-category articles (both summarised and pending),
    ordered newest-first. Powers the Feed tab in the frontend.

    since  — optional ISO datetime or YYYY-MM-DD lower bound on published_at
    limit  — capped at 200, defaults to 50
    """
    since_dt: Optional[datetime] = None
    if since is not None:
        # Accept either full ISO datetime or YYYY-MM-DD
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            since_dt = _parse_date(since, "since")

    rows = db.get_articles_filtered(
        since=since_dt,
        category="informative",
        limit=limit,
    )
    return [_article_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Page summary endpoint
# ---------------------------------------------------------------------------

_PAGE_SUMMARY_MIN_ARTICLES = 2
_PAGE_SUMMARY_MAX_WORDS = 5000


@router.post("/summarise-page")
def summarise_page(body: PageSummaryRequest) -> dict[str, Any]:
    """
    Synthesise summaries of a set of articles into a structured briefing.

    Accepts a list of article IDs (those currently visible on the page), fetches
    their summaries, and asks Gemini to distill them into key themes, notable
    items, and a market mood assessment.

    Returns 400 if fewer than 2 articles have summaries.
    Returns 503 on a transient Gemini failure.
    """
    from datetime import timezone

    rows = db.get_articles_by_ids(body.article_ids)

    # Keep only articles that have been summarised; rows are already newest-first
    summarised = [dict(r) for r in rows if r["summary"]]

    if len(summarised) < _PAGE_SUMMARY_MIN_ARTICLES:
        raise HTTPException(
            status_code=400,
            detail="Not enough summarised articles to generate a page summary",
        )

    # Build the article block, truncating from the oldest end if needed
    lines: list[str] = []
    total_words = 0
    for article in summarised:
        line = f"- [{article['source_name']}] {article['title']}: {article['summary']}"
        word_count = len(line.split())
        if total_words + word_count > _PAGE_SUMMARY_MAX_WORDS and lines:
            break
        lines.append(line)
        total_words += word_count

    article_block = "\n".join(lines)

    prompt = (
        "You are a financial and technology analyst producing a daily briefing for a "
        "professional investor.\n\n"
        "Below are AI-generated summaries of articles and podcast episodes published recently. "
        "Synthesise them into a structured briefing.\n\n"
        f"ARTICLES:\n{article_block}\n\n"
        "Respond in JSON only, no markdown, no preamble:\n"
        "{\n"
        '  "key_themes": [\n'
        '    "2-3 sentence description of each major theme that spans multiple articles"\n'
        "  ],\n"
        '  "notable_items": [\n'
        '    "1-2 sentence description of individual items that are noteworthy but don\'t fit a broader theme"\n'
        "  ],\n"
        '  "market_mood": "1-2 sentence overall assessment of market sentiment based on the coverage"\n'
        "}\n\n"
        "Rules:\n"
        "- key_themes should have 3-5 entries, each synthesising across multiple sources\n"
        "- notable_items should have 2-4 entries for standalone noteworthy items\n"
        "- market_mood should be concise and directional (bullish/bearish/mixed + reasoning)\n"
        "- Write in UK English\n"
        "- Do not invent information not present in the summaries"
    )

    # Lazy import — avoids loading Gemini SDK at module load time
    from src.summariser.summariser import _call_gemini

    try:
        raw = _call_gemini(prompt)
    except Exception as exc:
        logger.error("Page summary Gemini call failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Summary generation temporarily unavailable — try again shortly",
        )

    # Strip optional ``` fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE).strip()

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error("Page summary: Gemini returned non-JSON: %s — raw: %.200s", exc, raw)
        raise HTTPException(
            status_code=503,
            detail="Summary generation returned an unexpected response — try again shortly",
        )

    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    return result


# ---------------------------------------------------------------------------
# Groq budget endpoint
# ---------------------------------------------------------------------------

@router.get("/groq-budget")
def get_groq_budget() -> dict[str, Any]:
    """
    Return current Groq audio-transcription usage and remaining budget.

    Values are expressed in minutes (rounded to one decimal place) for
    display in the GroqBudget frontend component.  Limits are based on
    the Groq free tier: 120 min/hour, 480 min/day.
    """
    usage = db.get_groq_usage()
    return {
        "used_minutes_hour": round(usage["used_seconds_hour"] / 60, 1),
        "used_minutes_day": round(usage["used_seconds_day"] / 60, 1),
        "remaining_minutes_hour": round(usage["remaining_seconds_hour"] / 60, 1),
        "remaining_minutes_day": round(usage["remaining_seconds_day"] / 60, 1),
        "limit_minutes_hour": round(usage["limit_seconds_hour"] / 60, 1),
        "limit_minutes_day": round(usage["limit_seconds_day"] / 60, 1),
    }


@router.get("/feed-summary")
def get_feed_summary(
    date: Optional[str] = Query(
        default=None,
        description="Date in YYYY-MM-DD format (defaults to today UTC)",
    ),
) -> dict[str, Any]:
    """
    Return the pre-computed structured feed summary for the given date.

    Generated automatically at the end of each pipeline run.
    Returns 404 if no summary has been stored for that date yet — the frontend
    should fall back to offering the SUMMARISE FEED button.
    """
    from datetime import timezone
    target_date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = db.get_feed_summary(target_date)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No feed summary available for {target_date}",
        )
    result = json.loads(row["summary_json"])
    result["date"] = target_date
    result["generated_at"] = row["generated_at"]
    return result


@router.post("/feed-summary/refresh")
def refresh_feed_summary() -> dict[str, Any]:
    """
    Regenerate and store the feed summary for today using recent article summaries.

    Called when the user clicks REFRESH on a pre-computed summary.
    Uses the same Gemini call as the pipeline's auto-generation.
    Returns 400 if there are not enough summarised articles.
    Returns 503 on a transient Gemini failure.
    """
    from datetime import timezone
    from src.summariser.summariser import generate_and_save_feed_summary

    try:
        ok = generate_and_save_feed_summary()
    except Exception as exc:
        logger.error("Feed summary refresh failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Feed summary generation temporarily unavailable — try again shortly",
        )

    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Not enough summarised articles to generate a feed summary",
        )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = db.get_feed_summary(today)
    if row is None:
        raise HTTPException(status_code=503, detail="Summary was generated but could not be retrieved")

    result = json.loads(row["summary_json"])
    result["date"] = today
    result["generated_at"] = row["generated_at"]
    return result


@router.get("/gemini-budget")
def get_gemini_budget() -> dict[str, Any]:
    """
    Return current Gemini API call usage and remaining daily budget.

    Based on gemini-3.1-flash-lite-preview free-tier RPD cap (500/day).
    The day window resets at midnight UTC.
    """
    return db.get_gemini_usage()


# ---------------------------------------------------------------------------
# Transcription activity log
# ---------------------------------------------------------------------------

@router.get("/transcription-log")
def get_transcription_log(
    hours: int = Query(default=24, ge=1, le=168, description="Look-back window in hours (max 7 days)"),
) -> list[dict[str, Any]]:
    """
    Return recent Groq transcription events from the past N hours.

    Each entry includes article title, source name, audio duration, provider,
    timestamp, and whether a summary has been generated.  Ordered newest first.
    """
    rows = db.get_recent_transcriptions(hours=hours)
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# On-demand transcription endpoint
# ---------------------------------------------------------------------------

@router.post("/articles/{article_id}/transcribe")
def transcribe_article_on_demand(article_id: int) -> dict[str, Any]:
    """
    Trigger Groq transcription for a single on-demand article.

    Only available for podcast articles where transcript_priority is not 'none'
    and an audio_url is stored.  Returns 400 if already transcribed, 400 if
    the source does not support transcription, 429 on rate-limit, 503 on
    other failures.

    On success, runs the summariser so a summary is available immediately,
    then returns the updated article dict plus audio_seconds and word_count.
    """
    row = db.get_article_by_id(article_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Article {article_id} not found")

    article = dict(row)
    priority = article.get("transcript_priority") or "always"

    if priority == "skip":
        raise HTTPException(
            status_code=400,
            detail="This source is not configured for transcription",
        )

    if not article.get("audio_url"):
        raise HTTPException(
            status_code=400,
            detail="No audio URL stored for this article",
        )

    content = article.get("content") or ""
    if len(content.split()) > _CONTENT_MIN_WORDS:
        raise HTTPException(
            status_code=400,
            detail="Article already has a full transcript",
        )

    source_name: str = article.get("source_name") or ""
    title: str = article.get("title") or ""
    audio_url: str = article.get("audio_url") or ""

    # --- Try website scraper first (saves Groq budget) ---
    from src.ingestors.scrapers.runner import get_scraper_for_source
    scraper = get_scraper_for_source(source_name)

    if scraper is not None:
        try:
            transcript: Optional[str] = scraper.scrape(  # type: ignore[attr-defined]
                episode_title=title,
                audio_url=audio_url,
            )
        except Exception as exc:
            logger.warning(
                "[%s] On-demand scraper raised for article id=%d: %s — falling back to Groq",
                source_name, article_id, exc,
            )
            transcript = None

        if transcript and len(transcript.split()) >= _CONTENT_MIN_WORDS:
            word_count = len(transcript.split())
            logger.info(
                "[%s] On-demand transcript scraped from website: article id=%d, %d words",
                source_name, article_id, word_count,
            )
            try:
                db.save_transcription(article_id, transcript)
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=f"Failed to save transcript — {exc}")

            try:
                from src.summariser.summariser import summarise_new_articles
                summarise_new_articles()
            except Exception as exc:
                logger.warning("Auto-summarisation after scrape failed: %s", exc)

            updated_row = db.get_article_by_id(article_id)
            response = _article_dict(updated_row, include_content=False) if updated_row else {}
            response["audio_seconds"] = None
            response["word_count"] = word_count
            return response

        if transcript is not None:
            logger.warning(
                "[%s] On-demand scraper returned too-short transcript (%d words) "
                "for article id=%d — falling back to Groq",
                source_name, len(transcript.split()), article_id,
            )

    # --- Fall back to Groq Whisper ---
    from src.ingestors.whisper_transcriber import transcribe_article, RateLimitError

    try:
        result = transcribe_article(article_id)
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except RuntimeError as exc:
        logger.error("On-demand transcription failed for article id=%d: %s", article_id, exc)
        raise HTTPException(
            status_code=503,
            detail=f"Transcription failed — {exc}",
        )

    logger.info(
        "[%s] On-demand transcript via Groq Whisper: article id=%d, %d words",
        source_name, article_id, result.get("word_count", 0),
    )

    # Trigger summarisation so the article summary is available immediately
    try:
        from src.summariser.summariser import summarise_new_articles
        summarise_new_articles()
    except Exception as exc:
        logger.warning("Auto-summarisation after transcription failed: %s", exc)

    updated_row = db.get_article_by_id(article_id)
    response = _article_dict(updated_row, include_content=False) if updated_row else {}
    response["audio_seconds"] = result.get("audio_seconds")
    response["word_count"] = result.get("word_count")
    return response


# ---------------------------------------------------------------------------
# Sources endpoints
# ---------------------------------------------------------------------------

@router.get("/sources")
def list_sources() -> list[dict[str, Any]]:
    """Return all configured sources with last ingestion time and article count."""
    return [_row_to_dict(r) for r in db.get_all_sources()]


@router.get("/sources/search")
def search_sources(
    q: str = Query(..., min_length=2, description="Podcast search term (min 2 characters)"),
) -> list[dict[str, Any]]:
    """
    Search Taddy for podcast series matching the query term.

    Returns up to 10 candidates with name, description, image_url, rss_url,
    author_name, and taddy_uuid.  Requires TADDY_USER_ID and TADDY_API_KEY
    to be configured.  Returns 503 if Taddy is unreachable.
    """
    import requests as _requests

    user_id = _settings.taddy_user_id or ""
    api_key = _settings.taddy_api_key or ""
    if not user_id or not api_key:
        raise HTTPException(
            status_code=503,
            detail="Taddy credentials not configured (TADDY_USER_ID / TADDY_API_KEY)",
        )

    # Use inline string interpolation — no GraphQL variables — matching the
    # pattern in src/ingestors/taddy.py's _graphql() helper exactly.
    safe_term = q.replace('"', '\\"')
    gql = f"""{{
      search(term: "{safe_term}", filterForTypes: PODCASTSERIES) {{
        searchId
        podcastSeries {{
          uuid
          name
          description(shouldStripHtmlTags: true)
          imageUrl
          rssUrl
        }}
      }}
    }}"""

    try:
        resp = _requests.post(
            "https://api.taddy.org",
            json={"query": gql},
            headers={
                "X-USER-ID": user_id,
                "X-API-KEY": api_key,
                "Content-Type": "application/json",
                "User-Agent": "my-daily-digest/1.0",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error(
            "Taddy search failed for %r: %s %s", q, type(exc).__name__, exc
        )
        raise HTTPException(
            status_code=503,
            detail="Podcast search temporarily unavailable — try the Manual URL tab",
        )

    if data.get("errors"):
        msgs = "; ".join(e.get("message", str(e)) for e in data["errors"])
        logger.error("Taddy search GraphQL errors for %r: %s", q, msgs)
        raise HTTPException(
            status_code=503,
            detail="Podcast search temporarily unavailable — try the Manual URL tab",
        )

    series_list = (data.get("data") or {}).get("search", {}).get("podcastSeries") or []
    results: list[dict[str, Any]] = []

    for s in series_list:
        results.append({
            "name": s.get("name") or "",
            "description": s.get("description") or "",
            "image_url": s.get("imageUrl") or "",
            "rss_url": s.get("rssUrl") or "",
            "author_name": "",
            "taddy_uuid": s.get("uuid") or "",
        })

    return results


@router.post("/sources/validate-url")
def validate_source_url(body: ValidateUrlRequest) -> dict[str, Any]:
    """
    Validate a feed URL and return parsed metadata.

    Appends /feed for Substack URLs.  Auto-detects source type: podcast if the
    feed contains audio enclosures, newsletter otherwise.  Returns a clear error
    message on failure (timeout, invalid XML, not a feed).
    """
    url = body.url.strip()
    if not url:
        raise HTTPException(status_code=422, detail="URL must not be empty")

    # Normalise Substack URLs
    if "substack.com" in url and not url.rstrip("/").endswith("/feed"):
        url = url.rstrip("/") + "/feed"

    try:
        feed = feedparser.parse(url)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to fetch feed: {exc}")

    if feed.bozo and not feed.entries:
        raise HTTPException(
            status_code=422,
            detail="URL does not appear to be a valid RSS/Atom feed",
        )

    title = (feed.feed.get("title") or "").strip() or url
    description = (
        feed.feed.get("description") or feed.feed.get("subtitle") or ""
    ).strip()

    # Detect podcast: any entry has an audio enclosure
    source_type = "newsletter"
    for entry in feed.entries[:5]:
        for enc in entry.get("enclosures", []):
            if (enc.get("type") or "").startswith("audio/"):
                source_type = "podcast"
                break
        if source_type == "podcast":
            break

    return {
        "name": title,
        "description": description,
        "source_type": source_type,
        "url": url,
    }


@router.post("/sources/discover")
def discover_source(body: DiscoverRequest) -> list[dict[str, Any]]:
    """
    Validate a Substack RSS URL or search for a podcast by name.

    For type='substack': body.query should be the feed URL.  Returns a
    single-item list with the parsed feed title and Claude-suggested topics.

    For type='podcast': body.query is a search string.  Tries Taddy first,
    falls back to the iTunes Search API.  Returns up to 5 candidates with
    name, url, description, suggested_topics, taddy_uuid.

    Returns an empty list if nothing is found.
    """
    source_type = body.type.strip().lower()
    if source_type not in ("substack", "podcast"):
        raise HTTPException(status_code=422, detail="type must be 'substack' or 'podcast'")

    if not body.query.strip():
        raise HTTPException(status_code=422, detail="query must not be empty")

    if source_type == "substack":
        return _discover_substack(body.query)
    return _discover_podcast(body.query)


@router.post("/sources", status_code=201)
def create_source(body: CreateSourceRequest) -> dict[str, Any]:
    """
    Add a new source to the database.

    Returns 409 if a source with the same URL already exists (normalised by
    stripping trailing slashes).  Validates content_type and transcript_priority
    when provided.  Returns the newly created source record plus scraper_available
    (whether a dedicated website scraper exists for this source).
    """
    _ALLOWED_CONTENT_TYPES = {"news", "informative"}
    _ALLOWED_PRIORITIES = {"always", "on_demand"}

    source_type = body.type.strip().lower()
    if source_type not in ("podcast", "newsletter", "substack"):
        raise HTTPException(status_code=422, detail="type must be 'podcast', 'newsletter', or 'substack'")

    if body.content_type is not None and body.content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"content_type must be one of: {', '.join(sorted(_ALLOWED_CONTENT_TYPES))}",
        )

    if body.transcript_priority is not None and body.transcript_priority not in _ALLOWED_PRIORITIES:
        raise HTTPException(
            status_code=422,
            detail=f"transcript_priority must be one of: {', '.join(sorted(_ALLOWED_PRIORITIES))}",
        )

    # Normalise URL for duplicate check
    normalised_url = body.url.strip().rstrip("/")
    existing = db.search_source_by_url(normalised_url)
    if existing is None:
        existing = db.search_source_by_url(normalised_url + "/")
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A source with this feed URL already exists (id={existing['id']})",
        )

    source_id = db.create_source(
        name=body.name.strip(),
        source_type=source_type,
        url=normalised_url,
        default_topics=body.default_topics,
        description=body.description,
        taddy_uuid=body.taddy_uuid,
        spotify_url=body.spotify_url,
        transcript_priority=body.transcript_priority,
        content_category=body.content_type,
    )

    row = db.get_source_by_id(source_id)
    if row is None:
        raise HTTPException(status_code=500, detail="Source created but could not be retrieved")

    result = _row_to_dict(row)

    # Include scraper availability so the frontend can display status
    from src.ingestors.scrapers.runner import get_scraper_for_source
    scraper = get_scraper_for_source(body.name.strip())
    result["scraper_available"] = scraper is not None

    return result


@router.patch("/sources/{source_id}")
def update_source(source_id: int, body: UpdateSourceRequest) -> dict[str, Any]:
    """
    Update one or more fields on an existing source.

    Only the fields present in the request body are modified.
    Returns the full updated source record on success.
    """
    existing = db.get_source_by_id(source_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id} not found")

    fields: dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name.strip()
    if body.default_topics is not None:
        fields["default_topics"] = body.default_topics
    if body.description is not None:
        fields["description"] = body.description
    if body.active is not None:
        fields["active"] = 1 if body.active else 0
    if body.taddy_uuid is not None:
        fields["taddy_uuid"] = body.taddy_uuid
    if body.spotify_url is not None:
        fields["spotify_url"] = body.spotify_url
    if body.transcript_priority is not None:
        allowed_priorities = {"always", "on_demand", "skip"}
        if body.transcript_priority not in allowed_priorities:
            raise HTTPException(
                status_code=422,
                detail=f"transcript_priority must be one of: {', '.join(sorted(allowed_priorities))}",
            )
        fields["transcript_priority"] = body.transcript_priority
    if body.content_type is not None:
        allowed_content_types = {"news", "informative"}
        if body.content_type not in allowed_content_types:
            raise HTTPException(
                status_code=422,
                detail=f"content_type must be one of: {', '.join(sorted(allowed_content_types))}",
            )
        fields["content_category"] = body.content_type

    if not fields:
        # Nothing to update — return the existing record unchanged
        return _row_to_dict(existing)

    db.update_source(source_id, **fields)

    updated = db.get_source_by_id(source_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="Source updated but could not be retrieved")
    return _row_to_dict(updated)


@router.delete("/sources/{source_id}", status_code=204)
def delete_source(source_id: int) -> Response:
    """
    Permanently delete a source and all its associated articles.

    Returns 204 No Content on success, 404 if the source does not exist.
    """
    existing = db.get_source_by_id(source_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id} not found")

    db.delete_source(source_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# WhatsApp webhook
# ---------------------------------------------------------------------------

@router.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request) -> PlainTextResponse:
    """
    Receive incoming WhatsApp messages from Twilio and dispatch on-demand replies.

    Twilio POSTs form-encoded data.  The handler in whatsapp.py decides what
    to reply.  We always return an empty TwiML 200 response so Twilio does not
    retry the webhook.
    """
    form = await request.form()
    from_number = str(form.get("From", "")).replace("whatsapp:", "")
    body = str(form.get("Body", "")).strip()

    logger.info("Incoming WhatsApp from %s: %r", from_number, body)

    try:
        from src.delivery.whatsapp import handle_incoming_message
        handle_incoming_message(from_number=from_number, body=body)
    except Exception as exc:
        logger.error("WhatsApp handler error: %s", exc, exc_info=True)

    # Empty TwiML tells Twilio the webhook was handled successfully
    return PlainTextResponse(
        content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        media_type="application/xml",
    )


# ---------------------------------------------------------------------------
# Register all /api/* routes
# ---------------------------------------------------------------------------

app.include_router(router)

# ---------------------------------------------------------------------------
# Static frontend serving (production build)
# ---------------------------------------------------------------------------
# Only activated when frontend/dist/ exists (i.e. after `npm run build`).
# API routes registered above are matched first; this catch-all only handles
# paths that didn't match any /api/* route.

_FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

if _FRONTEND_DIR.is_dir():
    # Serve compiled JS/CSS/image assets from /assets/...
    app.mount(
        "/assets",
        StaticFiles(directory=_FRONTEND_DIR / "assets"),
        name="static-assets",
    )

    @app.get("/{path:path}")
    async def serve_spa(path: str) -> FileResponse:
        """
        SPA catch-all: serve the requested file from dist/ if it exists
        (e.g. favicon.ico, robots.txt), otherwise return index.html so
        React Router can handle client-side navigation.

        Paths starting with 'api/' must never reach this handler — they
        belong to the router registered above.  If one does (e.g. a typo
        in a route definition), return 404 JSON rather than index.html so
        the bug surfaces as a clear error instead of a silent HTML response.
        """
        if path.startswith("api/") or path == "api":
            raise HTTPException(status_code=404, detail=f"API route not found: /{path}")
        file_path = _FRONTEND_DIR / path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_FRONTEND_DIR / "index.html")
