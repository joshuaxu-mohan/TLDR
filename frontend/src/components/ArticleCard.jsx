/**
 * ArticleCard — editorial card for masonry grid layout.
 *
 * Layout (natural height, no truncation):
 *   Title (Playfair Display bold, hover cyan, line-clamp-2)
 *   source name (colour-coded: orange=newsletter, green=podcast) · relative time (DM Sans)
 *   Full summary text (DM Sans body, no truncation)
 *   topic tags (#TAG format, DM Sans, muted)
 *   action buttons (TRANSCRIBE, DEEP DIVE, COPY TRANSCRIPT)
 *
 * Source type colours:
 *   podcast   → #3ECF6E (green)
 *   substack  → #E8813B (orange)
 *
 * Cards use break-inside-avoid for CSS columns masonry layout.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import { extendArticleSummary, getArticleContent, transcribeArticle } from '../api'

/**
 * ReactMarkdown component map — editorial prose style.
 */
const MD = {
  h1: ({ children }) => (
    <h2 className="font-headline font-bold text-sm uppercase tracking-widest text-on-surface mt-4 mb-1.5">
      {children}
    </h2>
  ),
  h2: ({ children }) => (
    <h2 className="font-headline font-bold text-sm uppercase tracking-widest text-on-surface mt-4 mb-1.5">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="font-label font-bold text-[11px] uppercase tracking-widest text-secondary mt-3 mb-1">
      {children}
    </h3>
  ),
  h4: ({ children }) => (
    <h4 className="font-label font-bold text-[10px] uppercase tracking-widest text-secondary mt-2 mb-0.5">
      {children}
    </h4>
  ),
  p: ({ children }) => (
    <p className="font-body text-sm text-on-surface-variant leading-relaxed mb-2">
      {children}
    </p>
  ),
  strong: ({ children }) => (
    <strong className="text-on-surface font-semibold">{children}</strong>
  ),
  em: ({ children }) => (
    <em className="text-on-surface-variant italic">{children}</em>
  ),
  ul: ({ children }) => <ul className="ml-2 space-y-0.5 mb-2">{children}</ul>,
  ol: ({ children }) => <ol className="ml-2 space-y-0.5 mb-2">{children}</ol>,
  li: ({ children }) => (
    <li className="font-body text-sm text-on-surface-variant leading-relaxed flex gap-2">
      <span className="text-secondary flex-shrink-0 select-none mt-0.5">›</span>
      <span>{children}</span>
    </li>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-secondary pl-3 my-2 text-on-surface-variant/80">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="border-outline-variant my-3" />,
  code: ({ children }) => (
    <code className="font-mono text-[11px] bg-surface-container px-1 py-0.5 text-secondary">
      {children}
    </code>
  ),
}

function relativeTime(isoString) {
  if (!isoString) return null
  const diff = (Date.now() - new Date(isoString).getTime()) / 1000
  if (diff < 60)    return 'JUST NOW'
  if (diff < 3600)  return `${Math.floor(diff / 60)}M AGO`
  if (diff < 86400) return `${Math.floor(diff / 3600)}H AGO`
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}D AGO`
  return new Date(isoString)
    .toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
    .toUpperCase()
}

function sourceNameColor(sourceType) {
  if (sourceType === 'podcast')  return '#3ECF6E'
  if (sourceType === 'substack') return '#E8813B'
  return '#00E5FF' // fallback cyan
}

export default function ArticleCard({ article }) {
  const [copyState,         setCopyState]         = useState('idle')
  const [deepDiveCopyState, setDeepDiveCopyState] = useState('idle')
  const [extended,          setExtended]          = useState(null)
  const [extendLoading,     setExtendLoading]     = useState(false)
  const [extendError,       setExtendError]       = useState(null)
  const [localTranscribed,  setLocalTranscribed]  = useState(false)
  const [transcribeState,   setTranscribeState]   = useState('idle')
  const [transcribeError,   setTranscribeError]   = useState(null)

  const rawTags = typeof article.topic_tags === 'string' ? article.topic_tags : ''
  const tags    = rawTags.split(',').map(t => t.trim()).filter(Boolean)

  const effectiveHasContent = localTranscribed || article.has_content
  const canTranscribe = (
    article.audio_url
    && !effectiveHasContent
    && transcribeState !== 'done'
    && article.transcript_priority != null
    && article.transcript_priority !== 'skip'
  )

  const srcColor = sourceNameColor(article.source_type)

  async function handleCopy(e) {
    e.preventDefault()
    if (copyState !== 'idle') return
    setCopyState('copying')
    try {
      const { content } = await getArticleContent(article.id)
      await navigator.clipboard.writeText(content)
      setCopyState('copied')
      setTimeout(() => setCopyState('idle'), 2000)
    } catch {
      setCopyState('idle')
    }
  }

  async function handleTranscribe(e) {
    e.preventDefault()
    if (transcribeState === 'loading') return
    setTranscribeState('loading')
    setTranscribeError(null)
    try {
      await transcribeArticle(article.id)
      setLocalTranscribed(true)
      setTranscribeState('done')
      window.dispatchEvent(new CustomEvent('groq-budget-refresh'))
    } catch (err) {
      const msg = err.message?.includes('429')
        ? 'Rate limit reached — try again later'
        : 'Transcription failed — try again'
      setTranscribeError(msg)
      setTranscribeState('error')
    }
  }

  async function handleDeepDive(e) {
    e.preventDefault()
    if (extendLoading) return
    setExtendLoading(true)
    setExtendError(null)
    try {
      if (article.extended_summary) {
        setExtended(article.extended_summary)
      } else {
        const { extended_summary } = await extendArticleSummary(article.id)
        setExtended(extended_summary)
        window.dispatchEvent(new CustomEvent('gemini-budget-refresh'))
      }
    } catch (err) {
      setExtendError(err.message)
    } finally {
      setExtendLoading(false)
    }
  }

  return (
    <li className="group border border-outline-variant bg-[#181818] hover:border-secondary transition-colors relative flex flex-col break-inside-avoid mb-4">

      {/* Full-card overlay link — sits behind all content */}
      <Link
        to={`/article/${article.id}`}
        className="absolute inset-0 z-0"
        aria-hidden="true"
        tabIndex={-1}
      />

      {/* Card body */}
      <div className="relative z-10 p-4 flex flex-col gap-2">

        {/* Title */}
        <Link to={`/article/${article.id}`} className="block">
          <h3 className="font-headline font-bold text-3xl text-on-surface leading-snug tracking-tight line-clamp-2 group-hover:text-secondary transition-colors">
            {article.title}
          </h3>
        </Link>

        {/* Source · time */}
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className="font-label text-[10px] font-bold uppercase tracking-wider"
            style={{ color: srcColor }}
          >
            {article.source_name}
          </span>
          <span className="text-outline-variant select-none">·</span>
          <span className="font-label text-[10px] text-on-surface-variant uppercase tracking-wider">
            {relativeTime(article.published_at)}
          </span>
        </div>

        {/* Full summary, or podcast description stub when no summary yet */}
        {article.summary ? (
          <div>
            <span
              className="font-label text-[9px] uppercase tracking-widest"
              style={{ color: article.is_transcribed ? '#00E5FF' : '#6B7280' }}
            >
              {article.is_transcribed ? 'TRANSCRIPT SUMMARY' : 'DESCRIPTION SUMMARY'}
            </span>
            <p className="font-body text-sm text-on-surface-variant leading-relaxed mt-0.5">
              {article.summary}
            </p>
          </div>
        ) : article.content ? (
          <div>
            <p className="font-label text-[9px] uppercase tracking-widest text-on-surface-variant/40 mb-0.5">
              SHOW DESCRIPTION
            </p>
            <p className="font-body text-sm text-on-surface-variant/70 leading-relaxed italic">
              {article.content}
            </p>
          </div>
        ) : null}

        {/* Topic tags */}
        {tags.length > 0 && (
          <div className="flex flex-wrap gap-3 mt-0.5">
            {tags.map(t => (
              <span key={t} className="font-label text-[10px] text-on-surface-variant/50 uppercase tracking-wider">
                #{t}
              </span>
            ))}
          </div>
        )}

        {/* Transcribe button for on-demand episodes */}
        {canTranscribe && (
          <div>
            <button
              onClick={handleTranscribe}
              disabled={transcribeState === 'loading'}
              className="font-label text-[10px] uppercase tracking-widest text-secondary border border-secondary px-3 py-1 hover:bg-secondary hover:text-on-secondary transition-colors disabled:opacity-50 min-h-[36px]"
            >
              {transcribeState === 'loading' ? 'TRANSCRIBING...' : 'TRANSCRIBE'}
            </button>
            {transcribeError && (
              <p className="font-label text-[10px] text-error mt-1">{transcribeError}</p>
            )}
          </div>
        )}

        {/* Action row — shown when article has full content and deep dive is not open */}
        {effectiveHasContent && !extended && (
          <div className="flex items-center gap-4">
            <button
              onClick={handleDeepDive}
              disabled={extendLoading}
              className="font-label text-[10px] uppercase tracking-widest text-on-surface-variant hover:text-on-surface transition-colors disabled:opacity-50 min-h-[36px] flex items-center"
            >
              {extendLoading ? 'GENERATING...' : 'DEEP DIVE'}
            </button>
            <button
              onClick={handleCopy}
              disabled={copyState === 'copying'}
              className="font-label text-[10px] uppercase tracking-widest text-on-surface-variant hover:text-on-surface transition-colors disabled:opacity-50 min-h-[36px] flex items-center"
            >
              {copyState === 'copied' ? 'COPIED' : 'COPY TRANSCRIPT'}
            </button>
          </div>
        )}

        {extendError && (
          <p className="font-label text-[10px] text-error">{extendError}</p>
        )}

      </div>

      {/* Deep dive panel — full width below card body */}
      {extended && (
        <div className="relative z-10 border-t border-outline-variant bg-surface-container-low p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="font-label text-[10px] text-secondary uppercase tracking-wider">
              DEEP DIVE
            </p>
            <button
              onClick={async e => {
                e.preventDefault()
                if (deepDiveCopyState !== 'idle') return
                setDeepDiveCopyState('copied')
                await navigator.clipboard.writeText(extended)
                setTimeout(() => setDeepDiveCopyState('idle'), 1500)
              }}
              title="Copy deep dive"
              className="material-symbols-outlined text-[16px] text-on-surface-variant/40 hover:text-on-surface-variant transition-colors select-none"
            >
              {deepDiveCopyState === 'copied' ? 'check' : 'content_copy'}
            </button>
          </div>
          <div className="prose-deep-dive">
            <ReactMarkdown components={MD}>{extended}</ReactMarkdown>
          </div>
          <button
            onClick={e => { e.preventDefault(); setExtended(null) }}
            className="mt-2 font-label text-[10px] uppercase tracking-widest text-on-surface-variant/50 hover:text-on-surface transition-colors min-h-[36px] flex items-center"
          >
            DISMISS
          </button>
        </div>
      )}

    </li>
  )
}
