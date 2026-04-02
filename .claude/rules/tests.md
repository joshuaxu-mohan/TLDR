--- paths: ["tests/**"] ---

# Test Rules

## Framework

- Use pytest with plain assert statements, not unittest.TestCase
- Name test files `test_<module>.py` matching the source module name
- Each test function tests one behaviour; name it `test_<what_it_does>`
- Use fixtures for shared setup (DB connections, mock HTTP responses)

## Mocking

- Mock all external HTTP calls using `responses` or `unittest.mock.patch`
- Mock Groq API calls — never make real transcription requests in tests
- Mock Gemini API calls (`gemini-2.5-flash-lite-preview`, `gemini-2.5-flash`) — never make real summarisation requests in tests
- Mock Taddy GraphQL calls — never make real API requests in tests
- Use a fresh in-memory SQLite database for each test (`:memory:` or `tmp_path` fixture)
- Never make real network requests in tests

## Coverage expectations

- At least one happy-path and one error-path test per public function
- Scraper tests must include a fixture with a sample HTML page and test both successful parse and graceful failure on changed markup
- Ingestor tests must verify timezone-aware datetime handling (naive datetimes should be rejected or normalised)
- Budget tests (Groq, Gemini) must verify that the pipeline stops gracefully when limits are hit

## DB access in tests

- Use `db.init_db()` on a temporary SQLite file or `:memory:` at test start
- SQLite Row access uses bracket notation with `"key" in row.keys()` guard — never `.get()` — verify this pattern in any DB-touching code under test
