"""
Cheeky Pint transcript scraper (John Collison / Stripe).

last_verified: 2026-03-26

Strategy
--------
Transistor-hosted episodes have dedicated transcript pages at:
    cheekypint.transistor.fm/{episode-slug}/transcript

This scraper:
  1. Fetches the Transistor RSS feed to find the episode page URL.
  2. Appends '/transcript' and fetches the page.
  3. Falls back to Substack at cheekypint.substack.com if Transistor fails.
"""

import logging
from difflib import SequenceMatcher
from typing import Optional

import feedparser
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_RSS_URL = "https://feeds.transistor.fm/cheeky-pint-with-john-collison"
_TRANSISTOR_BASE = "https://cheekypint.transistor.fm"
_SUBSTACK_FEED = "https://cheekypint.substack.com/feed"
_USER_AGENT = "my-daily-digest/1.0 (Cheeky Pint scraper)"
_MATCH_THRESHOLD = 0.55
_REQUEST_TIMEOUT = 10

_session = requests.Session()
_session.headers.update({"User-Agent": _USER_AGENT})


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _fetch_episode_links() -> list[tuple[str, str]]:
    """Return (title, episode_url) pairs from the Transistor RSS feed."""
    try:
        resp = _session.get(_RSS_URL, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.warning("Cheeky Pint: RSS fetch failed: %s", exc)
        return []

    feed = feedparser.parse(resp.text)
    links: list[tuple[str, str]] = []
    for entry in feed.entries:
        title = getattr(entry, "title", "").strip()
        link = getattr(entry, "link", "").strip()
        if title and link:
            links.append((title, link))

    logger.debug("Cheeky Pint: found %d episodes in RSS", len(links))
    return links


def _fetch_transistor_transcript(episode_url: str) -> Optional[str]:
    """Fetch the /transcript subpath for a Transistor episode page."""
    transcript_url = episode_url.rstrip("/") + "/transcript"
    try:
        resp = _session.get(transcript_url, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.warning("Cheeky Pint: Transistor transcript fetch failed %s: %s", transcript_url, exc)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Transistor transcript pages render text in main or a labelled container
    content = (
        soup.find("div", class_="transcript")
        or soup.find("div", id="transcript")
        or soup.find("main")
        or soup.find("article")
    )
    if content is None:
        logger.warning("Cheeky Pint: no transcript container on %s", transcript_url)
        return None

    text = content.get_text(separator="\n", strip=True)
    return text if len(text.split()) > 100 else None


def _fetch_substack_transcript(episode_title: str) -> Optional[str]:
    """Fallback: match the episode on the Cheeky Pint Substack."""
    try:
        resp = _session.get(_SUBSTACK_FEED, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        for entry in feed.entries:
            title = getattr(entry, "title", "")
            if _similarity(episode_title, title) >= _MATCH_THRESHOLD:
                link = getattr(entry, "link", "")
                if not link:
                    continue
                page = _session.get(link, timeout=_REQUEST_TIMEOUT)
                page.raise_for_status()
                soup = BeautifulSoup(page.text, "html.parser")
                content = (
                    soup.find("div", class_="available-content")
                    or soup.find("div", class_="body")
                    or soup.find("article")
                )
                if content:
                    return content.get_text(separator="\n", strip=True)
    except Exception as exc:
        logger.warning("Cheeky Pint: Substack fallback failed: %s", exc)
    return None


def scrape(episode_title: str, audio_url: str = "") -> Optional[str]:
    """Return the plain-text transcript for a Cheeky Pint episode, or None."""
    logger.info("Cheeky Pint scraper: searching for %r", episode_title)

    links = _fetch_episode_links()
    if links:
        best_score = 0.0
        best_url = ""
        for title, url in links:
            score = _similarity(episode_title, title)
            if score > best_score:
                best_score = score
                best_url = url

        if best_score >= _MATCH_THRESHOLD:
            logger.info("Cheeky Pint: matched %.0f%% → %s", best_score * 100, best_url)
            text = _fetch_transistor_transcript(best_url)
            if text:
                logger.info("Cheeky Pint: extracted %d words from Transistor", len(text.split()))
                return text
            logger.warning("Cheeky Pint: Transistor transcript unavailable — trying Substack fallback")
        else:
            logger.warning(
                "Cheeky Pint: no confident match for %r (best %.2f < %.2f) — trying Substack",
                episode_title, best_score, _MATCH_THRESHOLD,
            )
    else:
        logger.warning("Cheeky Pint: RSS empty — trying Substack fallback")

    text = _fetch_substack_transcript(episode_title)
    if text:
        logger.info("Cheeky Pint: extracted %d words from Substack fallback", len(text.split()))
    return text
