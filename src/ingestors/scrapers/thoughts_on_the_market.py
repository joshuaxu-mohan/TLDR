"""
Thoughts on the Market transcript scraper (Morgan Stanley).

last_verified: 2026-03-26

Strategy
--------
Morgan Stanley publishes episode pages at:
    morganstanley.com/insights/podcasts/thoughts-on-the-market/{slug}

The transcript text is present in the HTML DOM (behind a "View Transcript"
toggle) and is accessible without JavaScript execution.

This scraper:
  1. Fetches the Art19 RSS feed to discover Morgan Stanley episode page URLs.
  2. Matches the requested title using string similarity.
  3. Fetches the episode page and extracts the transcript text from the HTML.
"""

import logging
import re
from difflib import SequenceMatcher
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_MS_BASE = "https://www.morganstanley.com"
_LISTING_URL = f"{_MS_BASE}/insights/podcasts/thoughts-on-the-market"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_MATCH_THRESHOLD = 0.55
_REQUEST_TIMEOUT = 10

_session = requests.Session()
_session.headers.update({
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
})


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _fetch_episode_links() -> list[tuple[str, str]]:
    """
    Return (title, episode_url) pairs from the Morgan Stanley podcast listing page.

    The Art19 RSS feed does not include Morgan Stanley episode page URLs, so we
    scrape the listing page at morganstanley.com directly.
    """
    try:
        resp = _session.get(_LISTING_URL, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.warning("Thoughts on the Market: listing page fetch failed: %s", exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    links: list[tuple[str, str]] = []
    section_path = "/insights/podcasts/thoughts-on-the-market/"

    for anchor in soup.find_all("a", href=True):
        href: str = anchor["href"]
        if section_path not in href:
            continue
        # Skip the listing page itself
        full_url = href if href.startswith("http") else f"{_MS_BASE}{href}"
        if full_url.rstrip("/") == _LISTING_URL.rstrip("/"):
            continue
        title = anchor.get_text(strip=True)
        if title and len(title) > 5:
            links.append((title, full_url))

    # Deduplicate by URL
    seen: set[str] = set()
    deduped = [(t, u) for t, u in links if not (u in seen or seen.add(u))]  # type: ignore[func-returns-value]
    logger.debug("Thoughts on the Market: found %d episodes via listing page", len(deduped))
    return deduped


def _fetch_transcript_text(episode_url: str) -> Optional[str]:
    """Fetch the Morgan Stanley episode page and extract the transcript."""
    try:
        resp = _session.get(episode_url, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.warning("Thoughts on the Market: page fetch failed %s: %s", episode_url, exc)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Morgan Stanley embeds the transcript in a div that is toggled via CSS/JS.
    # Try transcript-specific selectors first, then fall back to heading scan.
    for tag, attrs in [
        ("div", {"class": re.compile(r"transcript", re.I)}),
        ("div", {"id": re.compile(r"transcript", re.I)}),
        ("section", {"class": re.compile(r"transcript", re.I)}),
        ("div", {"class": re.compile(r"rich.text|wysiwyg|content.body", re.I)}),
    ]:
        el = soup.find(tag, attrs)
        if el:
            text = el.get_text(separator="\n", strip=True)
            if len(text.split()) > 100:
                return text

    # Fallback: find a heading labelled "transcript" and collect following siblings
    for heading in soup.find_all(["h2", "h3", "h4"]):
        if "transcript" in heading.get_text().lower():
            parts: list[str] = []
            for sibling in heading.find_next_siblings():
                if sibling.name in ("h2", "h3", "h4"):
                    break
                text = sibling.get_text(separator="\n", strip=True)
                if text:
                    parts.append(text)
            if parts:
                return "\n".join(parts)

    logger.warning("Thoughts on the Market: no transcript found on %s", episode_url)
    return None


def scrape(episode_title: str, audio_url: str = "") -> Optional[str]:
    """Return the plain-text transcript for a Thoughts on the Market episode, or None."""
    logger.info("Thoughts on the Market scraper: searching for %r", episode_title)

    links = _fetch_episode_links()
    if not links:
        logger.warning("Thoughts on the Market: could not retrieve episode listing")
        return None

    best_score = 0.0
    best_url = ""
    for title, url in links:
        score = _similarity(episode_title, title)
        if score > best_score:
            best_score = score
            best_url = url

    if best_score < _MATCH_THRESHOLD:
        logger.warning(
            "Thoughts on the Market: no confident match for %r (best %.2f < %.2f)",
            episode_title, best_score, _MATCH_THRESHOLD,
        )
        return None

    logger.info("Thoughts on the Market: matched %.0f%% → %s", best_score * 100, best_url)
    text = _fetch_transcript_text(best_url)
    if text:
        logger.info("Thoughts on the Market: extracted %d words", len(text.split()))
    return text
