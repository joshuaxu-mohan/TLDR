/**
 * API client for the Daily Digest FastAPI backend.
 *
 * All functions throw an Error on non-2xx responses so callers can catch them
 * and display an error state.  The base URL is read from VITE_API_URL, falling
 * back to the dev-server proxy path (/api) so you don't need a .env file in
 * development if you configure the Vite proxy.
 */

const BASE = import.meta.env.VITE_API_URL || '/api'

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, options)
  if (res.status === 204) return null
  if (!res.ok) {
    const detail = await res.json().then(d => d.detail).catch(() => res.statusText)
    throw new Error(detail || `HTTP ${res.status}`)
  }
  return res.json()
}

function get(path) {
  return request(path)
}

function post(path, body) {
  return request(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

function patch(path, body) {
  return request(path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

function del(path) {
  return request(path, { method: 'DELETE' })
}

// Digests

export const getLatestDigest = (category) => {
  const qs = category ? `?category=${encodeURIComponent(category)}` : ''
  return get(`/digest/latest${qs}`)
}

export const getDigestByDate = (date, category) => {
  const qs = category ? `?category=${encodeURIComponent(category)}` : ''
  return get(`/digest/${date}${qs}`)
}

export const getDigests = (category) => {
  const qs = category ? `?category=${encodeURIComponent(category)}` : ''
  return get(`/digests${qs}`)
}

// Articles

export function getArticles({
  source, type, topic, since, until,
  summarised_since, summarised_until, summarised_only,
  category, transcribed, limit, q,
} = {}) {
  const params = new URLSearchParams()
  if (source) params.set('source', source)
  if (type) params.set('type', type)
  if (topic) params.set('topic', topic)
  if (since) params.set('since', since)
  if (until) params.set('until', until)
  if (summarised_since) params.set('summarised_since', summarised_since)
  if (summarised_until) params.set('summarised_until', summarised_until)
  if (summarised_only) params.set('summarised_only', 'true')
  if (category) params.set('category', category)
  if (transcribed != null) params.set('transcribed', String(transcribed))
  if (limit != null) params.set('limit', String(limit))
  if (q && q.trim()) params.set('q', q.trim())
  const qs = params.toString()
  return get(`/articles${qs ? `?${qs}` : ''}`)
}

/**
 * Fetch all summarised articles belonging to a specific digest date.
 * @param {string} date YYYY-MM-DD
 * @param {string} [category] optional category filter
 */
export const getDigestArticles = (date, category) => {
  const qs = category ? `?category=${encodeURIComponent(category)}` : ''
  return get(`/digest/${date}/articles${qs}`)
}

/**
 * Rolling feed of informative articles (summarised + pending), newest first.
 * @param {string} [since] optional ISO datetime lower bound for pagination
 */
export function getFeed(since) {
  const params = new URLSearchParams()
  if (since) params.set('since', since)
  const qs = params.toString()
  return get(`/feed${qs ? `?${qs}` : ''}`)
}

export const getArticle = (id) => get(`/articles/${id}`)

/** Return the full stored content (transcript / newsletter body) for an article. */
export const getArticleContent = (id) => get(`/articles/${id}/content`)

/** Generate (or return cached) extended AI analysis for an article. */
export const extendArticleSummary = (id) => post(`/articles/${id}/extend`)

/**
 * Trigger on-demand Groq transcription for a single article.
 * Returns the updated article dict plus audio_seconds and word_count.
 * Throws an Error (status 429) if the Groq rate limit is reached.
 * @param {number} id
 */
export const transcribeArticle = (id) => post(`/articles/${id}/transcribe`)

/**
 * Return current Groq usage and remaining budget in minutes.
 * @returns {Promise<{ used_minutes_hour, used_minutes_day, remaining_minutes_hour, remaining_minutes_day, limit_minutes_hour, limit_minutes_day }>}
 */
export const getGroqBudget = () => get('/groq-budget')

/**
 * Return current Gemini API call usage and remaining daily budget.
 * @returns {Promise<{ used_today: number, remaining_today: number, limit_today: number }>}
 */
export const getGeminiBudget = () => get('/gemini-budget')

/**
 * Return recent Groq transcription events.
 * @param {number} [hours=24] look-back window in hours
 * @returns {Promise<Array>}
 */
export const getTranscriptionLog = (hours = 24) => get(`/transcription-log?hours=${hours}`)

/**
 * Synthesise summaries of the given article IDs into a structured page briefing.
 * @param {number[]} articleIds
 * @returns {Promise<{ key_themes: string[], notable_items: string[], market_mood: string, generated_at: string }>}
 */
export const summarisePage = (articleIds) => post('/summarise-page', { article_ids: articleIds })

/**
 * Return the pre-computed feed summary for a given date (defaults to today).
 * Throws a 404 error if no summary exists for that date.
 * @param {string} [date] YYYY-MM-DD
 */
export const getFeedSummary = (date) => {
  const qs = date ? `?date=${encodeURIComponent(date)}` : ''
  return get(`/feed-summary${qs}`)
}

/**
 * Regenerate the feed summary for today and return the result.
 * @returns {Promise<{ key_themes, notable_items, market_mood, generated_at, date }>}
 */
export const refreshFeedSummary = () => post('/feed-summary/refresh')

// Sources

export const getSources = () => get('/sources')

/**
 * Search Taddy for podcasts matching the query term (min 2 characters).
 * @param {string} q
 * @returns {Promise<Array<{ name, description, image_url, rss_url, author_name, taddy_uuid }>>}
 */
export const searchSources = (q) => get(`/sources/search?q=${encodeURIComponent(q)}`)

/**
 * Validate a feed URL and auto-detect its type (podcast/newsletter).
 * @param {string} url
 * @returns {Promise<{ name, description, source_type, url }>}
 */
export const validateSourceUrl = (url) => post('/sources/validate-url', { url })

/**
 * Discover a source by URL (substack) or search term (podcast).
 * @param {{ type: 'substack'|'podcast', query: string }} params
 * @returns {Promise<Array>} list of candidate source objects
 */
export const discoverSource = ({ type, query }) =>
  post('/sources/discover', { type, query })

/**
 * Create a new source.
 * @param {{ name, type, url, default_topics?, description?, taddy_uuid?, transcript_priority?, content_type? }} data
 */
export const createSource = (data) => post('/sources', data)

/**
 * Update fields on an existing source.
 * @param {number} id
 * @param {{ name?, default_topics?, description?, transcript_tier?, active?, taddy_uuid? }} fields
 */
export const updateSource = (id, fields) => patch(`/sources/${id}`, fields)

/**
 * Delete a source permanently.
 * @param {number} id
 */
export const deleteSource = (id) => del(`/sources/${id}`)
