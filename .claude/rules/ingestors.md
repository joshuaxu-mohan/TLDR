--- paths: ["src/ingestors/**"] ---

# Ingestor Rules

## Architecture

- **Two-tier transcript priority only**: `always` (auto-transcribed every pipeline run via Groq) and `on_demand` (ingested with podcast description, user triggers transcription from the frontend)
- **Transcription**: Groq Whisper API only (`whisper-large-v3`). There is NO local Whisper. Do not add torch, whisper, or any local audio model dependency.
- **Episode discovery**: Taddy GraphQL for podcast metadata only (title, description, published date, audio URL). Do NOT attempt Taddy transcript fetches — they return 400 for most shows.
- **Newsletter ingestion**: feedparser RSS; full article content fetched from RSS `<content:encoded>` or scraped from the URL.
- **Podcast ingestion flow**: Taddy (metadata) → scrapers (website transcripts, `always` priority) → Groq Whisper (audio transcription, `always` fallback + `on_demand` on request)
- `src/ingestors/podcast.py` has been deleted — it was dead code superseded by `taddy.py`.

## Pipeline flow (two-phase summarisation)

1. RSS + Taddy fetch metadata only
2. Scrapers attempt website transcripts for `always`-priority episodes
3. Early summarise + digest (newsletters, descriptions, scraped transcripts)
4. Groq Whisper transcription (max 5 episodes/run, budget-gated)
5. Post-Whisper summarise + digest (newly transcribed episodes only)

## Conventions

- Wrap all HTTP requests in try/except with specific error types (`ConnectionError`, `Timeout`, `HTTPError`)
- Log the source name and URL on both success and failure at INFO/WARNING level
- Never silently swallow exceptions; always log at WARNING or ERROR level
- All parsed datetimes MUST be normalised to UTC-aware before comparing with `since_dt` — use `datetime.now(datetime.UTC)`, never `datetime.utcnow()`
- Include a `User-Agent` header on all HTTP requests
- Use `pathlib.Path` for all file paths — no hardcoded Windows backslashes

## Groq-specific

- Audio files are downloaded to a temp file, chunked with ffmpeg if >24 MB
- Skip audio files smaller than 2 MB (`_MIN_AUDIO_SIZE_BYTES`) to avoid transcribing trailers and promos — applies in both batch and on-demand paths
- Handle 429 responses by parsing the `Retry-After` header. If wait >60s, stop the batch gracefully — remaining episodes carry to the next pipeline run.
- Pre-flight budget check: skip episodes if hourly budget <5 minutes remaining
- Log audio duration to `transcription_log` table for budget tracking
- Temp files must be cleaned up in a `finally` block regardless of platform

## Deployment

- Pipeline runs on Windows (Task Scheduler) and Oracle Cloud ARM VM (planned, crontab)
- Windows Python: `C:\Users\xujos\AppData\Local\Python\bin\python.exe`
- Start server: `uvicorn src.delivery.api:app --host 0.0.0.0 --port 8000` (no `--reload` on Windows — causes WinError 6)
