import { useEffect, useMemo, useRef, useState } from 'react'
import { getArticles } from '../api'
import ArticleCard from '../components/ArticleCard'
import FilterBar, { DEFAULT_FILTERS, applyFilters } from '../components/FilterBar'
import PageSummary from '../components/PageSummary'
import TranscriptionLog from '../components/TranscriptionLog'

const PAGE_SIZE = 60

/**
 * MasterFeed — unified rolling feed replacing the old Home + Feed pages.
 *
 * Fetches all articles published in the last ~25 hours (both news and informative).
 * The FilterBar TYPE toggle lets users narrow to news/informative/all.
 * Format (newsletter/podcast) and tag filters are also applied client-side.
 * Keyword search (q) is sent to the backend and debounced by 300 ms.
 */
export default function MasterFeed() {
  const [articles,     setArticles]     = useState([])
  const [filters,      setFilters]      = useState(DEFAULT_FILTERS)
  const [loading,      setLoading]      = useState(true)
  const [error,        setError]        = useState(null)
  const [hasMore,      setHasMore]      = useState(false)
  const [searchInput,  setSearchInput]  = useState('')
  const [searchQuery,  setSearchQuery]  = useState('')
  const searchDebounce = useRef(null)

  // Debounce search input → searchQuery (300 ms)
  useEffect(() => {
    clearTimeout(searchDebounce.current)
    searchDebounce.current = setTimeout(() => {
      setSearchQuery(searchInput.trim())
    }, 300)
    return () => clearTimeout(searchDebounce.current)
  }, [searchInput])

  // Reload articles when searchQuery changes
  useEffect(() => {
    setLoading(true)
    setError(null)
    const yesterday = new Date(Date.now() - 25 * 60 * 60 * 1000)
    const sinceDate = yesterday.toISOString().split('T')[0]

    const params = { since: sinceDate, limit: PAGE_SIZE }
    if (searchQuery) params.q = searchQuery

    getArticles(params)
      .then(rows => {
        setArticles(rows)
        setHasMore(rows.length >= PAGE_SIZE)
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [searchQuery])

  const displayArticles = useMemo(
    () => applyFilters(articles, filters),
    [articles, filters]
  )

  const summarisedIds = displayArticles.filter(a => a.summary).map(a => a.id)

  return (
    <div>
      {/* Page header */}
      <div className="px-4 pt-6 pb-4 border-b border-outline-variant">
        <div className="flex items-baseline justify-between gap-4 flex-wrap">
          <h1 className="font-headline font-black text-7xl md:text-8xl text-on-surface leading-none">
            TL;DR
          </h1>
          {!loading && (
            <span className="font-label text-sm text-on-surface-variant/60">
              {displayArticles.length} items
            </span>
          )}
        </div>
      </div>

      {/* Search box */}
      <div className="px-4 py-2 border-b border-outline-variant bg-surface">
        <div className="relative">
          <span className="absolute left-3 top-1/2 -translate-y-1/2 material-symbols-outlined text-[16px] text-on-surface-variant/50 select-none">
            search
          </span>
          <input
            type="text"
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') {
                clearTimeout(searchDebounce.current)
                setSearchQuery(searchInput.trim())
              }
            }}
            placeholder="Search articles…"
            className="font-mono text-[11px] border border-outline-variant bg-surface-container pl-8 py-1.5 text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-secondary w-full"
            style={{ paddingRight: searchInput ? '2rem' : '0.75rem' }}
          />
          {searchInput && (
            <button
              type="button"
              onClick={() => setSearchInput('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-on-surface-variant/50 hover:text-on-surface transition-colors min-h-[24px] px-1 font-mono text-[14px] leading-none"
            >
              ×
            </button>
          )}
        </div>
      </div>

      {/* FilterBar — sticky below top bar */}
      <FilterBar articles={articles} filters={filters} onFiltersChange={setFilters} />

      <div className="px-4 pt-4">
        {/* TranscriptionLog — collapsed by default */}
        <TranscriptionLog />

        {/* Page summary — auto-loads pre-computed summary; falls back to manual */}
        {summarisedIds.length >= 2 && (
          <PageSummary
            articleIds={summarisedIds}
            label="SUMMARISE FEED"
            summaryDate={new Date().toISOString().split('T')[0]}
          />
        )}

        {/* Loading state */}
        {loading && (
          <div className="flex items-center justify-center h-40">
            <span className="font-mono text-[11px] text-on-surface-variant uppercase tracking-widest animate-pulse">
              LOADING_FEED...
            </span>
          </div>
        )}

        {/* Error state */}
        {error && (
          <div className="border border-error/30 bg-error/5 p-3 font-mono text-[11px] text-error">
            ERROR: {error}
          </div>
        )}

        {/* Empty states */}
        {!loading && !error && articles.length === 0 && (
          <p className="font-mono text-[11px] text-on-surface-variant uppercase tracking-wider py-8">
            NO_ARTICLES — run the ingestion pipeline to fetch content
          </p>
        )}
        {!loading && !error && articles.length > 0 && displayArticles.length === 0 && (
          <p className="font-mono text-[11px] text-on-surface-variant uppercase tracking-wider py-4">
            NO_MATCH — no articles match the current filters
          </p>
        )}

        {/* Article feed — divided by hairline borders */}
        {displayArticles.length > 0 && (
          <ul className="columns-1 md:columns-2 xl:columns-3 gap-4">
            {displayArticles.map(a => (
              <ArticleCard key={a.id} article={a} />
            ))}
          </ul>
        )}

        {/* End of stream marker */}
        {!loading && displayArticles.length > 0 && (
          <p className="font-mono text-[10px] text-on-surface-variant/30 uppercase tracking-wider text-center py-6">
            {hasMore ? '— SHOWING LAST 24H —' : '— END_OF_CURRENT_STREAM —'}
          </p>
        )}
      </div>
    </div>
  )
}
