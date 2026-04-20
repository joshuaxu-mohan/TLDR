# Stack Audit — 2026-04-20

Diagnostic-only audit across six layers: ingestion, data integrity, API, secrets,
frontend, observability. No code was modified. Findings below are cross-checked
against the actual source — subagent-reported false positives have been dropped.

## Critical (will break production)

- **src/ingestors/taddy.py:81-82** — `_graphql()` catches `HTTPError` and re-raises
  as generic `RuntimeError("Taddy API HTTP error: ...")` without the status code.
  The user already hit the "Taddy returns HTTP 500 for quota / auth problems"
  pattern; this handler masks those cases as an undifferentiated error, so the
  operator cannot tell "quota hit, retry later" from "key revoked, fix config".
  _Fix:_ inspect `response.status_code` before `raise_for_status()` and log/raise
  with the code and first ~500 chars of the body.

- **src/ingestors/taddy.py:122-147** — Batch loop has no try/except around
  `_graphql(query)`. A single bad UUID or transient 500 in chunk N raises
  `RuntimeError` and aborts the entire ingest, including chunks N+1..end that
  would have succeeded. With ~60 podcasts batched in groups of 10, one bad batch
  loses everything behind it.
  _Fix:_ wrap the per-chunk call in try/except, log + continue on failure.

- **src/storage/db.py:126-129** — `_run_migrations()` runs
  `UPDATE articles SET needs_transcription = 0 WHERE source_id IN (SELECT id FROM sources WHERE transcript_priority != 'always')`
  **unconditionally on every `init_db()` call**, and `init_db()` is invoked both
  by `main.py` at pipeline start and by `api.py:59` at every FastAPI startup.
  If a user clicks "transcribe now" on an `on_demand` episode and the API process
  restarts (deploy, crash-restart, cron-triggered reload) before Groq picks it
  up, the flag is silently wiped and the episode never transcribes. This is the
  same class of bug as the `_UPGRADED_SOURCES` issue that was already deleted.
  _Fix:_ gate the block behind a one-shot migration version row, or scope it so
  it cannot clobber a newly-set flag (e.g. `AND summary IS NULL AND content IS NULL`).

- **src/storage/db.py:131-133** — `DELETE FROM digests WHERE category = 'all'`
  also runs on every startup with no idempotency guard. If any future code path
  writes a digest row with `category='all'` (trivially possible via
  `save_digest`), it is silently deleted on next restart.
  _Fix:_ move into one-shot migration table or delete the block now that the
  legacy rows are gone.

- **src/delivery/api.py (entire router)** — No authentication of any kind on any
  route. The VM at 132.145.20.48 is public. Anyone who finds the IP can hit
  `POST /api/summarise-page`, `POST /api/articles/{id}/extend`,
  `POST /api/feed-summary/refresh`, and `POST /api/articles/{id}/transcribe` in
  a tight loop and burn the entire Gemini 500 RPD and Groq 480 min/day budget
  in under a minute. This is the single biggest production risk.
  _Fix:_ add a bearer-token middleware reading from an env var, reject
  unauthenticated requests to every write/expensive endpoint.

## High (likely to break soon or degrade silently)

- **src/ingestors/taddy.py:92-103** — `_parse_date` uses
  `datetime.utcfromtimestamp(date_published)` which (a) is deprecated and warns
  in 3.12, removed in future, and (b) returns a **naive** datetime. The returned
  value is compared against a UTC-aware `since_dt` at the caller; on any numeric
  `datePublished` this will raise `TypeError: can't compare offset-naive and
  offset-aware datetimes` and abort the entire Taddy ingest.
  _Fix:_ `datetime.fromtimestamp(date_published, tz=UTC)`. Project CLAUDE.md
  explicitly forbids `utcnow`/`utcfromtimestamp` patterns.

- **src/delivery/api.py:946-1027** — `GET /api/sources/search` interpolates the
  user query directly into a GraphQL string with only `"` escaped:
  `safe_term = q.replace('"', '\\"')`. Newlines, `}`, backslashes, and block
  terminators are not handled, so a crafted `q` can inject additional GraphQL
  fields or break the query. Compounds with "no auth".
  _Fix:_ use GraphQL variables, or restrict `q` to `[A-Za-z0-9 \-'&]{2,80}` and
  reject otherwise.

- **src/delivery/api.py POST /api/articles/{id}/extend, /api/summarise-page,
  /api/feed-summary/refresh** — All three trigger Gemini calls with no rate
  limit. Even with auth, a runaway frontend bug or a refresh-happy user could
  burn daily budget. Caching only protects repeated calls on the same
  `article_id`.
  _Fix:_ in-memory per-IP sliding window (e.g. 10/min, 100/day) on Gemini-hitting
  routes.

