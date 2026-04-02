import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import { summarisePage, getFeedSummary, refreshFeedSummary } from '../api'

/**
 * Module-level session state — persists across React Router navigations
 * within a single browser session, resets on full page reload (F5).
 * Default: expanded.
 */
let _sessionExpanded = true

/**
 * ReactMarkdown component map — editorial publication prose.
 * h2 = section headings, h3 = subheadings (DAILY BRIEFING, KEY THEMES, etc.)
 * Body text at text-lg for comfortable reading.
 */
const MD = {
  h1: ({ children }) => (
    <h2 className="font-headline font-bold text-2xl text-on-surface mt-8 mb-3">
      {children}
    </h2>
  ),
  h2: ({ children }) => (
    <h2 className="font-headline font-bold text-2xl text-on-surface mt-8 mb-3">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="font-headline font-bold text-xl text-secondary mt-6 mb-2">
      {children}
    </h3>
  ),
  h4: ({ children }) => (
    <h4 className="font-headline font-bold text-lg text-secondary mt-4 mb-1.5">
      {children}
    </h4>
  ),
  p: ({ children }) => (
    <p className="font-body text-lg text-on-surface-variant leading-relaxed mb-4">
      {children}
    </p>
  ),
  strong: ({ children }) => (
    <strong className="text-on-surface font-semibold">{children}</strong>
  ),
  em: ({ children }) => (
    <em className="text-on-surface-variant italic">{children}</em>
  ),
  ul: ({ children }) => <ul className="ml-2 space-y-1 mb-4">{children}</ul>,
  ol: ({ children }) => <ol className="ml-2 space-y-1 mb-4">{children}</ol>,
  li: ({ children }) => (
    <li className="font-body text-lg text-on-surface-variant leading-relaxed flex gap-2">
      <span className="text-secondary flex-shrink-0 select-none mt-1">›</span>
      <span>{children}</span>
    </li>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-secondary pl-4 my-4 text-on-surface-variant/80">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="border-outline-variant my-6" />,
  a: ({ href, children }) => {
    if (href && href.startsWith('/article/')) {
      // Parse optional ?st= source-type param added by the backend link injector.
      // Use it to colour the link by source type; strip the param before navigating.
      const qIdx = href.indexOf('?')
      const path = qIdx >= 0 ? href.slice(0, qIdx) : href
      const st   = qIdx >= 0 ? new URLSearchParams(href.slice(qIdx + 1)).get('st') : null
      const colour =
        st === 'newsletter' ? '#E8813B' :
        st === 'podcast'    ? '#3ECF6E' :
        null  // null → cyan default
      return (
        <Link
          to={path}
          className="hover:text-on-surface transition-colors underline"
          style={colour
            ? { color: colour, textDecorationColor: colour + '66' }
            : { color: '#00E5FF', textDecorationColor: '#00E5FF66' }
          }
        >
          {children}
        </Link>
      )
    }
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-secondary hover:text-on-surface transition-colors underline decoration-secondary/40"
      >
        {children}
      </a>
    )
  },
}

/**
 * Page-level summary button + two-segment feed summary card, editorial style.
 */
