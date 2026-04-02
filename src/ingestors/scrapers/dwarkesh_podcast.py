"""
Dwarkesh Podcast transcript scraper.

last_verified: 2026-03-23

Strategy
--------
Dwarkesh Patel's podcast is published via Substack at dwarkesh.com.
Each episode is a Substack post at dwarkesh.com/p/<slug> and the transcript
is embedded in the article body.

This scraper:
  1. Fetches the Substack sitemap or episode listing to discover episode URLs.
  2. Matches the episode title using string similarity.
  3. Fetches the episode page and extracts the article body.

Substack article bodies live in <div class="body markup"> (authenticated)
or <div class="available-content"> (free reader view).
"""

import logging
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import requests
import requests_cache
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.dwarkesh.com"
_FEED_URL = "https://apple.dwarkesh-podcast.workers.dev/feed.rss"
_USER_AGENT = "my-daily-digest/1.0 (Dwarkesh scraper)"
_CACHE_PATH = str(Path(__file__).parent.parent.parent.parent / "data" / ".requests_cache")
_MATCH_THRESHOLD = 0.60
_REQUEST_TIMEOUT = 20

_session = requests_cache.CachedSession(
    cache_name=_CACHE_PATH,
    expire_after=3600,  # 1 hour
    backend="sqlite",
)
_session.headers.update({"User-Agent": _USER_AGENT})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _clean_title(title: str) -> str:
    """Strip common Dwarkesh episode suffixes."""
    # Remove "| Dwarkesh Podcast" suffix
    return re.sub(r"\s*[\|—]\s*Dwarkesh.*$", "", title, flags=re.IGNORECASE).strip()


def _fetch_episode_links_from_rss() -> list[tuple[str, str]]:
    """
    Parse the Dwarkesh RSS feed to get (episode_title, episode_page_url) pairs.

    The RSS <link> element for each item points to the Substack episode page.
    Cached for 1 hour.
    """
    import feedparser
    try:
        resp = _session.get(_FEED_URL, timeout=15)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.warning("Dwarkesh RSS fetch failed: %s", exc)
        return []

    feed = feedparser.parse(resp.text)
    links: list[tuple[str, str]] = []
    for entry in feed.entries:
        title = getattr(entry, "title", "").strip()
        link = getattr(entry, "link", "").strip()
        if title and link and "dwarkesh" in link:
            links.append((title, link))

    logger.debug("Dwarkesh RSS: found %d episodes", len(links))
    return links


def _fetch_transcript_text(page_url: str) -> Optional[str]:
    """Fetch a Substack episode page and extract the article body."""
    try:
        resp = _session.get(page_url, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.warning("Dwarkesh page fetch failed %s: %s", page_url, exc)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Substack free reader view — content in <div class="available-content">
    # or the full post body in <div class="body markup">
    content = (
        soup.find("div", class_="available-content")
        or soup.find("div", class_="body")
        or soup.find("article")
    )
    if content is None:
        logger.warning("Dwarkesh: could not find article body on %s", page_url)
        return None

    return content.get_text(separator="\n", strip=True)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def scrape(episode_title: str, audio_url: str = "") -> Optional[str]:
    """
    Return the plain-text transcript for a Dwarkesh episode, or None.

    Parameters
    ----------
    episode_title:
        Episode title as stored in the database (from Taddy).
    audio_url:
        Audio enclosure URL (not used; present for interface consistency).
    """
    clean_ep = _clean_title(episode_title)
    logger.info("Dwarkesh scraper: searching for %r", clean_ep)

    links = _fetch_episode_links_from_rss()
    if not links:
        logger.warning("Dwarkesh: could not retrieve episode listing")
        return None

    best_score = 0.0
    best_url = ""
    for title, url in links:
        score = _similarity(clean_ep, _clean_title(title))
        if score > best_score:
            best_score = score
            best_url = url

    if best_score < _MATCH_THRESHOLD:
        logger.warning(
            "Dwarkesh: no confident match for %r (best %.2f < %.2f)",
            clean_ep, best_score, _MATCH_THRESHOLD,
        )
        return None

    logger.info("Dwarkesh: matched %.0f%% → %s", best_score * 100, best_url)
    text = _fetch_transcript_text(best_url)

    if text:
        logger.info("Dwarkesh: extracted %d words", len(text.split()))
    return text
