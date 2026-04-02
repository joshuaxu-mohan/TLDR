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

  function fetchBudget() {
    getGroqBudget().then(setBudget).catch(() => {})
  }

  useEffect(() => { fetchBudget() }, [refreshKey])

  useEffect(() => {
    window.addEventListener('groq-budget-refresh', fetchBudget)
    return () => window.removeEventListener('groq-budget-refresh', fetchBudget)
  }, [])

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
