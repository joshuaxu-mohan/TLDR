"""
Invest Like the Best — thin wrapper around the shared Colossus scraper.

last_verified: 2026-03-23

The normalised source name 'invest_like_the_best' maps to this module.
All scraping logic lives in colossus.py.
"""

from typing import Optional
from src.ingestors.scrapers import colossus

_SERIES_SLUG = "invest-like-the-best"


def scrape(episode_title: str, audio_url: str = "") -> Optional[str]:
    """Delegate to the shared Colossus scraper for this show."""
    return colossus.scrape(
        episode_title=episode_title,
        audio_url=audio_url,
        series_slug=_SERIES_SLUG,
    )
