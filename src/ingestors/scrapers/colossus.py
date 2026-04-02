"""
Colossus transcript scraper — shared logic for Invest Like the Best and
Business Breakdowns (both hosted on joincolossus.com).

last_verified: 2026-03-26

Strategy
--------
Full Colossus transcripts require authentication and are behind Cloudflare,
so this scraper uses the Megaphone RSS feeds as its source.  Each RSS entry
links to the episode page on colossus.com; the scraper fetches the transcript
tab from that page.

Episode RSS feeds:
    https://feeds.megaphone.fm/CLS2859450455  (Invest Like the Best)
    https://feeds.megaphone.fm/breakdowns     (Business Breakdowns)

This module:
  1. Parses the Megaphone RSS feed for the given show slug.
  2. Matches the requested title using string similarity.
  3. Fetches the episode transcript tab and extracts the text.

Called directly by invest_like_the_best.py and business_breakdowns.py which
pass in the appropriate show slug.
"""

import logging
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import requests
import requests_cache
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.joincolossus.com"
# Realistic browser UA to avoid Cloudflare 403 on episode pages
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
# Megaphone RSS feeds — verified 2026-03-26
_SERIES_RSS_URLS: dict[str, str] = {
    "invest-like-the-best": "https://feeds.megaphone.fm/CLS2859450455",
    "business-breakdowns": "https://feeds.megaphone.fm/breakdowns",
}
_CACHE_PATH = str(Path(__file__).parent.parent.parent.parent / "data" / ".requests_cache")
_MATCH_THRESHOLD = 0.60
_REQUEST_TIMEOUT = 20

# Shared cached session for RSS and episode page requests
_session = requests_cache.CachedSession(
    cache_name=_CACHE_PATH,
    expire_after=3600,
    backend="sqlite",
)
_session.headers.update({
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _clean_title(title: str) -> str:
    """Strip common suffixes like show name."""
    return re.sub(
        r"\s*[\|—]\s*(Invest Like the Best|Business Breakdowns).*$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()


def _fetch_episode_listing(series_slug: str) -> list[tuple[str, str]]:
    """
    Parse the Megaphone RSS feed and return (title, episode_page_url) pairs.

    Megaphone RSS entries for Colossus shows link to colossus.com episode
    pages, which carry the transcript tab in their HTML.
    """
    import feedparser

    logger.info("Colossus: using RSS fallback (authenticated scraping not configured)")

    rss_url = _SERIES_RSS_URLS.get(series_slug, "")
    if not rss_url:
        logger.warning("Colossus: no RSS URL configured for series_slug=%r", series_slug)
        return []

    try:
        resp = _session.get(rss_url, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.warning("Colossus RSS fetch failed (%s): %s", series_slug, exc)
        return []

    feed = feedparser.parse(resp.text)
    links: list[tuple[str, str]] = []
    for entry in feed.entries:
        title = getattr(entry, "title", "").strip()
        link = getattr(entry, "link", "").strip()
        # Megaphone RSS links point to colossus.com episode pages
        if title and link and "colossus.com" in link:
            links.append((title, link.split("?")[0]))

    logger.debug("Colossus RSS [%s]: %d episodes", series_slug, len(links))
    return links


def _fetch_transcript(episode_url: str) -> Optional[str]:
    """Fetch the transcript tab for a Colossus episode page."""
    transcript_url = f"{episode_url}?tab=transcript"

    try:
        resp = _session.get(transcript_url, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.warning("Colossus transcript fetch failed %s: %s", transcript_url, exc)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    for selector in [
        ("div", {"class": "episode-transcript"}),
        ("div", {"class": "transcript"}),
        ("div", {"id": "transcript"}),
        ("div", {"class": "prose"}),
    ]:
        el = soup.find(*selector)
        if el:
            text = el.get_text(separator="\n", strip=True)
            if len(text.split()) > 100:
                return text

    main = soup.find("main") or soup.find("article")
    if main:
        return main.get_text(separator="\n", strip=True)

    return None


# ---------------------------------------------------------------------------
# Public interface (called by show-specific thin wrappers)
# ---------------------------------------------------------------------------

def scrape(episode_title: str, audio_url: str = "", series_slug: str = "") -> Optional[str]:
    """
    Return the plain-text transcript for a Colossus episode, or None.

    Parameters
    ----------
    episode_title:
        Episode title as stored in the database.
    audio_url:
        Audio URL (not used; present for interface consistency).
    series_slug:
        Colossus show identifier, e.g. 'invest-like-the-best'.
        Must be provided by the calling wrapper module.
    """
    if not series_slug:
        logger.error("Colossus scraper called without a series_slug")
        return None

    clean_ep = _clean_title(episode_title)
    logger.info("Colossus scraper [%s]: searching for %r", series_slug, clean_ep)

    listing = _fetch_episode_listing(series_slug)
    if not listing:
        logger.warning("Colossus: no episode listing available for %s", series_slug)
        return None

    best_score = 0.0
    best_url = ""
    for title, url in listing:
        score = _similarity(clean_ep, _clean_title(title))
        if score > best_score:
            best_score = score
            best_url = url

    if best_score < _MATCH_THRESHOLD:
        logger.warning(
            "Colossus [%s]: no confident match for %r (best %.2f < %.2f)",
            series_slug, clean_ep, best_score, _MATCH_THRESHOLD,
        )
        return None

    if not best_url:
        logger.warning(
            "Colossus [%s]: matched %r but no episode URL available — cannot fetch transcript",
            series_slug, clean_ep,
        )
        return None

    logger.info("Colossus [%s]: matched %.0f%% → %s", series_slug, best_score * 100, best_url)
    text = _fetch_transcript(best_url)

    if text:
        logger.info("Colossus [%s]: extracted %d words", series_slug, len(text.split()))
    return text
