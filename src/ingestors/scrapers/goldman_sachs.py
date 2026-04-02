"""
Goldman Sachs podcast transcript scraper — Exchanges + The Markets.

last_verified: 2026-03-26

Strategy
--------
Goldman Sachs publishes PDF transcripts for both podcasts.  The episode
listing pages are JavaScript-rendered and cannot be scraped statically, so
this module constructs the PDF URL directly from the episode title.

PDF URL patterns:
    https://www.goldmansachs.com/pdfs/insights/goldman-sachs-exchanges/{slug}/transcript.pdf
    https://www.goldmansachs.com/pdfs/insights/the-markets/{slug}/transcript.pdf

The slug is derived by slugifying the episode title (lowercase, non-alphanumeric
characters replaced by hyphens, consecutive hyphens collapsed).

Two-step resolution
-------------------
1. Try the slugified title directly as the full PDF slug.  This works when the
   GS slug exactly matches the slugified title (common for Exchanges episodes).

2. If the direct URL returns 404, query the GS sitemap to find the correct slug.
   GS often appends guest names to The Markets episode slugs:
       "The Market Is Fragile"
       → the-market-is-fragile-john-storey-on-finding-opportunities-in-turbulent-markets
   We find the best sitemap PDF URL whose slug starts with the query slug.
   The sitemap is cached in memory for the process lifetime.

Akamai edge note
----------------
GS routes all /pdfs/ requests through a JavaScript PDF viewer unless the
request carries a Referer header — Akamai treats a bare request (no Referer)
as a direct-link and redirects to the viewer, while any Referer causes it to
serve the raw PDF binary.  We set Referer: https://www.goldmansachs.com/ on
the session to bypass the viewer transparently.

Called by exchanges.py and the_markets.py thin wrappers.
"""

import logging
import re
import tempfile
from pathlib import Path
from typing import Optional

import pdfplumber
import requests

logger = logging.getLogger(__name__)

_GS_BASE = "https://www.goldmansachs.com"
_PDF_BASE = f"{_GS_BASE}/pdfs/insights"
_SITEMAP_URL = f"{_GS_BASE}/sitemap-1.xml"

# section identifier → PDF path segment
_SECTION_PDF_PATHS: dict[str, str] = {
    "exchanges": "goldman-sachs-exchanges",
    "the-markets": "the-markets",
}

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_REQUEST_TIMEOUT = 20

_session = requests.Session()
_session.headers.update({
    "User-Agent": _USER_AGENT,
    "Accept": "application/pdf,*/*;q=0.9",
    "Accept-Language": "en-GB,en;q=0.9",
    # Akamai edge rule: any Referer header causes the CDN to serve the raw PDF
    # instead of redirecting to the JavaScript PDF viewer.
    "Referer": f"{_GS_BASE}/",
})

