import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import { extendArticleSummary, getArticle, transcribeArticle } from '../api'

const AUDIO_EXTENSIONS = ['.mp3', '.m4a', '.wav', '.ogg', '.aac', '.flac']

function isAudioUrl(url) {
  if (!url) return false
  const lower = url.split('?')[0].toLowerCase()
  return AUDIO_EXTENSIONS.some(ext => lower.endsWith(ext))
}

/**
 * ReactMarkdown component map — terminal aesthetic prose.
 * Applied to the deep-dive extended summary content.
 */
const MD = {
  h1: ({ children }) => (
    <h2 className="font-headline font-bold text-sm uppercase tracking-widest text-on-surface mt-5 mb-2">
      {children}
    </h2>
  ),
  h2: ({ children }) => (
    <h2 className="font-headline font-bold text-sm uppercase tracking-widest text-on-surface mt-5 mb-2">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="font-label font-bold text-[11px] uppercase tracking-widest text-secondary mt-4 mb-1">
      {children}
    </h3>
  ),
  h4: ({ children }) => (
    <h4 className="font-label font-bold text-[10px] uppercase tracking-widest text-secondary mt-3 mb-1">
      {children}
    </h4>
  ),
  p: ({ children }) => (
    <p className="font-body text-sm text-on-surface-variant leading-relaxed mb-3">
      {children}
    </p>
  ),
  strong: ({ children }) => (
    <strong className="text-on-surface font-semibold">{children}</strong>
  ),
  em: ({ children }) => (
    <em className="text-on-surface-variant italic">{children}</em>
  ),
  ul: ({ children }) => (
    <ul className="ml-2 space-y-1 mb-3">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="ml-2 space-y-1 mb-3">{children}</ol>
  ),
  li: ({ children }) => (
    <li className="font-body text-sm text-on-surface-variant leading-relaxed flex gap-2">
      <span className="text-secondary flex-shrink-0 select-none mt-0.5">›</span>
      <span>{children}</span>
    </li>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-secondary pl-3 my-3 text-on-surface-variant/80">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="border-outline-variant my-4" />,
  code: ({ children }) => (
    <code className="font-mono text-[11px] bg-surface-container px-1 py-0.5 text-secondary">
      {children}
    </code>
  ),
}

function SourceLink({ article }) {
  const cls =
    'inline-flex items-center gap-1.5 font-label text-[10px] uppercase tracking-widest text-on-surface-variant border border-outline-variant px-3 py-2 hover:border-secondary hover:text-secondary transition-colors min-h-[36px]'

  if (article.source_type !== 'podcast') {
    if (!article.url) return null
    return (
      <a href={article.url} target="_blank" rel="noopener noreferrer" className={cls}>
        VISIT SOURCE
      </a>
    )
  }

  const hasWebUrl = article.url && !isAudioUrl(article.url)
  if (hasWebUrl) {
    return (
      <a href={article.url} target="_blank" rel="noopener noreferrer" className={cls}>
        VISIT SOURCE
      </a>
    )
  }

  if (article.source_spotify_url) {
    return (
      <a href={article.source_spotify_url} target="_blank" rel="noopener noreferrer" className={cls}>
        LISTEN ON SPOTIFY
      </a>
    )
  }

  if (article.audio_url) {
    return (
      <a href={article.audio_url} target="_blank" rel="noopener noreferrer" className={cls}>
        LISTEN
      </a>
    )
  }

  return null
}

function SectionHeading({ label, color }) {
  return (
    <div className="flex items-center gap-3 mb-2">
      <span
        className="font-headline font-bold text-sm uppercase tracking-widest whitespace-nowrap"
        style={{ color: color || undefined }}
      >
        {label}
      </span>
      <span className="flex-1 h-px bg-outline-variant" />
    </div>
  )
}

export default function ArticleDetail() {
  const { id } = useParams()
  const [article,         setArticle]         = useState(null)
  const [loading,         setLoading]         = useState(true)
  const [error,           setError]           = useState(null)
  const [showTranscript,  setShowTranscript]  = useState(false)
  const [extended,        setExtended]        = useState(null)
  const [extendLoading,   setExtendLoading]   = useState(false)
  const [extendError,     setExtendError]     = useState(null)
  const [transcribeState,   setTranscribeState]   = useState('idle')
  const [transcribeError,   setTranscribeError]   = useState(null)
  const [copyState,         setCopyState]         = useState('idle')
  const [deepDiveCopyState, setDeepDiveCopyState] = useState('idle')

  useEffect(() => {
    getArticle(id)
      .then(setArticle)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [id])

  async function handleTranscribe() {
    if (transcribeState === 'loading') return
    setTranscribeState('loading')
    setTranscribeError(null)
    try {
      const updated = await transcribeArticle(article.id)
      setArticle(prev => ({ ...prev, ...updated, has_content: true, is_transcribed: true }))
      setTranscribeState('done')

      // Notify GroqBudget components to refresh
      window.dispatchEvent(new CustomEvent('groq-budget-refresh'))

      // Auto-trigger deep dive after transcription
      setExtendLoading(true)
      try {
        const { extended_summary } = await extendArticleSummary(article.id)
        setExtended(extended_summary)
        window.dispatchEvent(new CustomEvent('gemini-budget-refresh'))
      } catch {
        // Non-critical — user can still trigger manually
      } finally {
        setExtendLoading(false)
      }
    } catch (e) {
      const msg = e.message?.includes('429')
        ? 'Rate limit reached — try again later'
        : `Transcription failed — ${e.message}`
      setTranscribeError(msg)
      setTranscribeState('error')
    }
  }

  async function handleDeepDive() {
    if (extendLoading) return
    setExtendLoading(true)
    setExtendError(null)
    try {
      if (article.extended_summary) {
        setExtended(article.extended_summary)
      } else {
        const { extended_summary } = await extendArticleSummary(id)
        setExtended(extended_summary)
        window.dispatchEvent(new CustomEvent('gemini-budget-refresh'))
      }
    } catch (e) {
      setExtendError('Deep dive failed — ' + e.message)
    } finally {
      setExtendLoading(false)
    }
  }

  async function handleCopyTranscript() {
    if (copyState !== 'idle' || !article?.content) return
    setCopyState('copying')
    try {
      await navigator.clipboard.writeText(article.content)
      setCopyState('copied')
      setTimeout(() => setCopyState('idle'), 2000)
    } catch {
      setCopyState('idle')
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-40">
        <span className="font-mono text-[11px] text-on-surface-variant uppercase tracking-widest animate-pulse">
          LOADING_ARTICLE...
        </span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="m-4 border border-error/30 bg-error/5 p-4 font-mono text-[11px] text-error">
        ERROR: {error}
      </div>
    )
  }

  const tags = article.topic_tags
    ? article.topic_tags.split(',').map(t => t.trim()).filter(Boolean)
    : []

  const publishedDate = article.published_at
    ? new Date(article.published_at)
        .toLocaleDateString('en-GB', {
          day: '2-digit', month: '2-digit', year: 'numeric',
          hour: '2-digit', minute: '2-digit',
        })
        .replace(',', ' //')
    : null

  const hasContent = article.has_content || transcribeState === 'done'

  const srcColor = article.source_type === 'podcast'  ? '#3ECF6E'
                 : article.source_type === 'substack' ? '#E8813B'
                 : '#00E5FF'

  const showTranscribeButton = (
    article.transcript_priority === 'on_demand'
    && article.audio_url
    && !article.has_content
    && transcribeState !== 'done'
  )

  return (
    <div className="max-w-[720px] mx-auto px-4 pt-3 pb-20 md:pb-6">
      {/* Back link */}
      <Link
        to="/"
        className="inline-flex items-center gap-1 font-label text-[10px] uppercase tracking-widest text-on-surface-variant hover:text-secondary transition-colors mb-2 min-h-[44px]"
      >
        ← BACK
      </Link>

      {/* Source name badge */}
      <div className="mb-1">
        <span
          className="inline-block font-label text-[10px] uppercase tracking-widest px-2 py-1 border"
          style={{ color: srcColor, borderColor: srcColor + '66' }}
        >
          {article.source_name}
        </span>
      </div>

      {/* Title */}
      <h1 className="font-headline font-black text-4xl md:text-6xl uppercase tracking-tighter text-on-surface leading-tight break-words mb-2">
        {article.title}
      </h1>

      {/* Metadata row */}
      <div className="border-t border-b border-outline-variant py-2 mb-3 flex flex-wrap gap-5">
        <div>
          <p className="font-label text-[9px] uppercase tracking-widest text-on-surface-variant/50 mb-0.5">SOURCE</p>
          <p className="font-label text-[11px] uppercase tracking-wider" style={{ color: srcColor }}>{article.source_name}</p>
        </div>
        {publishedDate && (
          <div>
            <p className="font-label text-[9px] uppercase tracking-widest text-on-surface-variant/50 mb-0.5">PUBLISHED AT</p>
            <p className="font-mono text-[11px] text-on-surface uppercase tracking-wider">{publishedDate}</p>
          </div>
        )}
        {article.source_type && (
          <div>
            <p className="font-label text-[9px] uppercase tracking-widest text-on-surface-variant/50 mb-0.5">FORMAT</p>
            <p className="font-mono text-[11px] text-on-surface uppercase tracking-wider">
              {article.source_type === 'substack' ? 'NEWSLETTER' : 'PODCAST'}
            </p>
          </div>
        )}
        {tags.length > 0 && (
          <div>
            <p className="font-label text-[9px] uppercase tracking-widest text-on-surface-variant/50 mb-0.5">TOPICS</p>
            <div className="flex flex-wrap gap-2">
              {tags.map(t => (
                <span key={t} className="font-mono text-[10px] text-on-surface-variant uppercase tracking-wider">
                  #{t}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Transcribe button — on-demand episodes without a transcript */}
      {showTranscribeButton && (
        <div className="mb-3">
          <button
            onClick={handleTranscribe}
            disabled={transcribeState === 'loading'}
            className="font-label text-[10px] uppercase tracking-widest text-on-secondary bg-secondary border border-secondary px-4 py-2 hover:opacity-90 disabled:opacity-50 transition-opacity min-h-[44px] flex items-center gap-2"
          >
            {transcribeState === 'loading' ? (
              <>
                <span className="inline-block w-3 h-3 border border-on-secondary border-t-transparent rounded-full animate-spin" />
                TRANSCRIBING...
              </>
            ) : (
              'TRANSCRIBE EPISODE'
            )}
          </button>
          {transcribeError && (
            <p className="mt-1 font-mono text-[10px] text-error">{transcribeError}</p>
          )}
        </div>
      )}

      {/* Summary — label reflects whether it came from a transcript or description */}
      {article.summary && (
        <div className="mb-4">
          <SectionHeading
            label={hasContent ? 'TRANSCRIPT SUMMARY' : 'DESCRIPTION SUMMARY'}
            color={hasContent ? '#00E5FF' : '#FFFFFF'}
          />
          <p className="font-body text-xl font-light leading-relaxed text-on-surface">
            {article.summary}
          </p>
        </div>
      )}

      {/* Deep dive — auto-generated after transcription or manually triggered */}
      {(hasContent || extendLoading) && (
        <div className="mb-4">
          {extendLoading && !extended ? (
            <div className="border border-outline-variant bg-surface-container-low p-4">
              <p className="font-mono text-[11px] text-secondary uppercase tracking-widest animate-pulse">
                GENERATING ANALYSIS...
              </p>
            </div>
          ) : extended ? (
            <>
              <div className="flex items-center gap-3 mb-4">
                <span className="font-headline font-bold text-sm uppercase tracking-widest text-on-surface whitespace-nowrap">
                  DEEP DIVE
                </span>
                <span className="flex-1 h-px bg-outline-variant" />
                <button
                  onClick={async () => {
                    if (deepDiveCopyState !== 'idle') return
                    setDeepDiveCopyState('copied')
                    await navigator.clipboard.writeText(extended)
                    setTimeout(() => setDeepDiveCopyState('idle'), 1500)
                  }}
                  title="Copy deep dive"
                  className="material-symbols-outlined text-[18px] text-on-surface-variant/40 hover:text-on-surface-variant transition-colors select-none flex-shrink-0"
                >
                  {deepDiveCopyState === 'copied' ? 'check' : 'content_copy'}
                </button>
              </div>
              <div className="border border-outline-variant border-l-4 border-l-secondary bg-surface-container-low p-4">
                <ReactMarkdown components={MD}>{extended}</ReactMarkdown>
              </div>
              <button
                onClick={() => setExtended(null)}
                className="mt-2 font-label text-[10px] uppercase tracking-widest text-on-surface-variant/50 hover:text-on-surface-variant transition-colors min-h-[36px] flex items-center"
              >
                DISMISS
              </button>
              {extendError && (
                <p className="mt-1 font-mono text-[10px] text-error">{extendError}</p>
              )}
            </>
          ) : (
            <div>
              <button
                onClick={handleDeepDive}
                disabled={extendLoading}
                className="font-label text-[10px] uppercase tracking-widest text-on-surface border border-outline px-4 py-2 hover:border-on-surface-variant hover:bg-surface-container-high transition-colors disabled:opacity-60 min-h-[36px] flex items-center gap-2"
              >
                {extendLoading ? (
                  <>
                    <span className="inline-block w-3 h-3 border border-on-surface-variant border-t-transparent rounded-full animate-spin" />
                    GENERATING...
                  </>
                ) : (
                  'DEEP DIVE'
                )}
              </button>
              {extendError && (
                <p className="mt-1 font-mono text-[10px] text-error">{extendError}</p>
              )}
            </div>
          )}
        </div>
      )}

      {/* Content toggle — labelled "SHOW DESCRIPTION" for pre-transcription stubs,
           "SHOW RAW TRANSCRIPT" for full transcripts */}
      {article.content && (
        <div className="mb-4">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setShowTranscript(v => !v)}
              className="font-label text-[10px] uppercase tracking-widest text-on-surface-variant/60 hover:text-on-surface transition-colors min-h-[36px] flex items-center gap-1"
            >
              <span className="material-symbols-outlined text-[14px]">
                {showTranscript ? 'expand_less' : 'expand_more'}
              </span>
              {article.is_transcribed
                ? (showTranscript ? 'HIDE RAW TRANSCRIPT' : 'SHOW RAW TRANSCRIPT')
                : (showTranscript ? 'HIDE DESCRIPTION'    : 'SHOW DESCRIPTION')}
            </button>
            {article.is_transcribed && showTranscript && (
              <button
                onClick={handleCopyTranscript}
                disabled={copyState === 'copying'}
                className="font-label text-[10px] uppercase tracking-widest text-on-surface-variant/50 hover:text-on-surface-variant transition-colors disabled:opacity-50 min-h-[36px]"
              >
                {copyState === 'copied' ? 'COPIED' : 'COPY'}
              </button>
            )}
          </div>
          {/* Animated expand */}
          <div
            style={{
              display: 'grid',
              gridTemplateRows: showTranscript ? '1fr' : '0fr',
              transition: 'grid-template-rows 0.25s ease',
            }}
          >
            <div style={{ overflow: 'hidden' }}>
              <div className="mt-2 border-l-2 border-outline bg-[#1e1e1e] pl-4 pr-3 py-3 max-h-[60vh] overflow-y-auto">
                {article.is_transcribed ? (
                  <pre className="font-body text-sm text-on-surface-variant/70 leading-relaxed whitespace-pre-wrap break-words italic">
                    {article.content}
                  </pre>
                ) : (
                  <p className="font-body text-sm text-on-surface-variant/70 leading-relaxed italic">
                    {article.content}
                  </p>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Action bar — fixed on mobile, inline on desktop */}
      <div className="fixed bottom-16 left-0 right-0 md:relative md:left-auto md:right-auto md:bottom-auto md:bg-transparent bg-surface-container-low/90 border-t border-outline-variant md:border-0 backdrop-blur-sm md:backdrop-blur-none px-4 py-3 flex items-center gap-3 flex-wrap z-10">
        <SourceLink article={article} />
      </div>
    </div>
  )
}