export default function PageSummary({ articleIds, label, summaryDate }) {
  const [state,    setState]    = useState('idle')
  const [summary,  setSummary]  = useState(null)
  const [errorMsg, setErrorMsg] = useState(null)
  const [expanded, setExpanded] = useState(() => _sessionExpanded)

  function toggleExpanded() {
    const next = !expanded
    _sessionExpanded = next
    setExpanded(next)
  }

  useEffect(() => {
    if (!summaryDate) return
    getFeedSummary(summaryDate)
      .then(result => {
        setSummary(result)
        setState('done')
      })
      .catch(() => {})
  }, [summaryDate])

  async function generate() {
    setState('loading')
    setErrorMsg(null)
    try {
      const result = summaryDate
        ? await refreshFeedSummary()
        : await summarisePage(articleIds)
      setSummary(result)
      setState('done')
      _sessionExpanded = true
      setExpanded(true)
      window.dispatchEvent(new CustomEvent('gemini-budget-refresh'))
    } catch {
      setErrorMsg('Summary generation failed — try again')
      setState('error')
    }
  }

  if (state === 'done' && summary) {
    const hasNews        = Boolean(summary.news_summary)
    const hasInformative = Boolean(summary.informative_summary)

    if (!hasNews && !hasInformative) {
      return (
        <div className="border border-outline-variant bg-surface-container-low mb-6">
          <button
            onClick={toggleExpanded}
            className="w-full flex items-center justify-between px-5 py-4 hover:bg-surface-container transition-colors"
          >
            <span className="font-headline font-bold text-lg text-secondary">
              Feed Summary
            </span>
            <span className="font-label text-sm text-on-surface-variant select-none">
              {expanded ? '▴' : '▾'}
            </span>
          </button>
          {expanded && (
            <div className="px-5 pb-5">
              <p className="font-label text-sm text-on-surface-variant/50 uppercase tracking-wider">
                Summary unavailable — regenerate via the button below
              </p>
              <button
                onClick={generate}
                className="mt-3 font-label text-[10px] uppercase tracking-widest text-on-surface-variant border border-outline-variant px-4 py-2 hover:border-secondary hover:text-secondary transition-colors min-h-[36px] flex items-center gap-2"
              >
                [ REGENERATE ]
              </button>
            </div>
          )}
        </div>
      )
    }

    return (
      <div className="border border-outline-variant bg-surface-container-low mb-6">

        {/* Collapsed state: show label button */}
        {!expanded && (
          <button
            onClick={toggleExpanded}
            className="w-full flex items-center justify-between px-5 py-4 hover:bg-surface-container transition-colors"
          >
            <span className="font-headline font-bold text-lg text-secondary">
              Feed Summary
            </span>
            <span className="font-label text-sm text-on-surface-variant select-none">▾</span>
          </button>
        )}

        {/* Expanded state: entire content area is the collapse target */}
        {expanded && (
          <div
            onClick={toggleExpanded}
            className="px-5 pb-6 pt-3 cursor-pointer hover:bg-surface-container transition-colors"
          >
            <div className="flex flex-col md:flex-row">

              {/* NEWS SUMMARY column — grows with content */}
              {hasNews && (
                <div className={`flex-1 min-w-0 py-2 ${hasInformative ? 'md:pr-8' : ''}`}>
                  <p className="font-headline font-bold text-4xl text-secondary mb-4">
                    News Summary
                  </p>
                  <ReactMarkdown components={MD}>{summary.news_summary}</ReactMarkdown>
                </div>
              )}

              {/* Vertical divider (desktop) / horizontal divider (mobile) */}
              {hasNews && hasInformative && (
                <>
                  <div className="hidden md:block w-px bg-outline-variant flex-shrink-0" />
                  <hr className="md:hidden border-outline-variant my-6" />
                </>
              )}

              {/* INFORMATIVE HIGHLIGHTS column — grows with content */}
              {hasInformative && (
                <div className={`flex-1 min-w-0 py-2 ${hasNews ? 'md:pl-8' : ''}`}>
                  <p className="font-headline font-bold text-4xl text-secondary mb-4">
                    Informative Highlights
                  </p>
                  <ReactMarkdown components={MD}>{summary.informative_summary}</ReactMarkdown>
                </div>
              )}

            </div>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="mb-6">
      <button
        onClick={generate}
        disabled={state === 'loading'}
        className="font-label text-[11px] uppercase tracking-widest text-on-surface-variant border border-outline-variant px-4 py-2 hover:border-secondary hover:text-secondary transition-colors disabled:opacity-60 min-h-[36px] flex items-center gap-2"
      >
        {state === 'loading' ? (
          <>
            <span className="inline-block w-3 h-3 border border-secondary border-t-transparent rounded-full animate-spin" />
            GENERATING...
          </>
        ) : (
          `[ ${label} ]`
        )}
      </button>
      {state === 'error' && errorMsg && (
        <p className="mt-1.5 font-label text-[10px] text-error">{errorMsg}</p>
      )}
    </div>
  )
}
