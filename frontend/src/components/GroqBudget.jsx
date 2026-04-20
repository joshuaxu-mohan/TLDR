import { useEffect, useState } from 'react'
import { getGroqBudget } from '../api'

/**
 * Compact Groq budget display — shows hourly and daily remaining minutes.
 *
 * Props:
 *   variant     — 'sidebar' (legacy, unused) or 'topbar'
 *   refreshKey  — increment to force a re-fetch
 *
 * Also listens for the 'groq-budget-refresh' custom event fired by
 * ArticleDetail after a successful on-demand transcription.
 *
 * Desktop: "GROQ: 120m/hr · 480m/day"
 * Mobile:  "120m/hr"
 */
export default function GroqBudget({ refreshKey = 0 }) {
  const [budget, setBudget] = useState(null)
  const [error,  setError]  = useState(false)

  function fetchBudget() {
    setError(false)
    getGroqBudget()
      .then(data => { setBudget(data) })
      .catch(err => { console.error('GroqBudget fetch failed:', err); setError(true) })
  }

  useEffect(() => { fetchBudget() }, [refreshKey])

  useEffect(() => {
    window.addEventListener('groq-budget-refresh', fetchBudget)
    return () => window.removeEventListener('groq-budget-refresh', fetchBudget)
  }, [])

  if (error) return (
    <p className="font-label text-[10px] uppercase tracking-wider text-on-surface-variant/40">
      GROQ: — failed to load
    </p>
  )
  if (!budget) return null

  const hourly = budget.remaining_minutes_hour.toFixed(0)
  const daily  = budget.remaining_minutes_day.toFixed(0)

  return (
    <p className="font-label text-[10px] uppercase tracking-wider text-secondary">
      <span className="hidden md:inline">GROQ: {hourly}m/hr · {daily}m/day</span>
      <span className="md:hidden">{hourly}m/hr</span>
    </p>
  )
}
