"""
Decoder with Nilay Patel transcript scraper (The Verge).

last_verified: 2026-03-26

Strategy
--------
Each Decoder episode is published as a full-text article on theverge.com.
The Megaphone RSS feed strips hyperlinks from descriptions, so we index
episodes from The Verge's listing page directly.

This scraper:
  1. Fetches the Decoder listing page at theverge.com/decoder-podcast-with-nilay-patel.
  2. Extracts episode article links (hrefs containing /podcast/).
  3. Matches the requested title against anchor text using fuzzy similarity.
  4. Fetches the matched Verge article and returns the body text (the transcript).
"""

import json
import logging
import re
from difflib import SequenceMatcher
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_LISTING_URL = "https://www.theverge.com/decoder-podcast-with-nilay-patel"
_VERGE_BASE = "https://www.theverge.com"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_MATCH_THRESHOLD = 0.50
_REQUEST_TIMEOUT = 10

_session = requests.Session()
_session.headers.update({
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
})


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _strip_punctuation(s: str) -> str:
    """Lower-case and strip punctuation for loose substring matching."""
    return re.sub(r"[^\w\s]", "", s.lower()).strip()


def _fetch_episode_links() -> list[tuple[str, str]]:
    """
    Return (title, article_url) pairs from the Decoder listing page.

    The Verge serves its listing page in static HTML for SEO, so a plain
    GET is sufficient — no JavaScript execution needed.
    """
    try:
        resp = _session.get(_LISTING_URL, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.warning("Decoder: listing page fetch failed: %s", exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    links: list[tuple[str, str]] = []

    for anchor in soup.find_all("a", href=True):
        href: str = anchor["href"]
        # Episode articles live under /podcast/ path segments
        if "/podcast/" not in href:
            continue
        full_url = href if href.startswith("http") else f"{_VERGE_BASE}{href}"
        # Skip the listing page itself and any non-Verge URLs
        if _VERGE_BASE not in full_url:
            continue
        title = anchor.get_text(strip=True)
        if title and len(title) > 8:
            links.append((title, full_url))

    # Deduplicate by URL, preserving order
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for t, u in links:
        if u not in seen:
            seen.add(u)
            deduped.append((t, u))

    logger.debug("Decoder: found %d episode links on listing page", len(deduped))
    return deduped


def _fetch_verge_article(url: str) -> Optional[str]:
    """Fetch a Verge article page and extract the body text.

    The Verge embeds full article content in a ``__NEXT_DATA__`` JSON blob under
    ``props.pageProps.hydration.responses[?].data.node.blocks[].paragraphContents[].html``.
    This is more reliable than CSS selectors, which change across redesigns.
    """
    try:
        resp = _session.get(url, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.warning("Decoder: Verge article fetch failed %s: %s", url, exc)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Primary: reconstruct article from __NEXT_DATA__ blocks
    next_data_tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if next_data_tag and next_data_tag.string:
        try:
            data = json.loads(next_data_tag.string)
            responses = (
                data.get("props", {})
                .get("pageProps", {})
                .get("hydration", {})
                .get("responses", [])
            )
            post = next(
                (r for r in responses if r.get("operationName") == "PostLayoutQuery"),
                None,
            )
            if post:
                blocks = post.get("data", {}).get("node", {}).get("blocks", [])
                parts: list[str] = []
                for block in blocks:
                    for para in block.get("paragraphContents", []):
                        html = para.get("html") or para.get("text") or ""
                        if html:
                            text = BeautifulSoup(html, "html.parser").get_text(
                                separator=" ", strip=True
                            )
                            if text.strip():
                                parts.append(text.strip())
                if parts:
                    full_text = "\n\n".join(parts)
                    if len(full_text.split()) > 100:
                        logger.debug(
                            "Decoder: extracted %d words from __NEXT_DATA__",
                            len(full_text.split()),
                        )
                        return full_text
        except Exception as exc:  # noqa: BLE001
            logger.warning("Decoder: __NEXT_DATA__ parse failed for %s: %s", url, exc)

    logger.warning("Decoder: could not extract article body from %s", url)
    return None


def scrape(episode_title: str, audio_url: str = "") -> Optional[str]:
    """Return the plain-text transcript for a Decoder episode, or None."""
    logger.info("Decoder scraper: searching for %r", episode_title)

    links = _fetch_episode_links()
    if not links:
        logger.warning("Decoder: could not retrieve episode listing")
        return None

    clean_query = _strip_punctuation(episode_title)

    best_score = 0.0
    best_url = ""
    for title, url in links:
        score = _similarity(episode_title, title)
        # Also try loose substring match: query words appearing in link title
        if score < _MATCH_THRESHOLD:
            clean_title = _strip_punctuation(title)
            if clean_query and clean_query in clean_title:
                score = max(score, _MATCH_THRESHOLD + 0.01)
        if score > best_score:
            best_score = score
            best_url = url

    if best_score < _MATCH_THRESHOLD:
        logger.warning(
            "Decoder: no confident match for %r (best %.2f < %.2f)",
            episode_title, best_score, _MATCH_THRESHOLD,
        )
        return None

    logger.info("Decoder: matched %.0f%% → %s", best_score * 100, best_url)
    text = _fetch_verge_article(best_url)
    if text:
        logger.info("Decoder: extracted %d words", len(text.split()))
    return text
