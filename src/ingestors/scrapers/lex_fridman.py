"""
Lex Fridman Podcast transcript scraper.

last_verified: 2026-03-23

Strategy
--------
Lex publishes full transcripts at lexfridman.com/category/transcripts/.
Each transcript page title matches the episode title.

This scraper:
  1. Fetches the transcripts listing (paginated, cached 24h).
  2. Finds the page whose title best matches the episode title.
  3. Fetches that transcript page and extracts the main content div.

Transcript pages use the TDBuilder WordPress theme; content is in
<div class="td-page-content"> or <div class="entry-content">.
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

_BASE_URL = "https://lexfridman.com"
_TRANSCRIPTS_URL = f"{_BASE_URL}/category/transcripts/"
_USER_AGENT = "my-daily-digest/1.0 (Lex Fridman scraper)"
_CACHE_PATH = str(Path(__file__).parent.parent.parent.parent / "data" / ".requests_cache")
_MATCH_THRESHOLD = 0.55  # Lex episode titles are often truncated — keep threshold lower
_PAGE_LIMIT = 10         # max listing pages to scan

_session = requests_cache.CachedSession(
    cache_name=_CACHE_PATH,
    expire_after=86400,  # 24 hours — transcript listings don't change often
    backend="sqlite",
)
_session.headers.update({"User-Agent": _USER_AGENT})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _clean_title(title: str) -> str:
    """Strip trailing '| Lex Fridman Podcast #N' suffixes for cleaner matching."""
    return re.sub(r"\s*\|?\s*Lex Fridman Podcast\s*#?\d*$", "", title, flags=re.IGNORECASE).strip()


def _fetch_transcript_links() -> list[tuple[str, str]]:
    """
    Return (title, url) pairs from the Lex Fridman transcripts listing.

    Paginates up to _PAGE_LIMIT pages.  Results are cached for 24h.
    """
    links: list[tuple[str, str]] = []
    page_url = _TRANSCRIPTS_URL

    for page_num in range(1, _PAGE_LIMIT + 1):
        try:
            resp = _session.get(page_url, timeout=15)
            resp.raise_for_status()
        except requests.exceptions.RequestException as exc:
            logger.warning("Failed to fetch transcript listing page %d: %s", page_num, exc)
            break

        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract post links — WordPress archive pages use <h3> or <h2> inside article tags
        found_any = False
        for article in soup.find_all("article"):
            heading = article.find(["h3", "h2", "h1"])
            if not heading:
                continue
            anchor = heading.find("a", href=True)
            if not anchor:
                continue
            title = anchor.get_text(strip=True)
            href = anchor["href"]
            if title and href:
                links.append((title, href))
                found_any = True

        # Find "next page" link
        next_link = soup.find("a", class_="next")
        if not next_link or not found_any:
            break
        page_url = next_link["href"]
        time.sleep(0.5)

    logger.debug("Found %d transcript listing entries from Lex Fridman site", len(links))
    return links


def _fetch_transcript_text(page_url: str) -> Optional[str]:
    """Fetch a transcript page and return the plain text of the main content."""
    try:
        resp = _session.get(page_url, timeout=20)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.warning("Failed to fetch transcript page %s: %s", page_url, exc)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # TDBuilder theme puts content in .td-page-content; fall back to .entry-content
    content_div = (
        soup.find("div", class_="td-page-content")
        or soup.find("div", class_="entry-content")
        or soup.find("article")
    )
    if content_div is None:
        logger.warning("Could not find content div on %s", page_url)
        return None

    return content_div.get_text(separator="\n", strip=True)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def scrape(episode_title: str, audio_url: str = "") -> Optional[str]:
    """
    Return the plain-text transcript for a Lex Fridman episode, or None.

    Parameters
    ----------
    episode_title:
        Episode title as stored in the database (from Taddy).
    audio_url:
        Audio enclosure URL (not used; present for interface consistency).
    """
    clean_episode = _clean_title(episode_title)
    logger.info("Lex Fridman scraper: searching for %r", clean_episode)

    listing = _fetch_transcript_links()
    if not listing:
        logger.warning("Lex Fridman transcript listing is empty — site may have changed")
        return None

    best_score = 0.0
    best_url = ""

    for title, url in listing:
        clean_candidate = _clean_title(title)
        score = _similarity(clean_episode, clean_candidate)
        if score > best_score:
            best_score = score
            best_url = url

    if best_score < _MATCH_THRESHOLD:
        logger.warning(
            "Lex Fridman: no confident match for %r (best score %.2f < %.2f)",
            clean_episode, best_score, _MATCH_THRESHOLD,
        )
        return None

    logger.info("Lex Fridman: matched %.0f%% confidence → %s", best_score * 100, best_url)
    text = _fetch_transcript_text(best_url)

    if text:
        word_count = len(text.split())
        logger.info("Lex Fridman: extracted %d words from transcript", word_count)
    return text
