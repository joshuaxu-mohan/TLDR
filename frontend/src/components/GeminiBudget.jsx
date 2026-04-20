import { useEffect, useState } from 'react'
import { getGeminiBudget } from '../api'

/**
 * Compact Gemini budget display — shows remaining daily API calls.
 *
 * Props:
 *   variant     — 'sidebar' (legacy, unused) or 'topbar'
 *   refreshKey  — increment to force a re-fetch
 *
 * Also listens for the 'gemini-budget-refresh' custom event.
 *
 * Desktop: "GEMINI: 491/500 today"
 * Mobile:  "491/500"
 */
export default function GeminiBudget({ refreshKey = 0 }) {
  const [budget, setBudget] = useState(null)
  const [error,  setError]  = useState(false)

  function fetchBudget() {
    setError(false)
    getGeminiBudget()
      .then(data => { setBudget(data) })
      .catch(err => { console.error('GeminiBudget fetch failed:', err); setError(true) })
  }

  useEffect(() => { fetchBudget() }, [refreshKey])

  useEffect(() => {
    window.addEventListener('gemini-budget-refresh', fetchBudget)
    return () => window.removeEventListener('gemini-budget-refresh', fetchBudget)
  }, [])

  if (error) return (
    <p className="font-label text-[10px] uppercase tracking-wider text-on-surface-variant/40">
      GEMINI: — failed to load
    </p>
  )
  if (!budget) return null

  const remaining = budget.remaining_today
  const limit     = budget.limit_today

  return (
    <p className="font-label text-[10px] uppercase tracking-wider text-secondary">
      <span className="hidden md:inline">GEMINI: {remaining}/{limit} today</span>
      <span className="md:hidden">{remaining}/{limit}</span>
    </p>
  )
}
