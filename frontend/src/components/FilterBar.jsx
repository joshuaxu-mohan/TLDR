import { useMemo } from 'react'

/**
 * Shared filter/sort bar. Horizontal row of text toggles, sticky below the top bar.
 * Active toggle: green text + underline. Inactive: muted, hover to white.
 * Mobile: horizontal scroll (overflow-x-auto, hidden scrollbar).
 *
 * Props:
 *   articles          — full article list (to derive available tags and detect podcast presence)
 *   filters           — current filter state (see DEFAULT_FILTERS)
 *   onFiltersChange   — callback with new filter state
 *   showSort          — show NEWEST/OLDEST sort toggles (Archive page only)
 *
 * Filter state shape:
 *   {
 *     types:       string[]                                     — 'podcast' | 'substack' (empty = all)
 *     categories:  string[]                                     — 'news' | 'informative' (empty = all)
 *     transcribed: 'transcribed'|'not_transcribed'|null
 *     tags:        string[]                                     — topic tag values (empty = all)
 *     sort:        'newest' | 'oldest'
 *   }
 *
 * Transcript status semantics:
 *   transcribed     — article has a full transcript (has_content or is_transcribed)
 *   not_transcribed — podcast article without a full transcript (covers both queued and on-demand)
 *   null            — all articles
 */

export const DEFAULT_FILTERS = {
  types: [],
  categories: [],
  transcribed: null,
  tags: [],
  sort: 'newest',
}

/**
 * Apply filters and sort to an in-memory article list.
 */
export function applyFilters(articles, filters) {
  let result = articles

  if (filters.types.length > 0) {
    result = result.filter(a => filters.types.includes(a.source_type))
  }
  if (filters.categories.length > 0) {
    result = result.filter(a => filters.categories.includes(a.content_category))
  }

  if (filters.transcribed === 'transcribed') {
    result = result.filter(a => a.is_transcribed || a.has_content)
  } else if (filters.transcribed === 'not_transcribed') {
    result = result.filter(a => a.audio_url && !a.has_content)
  }
  // null = all, no filter applied

  if (filters.tags.length > 0) {
    result = result.filter(a => {
      const articleTags = a.topic_tags
        ? a.topic_tags.split(',').map(t => t.trim())
        : []
      return filters.tags.some(t => articleTags.includes(t))
    })
  }

  if (filters.sort === 'oldest') {
    result = [...result].sort((a, b) =>
      (a.published_at ?? '').localeCompare(b.published_at ?? '')
    )
  } else {
    result = [...result].sort((a, b) =>
      (b.published_at ?? '').localeCompare(a.published_at ?? '')
    )
  }

  return result
}

function Toggle({ label, active, onClick, activeColor = null }) {
  return (
    <button
      onClick={onClick}
      className={`whitespace-nowrap font-label text-[10px] uppercase tracking-widest transition-colors px-1 py-1 min-h-[36px] ${
        active
          ? 'underline underline-offset-4'
          : 'text-on-surface-variant hover:text-on-surface'
      }`}
      style={active ? { color: activeColor ?? '#00E5FF' } : {}}
    >
      {label}
    </button>
  )
}

function GroupLabel({ label }) {
  return (
    <span className="font-label text-[10px] uppercase tracking-widest text-on-surface-variant/50 font-bold whitespace-nowrap flex items-center">
      {label}
    </span>
  )
}

function Sep() {
  return <span className="text-outline-variant select-none px-1">|</span>
}

export default function FilterBar({ articles, filters, onFiltersChange, showSort = false }) {
  const allTags = useMemo(() => {
    const tagSet = new Set()
    for (const a of articles) {
      if (a.topic_tags) {
        a.topic_tags.split(',').forEach(t => {
          const trimmed = t.trim()
          if (trimmed) tagSet.add(trimmed)
        })
      }
    }
    return [...tagSet].sort()
  }, [articles])

  const hasPodcasts = articles.some(a => a.source_type === 'podcast')

  function toggleMulti(field, value) {
    const current = filters[field]
    const next = current.includes(value)
      ? current.filter(v => v !== value)
      : [...current, value]
    onFiltersChange({ ...filters, [field]: next })
  }

  function setTranscribed(value) {
    onFiltersChange({
      ...filters,
      transcribed: filters.transcribed === value ? null : value,
    })
  }

  return (
    <div className="sticky top-12 z-30 border-b border-outline-variant" style={{ backgroundColor: '#131313' }}>
      <div className="overflow-x-auto scrollbar-hide">
        <div className="flex items-center gap-2 px-4 py-1.5 min-w-max">
          {/* TYPE group */}
          <GroupLabel label="TYPE:" />
          <Toggle
            label="ALL"
            active={filters.categories.length === 0}
            onClick={() => onFiltersChange({ ...filters, categories: [] })}
          />
          <Toggle
            label="NEWS"
            active={filters.categories.includes('news')}
            onClick={() => toggleMulti('categories', 'news')}
          />
          <Toggle
            label="INFORMATIVE"
            active={filters.categories.includes('informative')}
            onClick={() => toggleMulti('categories', 'informative')}
          />

          <Sep />

          {/* FORMAT group */}
          <GroupLabel label="FORMAT:" />
          <Toggle
            label="ALL"
            active={filters.types.length === 0}
            onClick={() => onFiltersChange({ ...filters, types: [] })}
          />
          <Toggle
            label="NEWSLETTER"
            active={filters.types.includes('substack')}
            onClick={() => toggleMulti('types', 'substack')}
            activeColor="#E8813B"
          />
          <Toggle
            label="PODCAST"
            active={filters.types.includes('podcast')}
            onClick={() => toggleMulti('types', 'podcast')}
            activeColor="#3ECF6E"
          />

          {/* STATUS group — only show when podcast articles are present */}
          {hasPodcasts && (
            <>
              <Sep />
              <GroupLabel label="STATUS:" />
              <Toggle
                label="ALL"
                active={filters.transcribed === null}
                onClick={() => onFiltersChange({ ...filters, transcribed: null })}
              />
              <Toggle
                label="TRANSCRIBED"
                active={filters.transcribed === 'transcribed'}
                onClick={() => setTranscribed('transcribed')}
              />
              <Toggle
                label="NOT TRANSCRIBED"
                active={filters.transcribed === 'not_transcribed'}
                onClick={() => setTranscribed('not_transcribed')}
              />
            </>
          )}

          {/* TAGS group */}
          {allTags.length > 0 && (
            <>
              <Sep />
              <GroupLabel label="TAGS:" />
              {allTags.map(tag => (
                <Toggle
                  key={tag}
                  label={tag.toUpperCase()}
                  active={filters.tags.includes(tag)}
                  onClick={() => toggleMulti('tags', tag)}
                />
              ))}
            </>
          )}

          {/* SORT — Archive page only */}
          {showSort && (
            <>
              <Sep />
              <GroupLabel label="SORT:" />
              <Toggle
                label="NEWEST"
                active={filters.sort === 'newest'}
                onClick={() => onFiltersChange({ ...filters, sort: 'newest' })}
              />
              <Toggle
                label="OLDEST"
                active={filters.sort === 'oldest'}
                onClick={() => onFiltersChange({ ...filters, sort: 'oldest' })}
              />
            </>
          )}
        </div>
      </div>
    </div>
  )
}
