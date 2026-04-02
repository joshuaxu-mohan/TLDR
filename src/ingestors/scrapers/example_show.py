"""
TEMPLATE — copy this file and rename it to match the normalised source name.

Naming convention
-----------------
A source named "My Cool Podcast" in sources.yaml maps to this module path:

    src/ingestors/scrapers/my_cool_podcast.py

The podcast ingestor discovers scrapers by importing
`src.ingestors.scrapers.<normalised_name>` where the normalised name is the
source's `name` field, lower-cased, with spaces and hyphens replaced by
underscores.

What to implement
-----------------
1. Find where the show publishes HTML transcripts (usually /episodes/<slug> or
   similar).
2. Identify the CSS selector or HTML structure that wraps the transcript text.
3. Fetch the page and extract the text in `scrape()` below.
4. Return None if the page has no transcript (e.g. episode not yet published),
   so the ingestor falls through to tier 2 (RSS) automatically.

Requirements
------------
- Always include a User-Agent header on HTTP requests (see below).
- Raise exceptions freely — the ingestor catches them and falls through to tier 2.
- Do not store state; this function may be called concurrently in future.
"""

import logging
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_USER_AGENT = "my-daily-digest/1.0 (website scraper)"
_REQUEST_TIMEOUT = 15


def scrape(episode_url: str, episode_title: str) -> Optional[str]:
    """
    Fetch and return the plain-text transcript for one episode.

    Parameters
    ----------
    episode_url:
        The episode's canonical web URL (from the RSS <link> field).
    episode_title:
        Human-readable title for log messages.

    Returns
    -------
    str
        Plain text of the transcript (HTML stripped).  Should be at least a few
        hundred words for the pipeline to consider it a usable transcript.
    None
        If no transcript is found on the page (episode not yet transcribed,
        paywalled, etc.).  Returning None causes the ingestor to try tier 2.
    """
    logger.info("Scraping transcript for: %s", episode_title)

    response = requests.get(
        episode_url,
        timeout=_REQUEST_TIMEOUT,
        headers={"User-Agent": _USER_AGENT},
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # TODO: replace this selector with the one that works for this show.
    # Examples:
    #   transcript_div = soup.select_one("div.transcript-body")
    #   transcript_div = soup.find("section", {"data-component": "transcript"})
    transcript_div = soup.select_one("div.TODO-replace-this-selector")

    if transcript_div is None:
        logger.debug("No transcript element found at %s", episode_url)
        return None

    return transcript_div.get_text(separator=" ", strip=True)
