--- paths: ["src/ingestors/scrapers/**"] ---

# Scraper Rules

## Active scrapers

Nine scraper modules exist in `src/ingestors/scrapers/` (plus `runner.py`, `__init__.py`, `example_show.py`):

| Module | Show(s) | Strategy |
|---|---|---|
| `lex_fridman.py` | Lex Fridman Podcast | Full transcript from lexfridman.com episode pages |
| `dwarkesh_podcast.py` | Dwarkesh Podcast | Full transcript from dwarkesh.com episode pages |
| `colossus.py` | Invest Like the Best, Business Breakdowns | RSS/Megaphone fallback; Cloudflare blocks auth login |
| `invest_like_the_best.py` | Invest Like the Best | Thin wrapper delegating to `colossus.py` |
| `business_breakdowns.py` | Business Breakdowns | Thin wrapper delegating to `colossus.py` |
| `cheeky_pint.py` | Cheeky Pint | Transistor transcript page (`/transcript` suffix), Substack fallback |
| `goldman_sachs.py` | Exchanges at GS, The Markets | PDF transcripts; slug constructed from episode title |
| `exchanges.py` | Exchanges at Goldman Sachs | Thin wrapper delegating to `goldman_sachs.py` with `section="exchanges"` |
| `the_markets.py` | The Markets | Thin wrapper delegating to `goldman_sachs.py` with `section="the-markets"` |
| `decoder.py` | Decoder with Nilay Patel | The Verge listing page; fuzzy title match; requires `_SCRAPER_MODULE_OVERRIDES` entry in runner.py because name contains punctuation |
| `thoughts_on_the_market.py` | Thoughts on the Market | Art19 RSS → Morgan Stanley episode pages; transcript in HTML DOM (no JS required) |

`example_show.py` is a template with interface documentation — do not delete.

## Runner architecture (`runner.py`)

- `run_scrapers(fallback_to_whisper=False)` — queries DB for `transcript_priority='always'` articles with NULL content, dispatches scrapers
- `get_scraper_for_source(source_name)` — public API used by `api.py` on-demand transcription endpoint
- Module resolution order:
  1. Check `_SCRAPER_MODULE_OVERRIDES` (e.g. `"decoder with nilay patel"` → `"decoder"`)
  2. Normalise source name (`My Podcast` → `my_podcast`)
  3. Try exact normalised name, then strip trailing suffixes (`_podcast`, `_show`, `_pod`, `_radio`, `_audio`)
- `_TRANSCRIPT_MIN_WORDS = 500` — transcripts shorter than this are treated as failures
- On scraper failure or short transcript: log WARNING, flag `needs_transcription=1` if `fallback_to_whisper=True` and audio URL present

## Conventions

- Each scraper exposes exactly one public function: `scrape(episode_title: str, audio_url: str = "") -> Optional[str]`
- Use BeautifulSoup4 with `html.parser` (no lxml dependency)
- If a scraper fails (site redesign, 404, timeout), log a WARNING and return `None` — never raise. The fallback-to-Groq pattern handles it.
- Add a `last_verified: YYYY-MM-DD` date in the module docstring whenever the scraper is confirmed working
- All HTTP requests must include a `User-Agent` header and a 10-second timeout
- Do not use requests-cache; scrapers run at most 3x/day
- When adding a new scraper whose normalised source name would not map cleanly to a Python identifier, add an entry to `_SCRAPER_MODULE_OVERRIDES` in `runner.py`
- Do not attempt programmatic login to Colossus — Cloudflare blocks it. Use RSS fallback only.