- **src/storage/db.py `_connection()` context manager (around line 40-50)** —
  Exception path rolls back silently with no logging of the failing statement or
  caller. When a save_article / save_digest / flag update raises mid-transaction
  the rollback leaves the DB consistent *but* there is zero diagnostic trace of
  what failed or why. Debugging production data issues becomes guesswork.
  _Fix:_ log at ERROR level with `exc_info=True` in the except branch before
  rolling back.

- **src/ingestors/whisper_transcriber.py (the ffprobe `_get_audio_duration`
  helper)** — If ffprobe fails, returns `0.0` seconds silently. The value is
  written into `transcription_log.audio_seconds`, which backs
  `db.get_groq_usage()` and the `/api/groq-budget` endpoint. A broken/missing
  ffprobe on the VM would make the budget display read "0 seconds used" while
  the real quota burns down, causing pipeline to exceed Groq limits without
  warning.
  _Fix:_ log at WARNING on ffprobe failure; either skip the episode or record
  the file's byte-size-derived estimate.

- **src/ingestors/rss.py** (published-date parsing, ~line 100 range) —
  `datetime.fromisoformat(s.rstrip("Z"))` strips the Z then parses as naive,
  which produces a naive datetime. If feedparser already handed a UTC-aware
  string, the stripping loses the tz. When later compared against `since_dt`
  (UTC-aware) this raises and the RSS ingest fails for that feed.
  _Fix:_ use `datetime.fromisoformat(s.replace("Z","+00:00"))` or normalise
  naive-to-UTC at parse time.

- **src/delivery/api.py:59** — `db.init_db()` runs at module import time. A
  migration failure or DB-locked error here aborts server startup with a raw
  traceback; there is no fallback. If the pipeline process holds a write lock
  at the moment uvicorn restarts, the API fails to come up.
  _Fix:_ wrap in try/except; log and continue — queries will surface the real
  error to the client anyway.

- **frontend/src/components/GeminiBudget.jsx:20,
  frontend/src/components/GroqBudget.jsx:21,
  frontend/src/components/PageSummary.jsx:123,
  frontend/src/components/TranscriptionLog.jsx:37** — All four use
  `.catch(() => {})` swallowing network errors silently. Widgets just disappear
  with no indication of why. User will assume "no budget used" or "no
  transcriptions ran" when the real cause is a backend outage.
  _Fix:_ set an error-state and render a placeholder ("—" or "failed to load").

## Medium (worth fixing next session)

- **src/storage/db.py:117-124** — Priority-rename migration uses f-string
  interpolation into SQL. Safe today because `old`/`new` are hardcoded literals,
  but the pattern is fragile. Parameterise.

- **src/storage/db.py:108-112** — Migration loop catches
  `sqlite3.OperationalError` and `pass`es, hiding typos, permission errors, and
  disk-full just as effectively as "column already exists". At minimum log the
  SQL and exception at DEBUG so the real failure is inspectable.

- **src/storage/db.py (articles table FK)** — `source_id` references
  `sources(id)` with no `ON DELETE CASCADE`. `delete_source()` will fail while
  any article row exists, and there is no deletion path for those articles. In
  practice this means source deletion almost never works. Pick a policy (soft
  delete via `active=0`, or cascade) and apply it.

- **src/delivery/api.py CreateSourceRequest / UpdateSourceRequest
  (lines 85-109)** — No `Field(max_length=...)` on any string field, and
  `PageSummaryRequest.article_ids` has no `max_items`. A malicious or buggy
  client can POST megabyte-sized names/descriptions or 100k-entry id lists.
  _Fix:_ add `Field(max_length=500)` / `Field(max_length=2000)` / list bound.

- **src/delivery/api.py:1265** — WhatsApp handler logs exceptions with
  `exc_info=True`. Full tracebacks including filesystem paths leak into logs.
  Acceptable for a private VM, worth tightening before any log-shipping.

- **src/delivery/telegram.py `_e()` (around line 36-42)** — MarkdownV2 escape
  regex does not cover `\n`, `\r`, `\t`. A podcast title with an embedded
  newline (rare but has happened in RSS) can corrupt the formatted Telegram
  message or trigger a 400 from Telegram's API.

- **src/delivery/telegram.py** send failures are logged at WARNING regardless
  of status code. 401 (revoked bot token) and 403 (chat blocked the bot) are
  permanent conditions that deserve ERROR; 5xx / timeouts are fine at WARNING.

- **src/main.py cycle entrypoint** — No file lock / single-instance guard. APScheduler's
  `max_instances=1` only covers the in-process case. If cron and the daemon
  both run, or if two crons overlap while one is slow, they will stomp on each
  other via the shared SQLite file. At minimum acquire `data/.pipeline.lock`
  with a 2-hour stale-lock timeout.

- **src/main.py error-notification block (around line 182-186)** — The outer
  `except Exception: pass` on the Telegram failure path swallows the inner
  exception completely. If Telegram is down at the same time as the pipeline
  crash you get zero signal. Log at WARNING before passing.

