import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getTranscriptionLog } from '../api'

/**
 * Compact collapsible panel showing recent Groq transcription activity.
 * Terminal aesthetic: mono font, green source names, muted metadata.
 *
 * Props:
 *   hours      — look-back window in hours (default 24)
 *   refreshKey — increment to force a re-fetch
 */

function formatDuration(seconds) {
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}

function timeAgo(isoString) {
  const diff = (Date.now() - new Date(isoString).getTime()) / 1000
  if (diff < 60)    return 'NOW'
  if (diff < 3600)  return `${Math.floor(diff / 60)}M`
  if (diff < 86400) return `${Math.floor(diff / 3600)}H`
  return `${Math.floor(diff / 86400)}D`
}

export default function TranscriptionLog({ hours = 24, refreshKey = 0 }) {
  const [entries, setEntries] = useState([])
  const [open,    setOpen]    = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    getTranscriptionLog(hours)
      .then(setEntries)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [hours, refreshKey])

  if (loading || entries.length === 0) return null

  return (
    <div className="mb-4 border border-outline-variant">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-3 py-2 hover:bg-surface-container-high transition-colors min-h-[40px]"
      >
        <span className="font-label text-[10px] text-secondary uppercase tracking-wider">
          TRANSCRIPTION LOG
        </span>
        <span className="font-label text-[10px] text-on-surface-variant">
          {entries.length} IN {hours}H {open ? '▲' : '▼'}
        </span>
      </button>

      {open && (
        <ul className="divide-y divide-outline-variant/40 px-3 pb-2">
          {entries.map(entry => (
            <li key={entry.id} className="py-2 flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="font-label text-[10px] text-secondary uppercase tracking-wider truncate">
                  {entry.source_name}
                </p>
                <Link
                  to={`/article/${entry.article_id}`}
                  className="font-label text-[11px] text-on-surface-variant hover:text-on-surface truncate block max-w-xs"
                >
                  {entry.title}
                </Link>
              </div>
              <div className="flex-shrink-0 text-right">
                <p className="font-label text-[10px] text-on-surface-variant">
                  {formatDuration(entry.audio_seconds)}
                </p>
                <p className="font-label text-[10px] text-on-surface-variant/40">
                  {timeAgo(entry.transcribed_at)} AGO
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
