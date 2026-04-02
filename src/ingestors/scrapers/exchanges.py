"""
Exchanges at Goldman Sachs — thin wrapper around goldman_sachs.py.

last_verified: 2026-03-26

The normalised source name 'exchanges' maps to this module.
All scraping logic lives in goldman_sachs.py.
"""

from typing import Optional
from src.ingestors.scrapers import goldman_sachs


def scrape(episode_title: str, audio_url: str = "") -> Optional[str]:
    """Delegate to the shared Goldman Sachs scraper for Exchanges."""
    return goldman_sachs.scrape(
        episode_title=episode_title,
        audio_url=audio_url,
        section="exchanges",
    )