- **src/summariser/summariser.py JSONDecode path (around line 252-257)** — A
  malformed Gemini JSON response drops the whole batch silently with only a
  DEBUG log. Upgrade to ERROR and include the first 500 chars of the raw
  response — this is how we discovered the Gemini-returns-prose-instead-of-JSON
  failure mode last time.

- **frontend/src/pages/Sources.jsx:16, 116-122, 1044** — `CORE_TOPICS`,
  `CATEGORY_OPTIONS`, `SOURCE_TAGS` are hardcoded in the frontend and must
  match backend values exactly. Nothing enforces sync. If a future edit adds a
  category on the backend only, the UI silently misrenders. Consider exposing a
  `/api/categories` lookup and fetching it once on app load.

## Low (nice to have, only if bored)

- **src/ingestors/whisper_transcriber.py `_parse_retry_after`** — does not
  handle HTTP-date form of the Retry-After header (only numeric). Rare but
  spec-legal.

- **src/ingestors/whisper_transcriber.py small-file skip** — duplicated
  between the batch and on-demand paths; consolidate.

- **src/ingestors/colossus.py cache path** — constructed relative to
  `__file__` with hardcoded `..` traversal. Moves break silently.

- **src/ingestors/scrapers/thoughts_on_the_market.py** — uses
  `not (u in seen or seen.add(u))` idiom for deduplication; correct but
  opaque.

- **scripts/** folder contains `check_err.txt`, `check_out.txt`,
  `scraper_test_err.txt`, `scraper_test_out.txt` — stale test output
  committed to the repo. Harmless, but clutter.

- **src/config/settings.py + src/ingestors/taddy.py:53-57,
  src/ingestors/whisper_transcriber.py (Groq key check),
  src/summariser/summariser.py:98-103** — all four services raise `RuntimeError`
  at first use if their key is missing, rather than at pipeline start-up. A
  missing key produces a half-completed run (e.g. Taddy works, Gemini explodes
  at the summarise step). A single startup-time "validate required keys" helper
  would fail fast.

## Explicit non-findings

These were checked and are fine — do not redo next audit.

- **`.env` is NOT in git history.** `git log --all -- .env` returns empty.
  `.gitignore` correctly excludes `.env`, `.env.*`, `CLAUDE.local.md`,
  `*.db`, `logs/`. No secrets found committed anywhere in the repo. A
  subagent initially flagged this as critical but the claim did not hold up
  to verification.

- **`requests.get` in `whisper_transcriber.py _download_audio`** does pass
  `timeout=_DOWNLOAD_TIMEOUT` (line 87). False positive.

- **No hardcoded API keys / bearer tokens / SSH keys / passwords** found in
  any `.py`, `.md`, `.yaml`, `.json`, or `.jsx` file in the repo (excluding
  `.git`, `node_modules`, `data`, `logs`, `__pycache__`).

- **No direct network calls outside `frontend/src/api.js`.** Every frontend
  component goes through the central client. Good discipline.

- **No `dangerouslySetInnerHTML`** anywhere in the frontend. Markdown is
  rendered via `react-markdown` which sanitises by default.

- **No hardcoded backend URLs, localhost, 127.0.0.1, or the Oracle VM IP**
  in `frontend/src/`. Production build correctly relies on same-origin
  `/api/...` paths served by FastAPI.

- **`PATCH /api/sources/{id}`** correctly whitelists editable columns via
  the `UpdateSourceRequest` Pydantic model and the allowed-fields list in
  `db.update_source()`; you cannot flip `source_id`, `url`, or `id` through
  this endpoint.

- **No `.get()` calls on `sqlite3.Row` objects** found. `dict(row).get(...)`
  is used in the two places that need optional access (api.py:465,
  whisper_transcriber.py:340) — this is safe and matches the project rule.

- **`seed_from_yaml()`** (src/config/loader.py) correctly skips existing
  rows by URL and does not overwrite `transcript_priority`, `active`, or
  `content_category`. User configuration is preserved.

- **CORS** is restricted to `settings.cors_origins` (not `["*"]`). Fine.

- **`INSERT OR IGNORE`** in `save_article()` and `get_or_create_source()`
  is the correct idempotent pattern. No risk of duplicate-row spam.

- **`PRAGMA foreign_keys = ON`** is set per-connection in `_connection()`,
  so FK constraints are actually enforced.

- **Scraper runner** (`src/ingestors/scrapers/runner.py:120-126`) has an
  explicit guard that rejects any article reaching the scraper queue with
  `transcript_priority != 'always'` — on-demand episodes cannot be
  incorrectly promoted into the Groq batch by this path.

- **Most scrapers handle failures correctly:** return `None` on exception,
  log at WARNING, let the runner flag `needs_transcription` as Groq fallback.
  Spot-checked `lex_fridman`, `dwarkesh_podcast`, `colossus`, `decoder`,
  `goldman_sachs`, `cheeky_pint`, `thoughts_on_the_market` — all conform.