# Module-level cache: section → list of (slug, full_pdf_url)
_sitemap_index: dict[str, list[tuple[str, str]]] = {}
_sitemap_fetched: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(title: str) -> str:
    """
    Convert an episode title to a URL slug.

    'Private Credit Concerns in Context' → 'private-credit-concerns-in-context'
    "New Mountain Capital's Steve Klinsky" → 'new-mountain-capitals-steve-klinsky'

    Apostrophes and right-single-quotes are stripped (not replaced with a hyphen)
    so that possessives like "Capital's" become "capitals" rather than "capital-s".
    All remaining non-alphanumeric characters are collapsed to a single hyphen.
    """
    slug = title.lower()
    slug = re.sub(r"['\u2019\u2018]", "", slug)   # strip apostrophes/smart quotes
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _load_sitemap_index() -> None:
    """
    Fetch the GS sitemap and populate _sitemap_index with PDF transcript URLs.

    The sitemap lists every PDF transcript URL directly, e.g.:
        /pdfs/insights/the-markets/{slug}/transcript.pdf
        /pdfs/insights/goldman-sachs-exchanges/{slug}/transcript.pdf

    Called at most once per process.  Failures are logged and silently ignored
    (the direct-URL path still works for Exchanges episodes).
    """
    global _sitemap_fetched
    if _sitemap_fetched:
        return
    _sitemap_fetched = True

    try:
        resp = _session.get(_SITEMAP_URL, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.warning("Goldman Sachs: sitemap fetch failed: %s", exc)
        return

    for section, pdf_path in _SECTION_PDF_PATHS.items():
        pattern = re.compile(
            rf"/pdfs/insights/{re.escape(pdf_path)}/([^/]+)/transcript\.pdf"
        )
        matches = pattern.findall(resp.text)
        entries = [
            (slug, f"{_PDF_BASE}/{pdf_path}/{slug}/transcript.pdf")
            for slug in matches
        ]
        _sitemap_index[section] = entries
        logger.debug(
            "Goldman Sachs sitemap: %d PDF URLs indexed for section=%s",
            len(entries), section,
        )


def _find_sitemap_pdf_url(section: str, query_slug: str) -> Optional[str]:
    """
    Search the sitemap index for a PDF URL whose slug starts with query_slug.

    GS sometimes appends guest names to episode slugs:
        query_slug  = 'the-market-is-fragile'
        actual slug = 'the-market-is-fragile-john-storey-on-finding-...'

    Returns the best match URL, or None.
    """
    _load_sitemap_index()
    entries = _sitemap_index.get(section, [])
    if not entries:
        return None

    # Prefer exact match, then prefix match with longest shared prefix
    exact = [(slug, url) for slug, url in entries if slug == query_slug]
    if exact:
        return exact[0][1]

    prefix = [(slug, url) for slug, url in entries if slug.startswith(query_slug + "-")]
    if prefix:
        # Pick the shortest slug (closest to the query title)
        prefix.sort(key=lambda t: len(t[0]))
        return prefix[0][1]

    return None


def _extract_pdf_text(pdf_url: str) -> Optional[str]:
    """
    Download a PDF and extract its full text using pdfplumber.

    Downloads to a named temp file and cleans up in a finally block.
    Returns None if the URL returns 404, is intercepted by the JS viewer,
    or the PDF cannot be parsed.
    """
    try:
        resp = _session.get(pdf_url, timeout=_REQUEST_TIMEOUT)
        if resp.status_code == 404:
            logger.debug("Goldman Sachs: PDF not found (404): %s", pdf_url)
            return None
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.warning("Goldman Sachs: PDF download failed %s: %s", pdf_url, exc)
        return None

    # Safety net: Akamai may still serve HTML viewer if Referer is stripped by
    # a redirect hop — detect and fail gracefully rather than passing HTML to pdfplumber.
    content_type = resp.headers.get("Content-Type", "")
    if "text/html" in content_type:
        logger.debug(
            "Goldman Sachs: PDF URL returned HTML (JS viewer): %s", pdf_url
        )
        return None

    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path = Path(tmp.name)

        with pdfplumber.open(tmp_path) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        text = "\n".join(pages).strip()
        return text if text else None

    except Exception as exc:
        logger.warning("Goldman Sachs: PDF extraction failed for %s: %s", pdf_url, exc)
        return None

    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def scrape(episode_title: str, audio_url: str = "", section: str = "exchanges") -> Optional[str]:
    """
    Return the plain-text transcript for a Goldman Sachs podcast episode, or None.

    Parameters
    ----------
    episode_title:
        Episode title as stored in the database.
    audio_url:
        Not used; present for interface consistency.
    section:
        'exchanges' for Exchanges at Goldman Sachs, 'the-markets' for The Markets.
    """
    pdf_path = _SECTION_PDF_PATHS.get(section)
    if not pdf_path:
        logger.error("Goldman Sachs: unknown section %r", section)
        return None

    slug = _slugify(episode_title)
    direct_url = f"{_PDF_BASE}/{pdf_path}/{slug}/transcript.pdf"

    # Step 1: try the slugified title as the exact PDF slug
    logger.info("Goldman Sachs [%s]: trying direct PDF URL for %r", section, episode_title)
    text = _extract_pdf_text(direct_url)
    if text:
        logger.info("Goldman Sachs [%s]: extracted %d words (direct slug)", section, len(text.split()))
        return text

    # Step 2: direct URL was 404 — search the sitemap for a slug that starts
    # with our query slug (GS often appends guest names)
    logger.debug(
        "Goldman Sachs [%s]: direct URL failed for %r (slug=%r) — trying sitemap",
        section, episode_title, slug,
    )
    sitemap_url = _find_sitemap_pdf_url(section, slug)
    if sitemap_url and sitemap_url != direct_url:
        logger.info("Goldman Sachs [%s]: sitemap match → %s", section, sitemap_url)
        text = _extract_pdf_text(sitemap_url)
        if text:
            logger.info(
                "Goldman Sachs [%s]: extracted %d words (sitemap slug)",
                section, len(text.split()),
            )
            return text

    logger.warning(
        "Goldman Sachs [%s]: no transcript PDF found for %r (slug=%r)",
        section, episode_title, slug,
    )
    return None
