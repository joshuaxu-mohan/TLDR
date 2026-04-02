import { useEffect, useMemo, useRef, useState } from 'react'
import { getArticles } from '../api'
import ArticleCard from '../components/ArticleCard'
import FilterBar, { DEFAULT_FILTERS, applyFilters } from '../components/FilterBar'

const PAGE_SIZE = 30

/**
 * Archive — flat chronological feed of all summarised articles, Load More pagination.
 *
 * All filtering (type, category, transcript status, tags, sort) is applied client-side
 * on the loaded article list via applyFilters(). The server is only asked for
 * summarised_only=true articles in chronological order.
 * Keyword search (q) triggers a full reload from the server, debounced 300 ms.
 */
export default function Archive() {
  const [rawArticles,  setRawArticles]  = useState([])
  const [filters,      setFilters]      = useState(DEFAULT_FILTERS)
  const [loading,      setLoading]      = useState(true)
  const [loadingMore,  setLoadingMore]  = useState(false)
  const [error,        setError]        = useState(null)
  const [hasMore,      setHasMore]      = useState(true)
  const [searchInput,  setSearchInput]  = useState('')
  const [searchQuery,  setSearchQuery]  = useState('')
  const searchDebounce = useRef(null)

  const oldestDateCursor = useRef(null)

  // Debounce search input → searchQuery (300 ms)
  useEffect(() => {
    clearTimeout(searchDebounce.current)
    searchDebounce.current = setTimeout(() => {
      setSearchQuery(searchInput.trim())
    }, 300)
    return () => clearTimeout(searchDebounce.current)
  }, [searchInput])

  // Reload when searchQuery changes (resets pagination)
  useEffect(() => {
    const params = { summarised_only: true, limit: PAGE_SIZE }
    if (searchQuery) params.q = searchQuery

    setRawArticles([])
    oldestDateCursor.current = null
    setHasMore(true)
    setError(null)
    setLoading(true)

    getArticles(params)
      .then(rows => {
        setRawArticles(rows)
        if (rows.length < PAGE_SIZE) {
          setHasMore(false)
        } else {
          const oldest = rows[rows.length - 1]?.published_at ?? null
          oldestDateCursor.current = oldest ? oldest.slice(0, 10) : null
        }
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [searchQuery])

  async function loadMore() {
    if (loadingMore || !hasMore) return
    setLoadingMore(true)

    const params = { summarised_only: true, limit: PAGE_SIZE + 1 }
    if (oldestDateCursor.current) params.until = oldestDateCursor.current
    if (searchQuery) params.q = searchQuery

    try {
      const rows = await getArticles(params)
      const existingIds = new Set(rawArticles.map(a => a.id))
      const newRows = rows.filter(r => !existingIds.has(r.id))

      if (newRows.length === 0 || rows.length < PAGE_SIZE) {
        setHasMore(false)
      } else {
        const oldest = newRows[newRows.length - 1]?.published_at ?? null
        oldestDateCursor.current = oldest ? oldest.slice(0, 10) : null
      }

      setRawArticles(prev => [...prev, ...newRows])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoadingMore(false)
    }
  }

  const displayArticles = useMemo(
    () => applyFilters(rawArticles, filters),
    [rawArticles, filters]
  )

  return (
    <div>
      {/* Page header */}
      <div className="px-4 pt-6 pb-4 border-b border-outline-variant">
        <div className="flex items-baseline justify-between gap-4 flex-wrap">
          <h1 className="font-headline font-black text-4xl md:text-5xl text-on-surface leading-none">
            Archive
          </h1>
          {!loading && (
            <span className="font-label text-sm text-on-surface-variant/60">
              {rawArticles.length} loaded
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

      {/* FilterBar — sticky below top bar, with sort control */}
      <FilterBar
        articles={rawArticles}
        filters={filters}
        onFiltersChange={setFilters}
        showSort
      />

      <div className="px-4 pt-4">
        {/* Error */}
        {error && (
          <div className="border border-error/30 bg-error/5 p-3 font-mono text-[11px] text-error mb-4">
            ERROR: {error}
          </div>
        )}

        {/* Loading */}
        {loading ? (
          <div className="flex items-center justify-center h-40">
            <span className="font-mono text-[11px] text-on-surface-variant uppercase tracking-widest animate-pulse">
              LOADING_ARCHIVE...
            </span>
          </div>
        ) : displayArticles.length === 0 ? (
          <p className="font-mono text-[11px] text-on-surface-variant uppercase tracking-wider py-6">
            {rawArticles.length === 0
              ? 'NO_ARTICLES — no archived articles found'
              : 'NO_MATCH — no articles match the current filters'}
          </p>
        ) : (
          <>
            {/* Article grid */}
            <ul className="columns-1 md:columns-2 xl:columns-3 gap-4">
              {displayArticles.map(a => (
                <ArticleCard key={a.id} article={a} />
              ))}
            </ul>

            {/* Load more */}
            {hasMore && (
              <div className="mt-6 flex justify-center pb-4">
                <button
                  onClick={loadMore}
                  disabled={loadingMore}
                  className="font-label text-[10px] uppercase tracking-widest text-on-surface-variant border border-outline-variant px-6 py-2 hover:border-secondary hover:text-secondary transition-colors disabled:opacity-50 min-h-[44px]"
                >
                  {loadingMore ? 'LOADING...' : 'LOAD MORE'}
                </button>
              </div>
            )}

            {!hasMore && rawArticles.length > 0 && (
              <p className="font-mono text-[10px] text-on-surface-variant/30 uppercase tracking-wider text-center py-6">
                — END_OF_ARCHIVE —
              </p>
            )}
          </>
        )}
      </div>
    </div>
  )
}
