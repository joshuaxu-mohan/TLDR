import { useEffect, useMemo, useRef, useState } from 'react'
import {
  createSource,
  deleteSource,
  discoverSource,
  getSources,
  searchSources,
  updateSource,
  validateSourceUrl,
} from '../api'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const CORE_TOPICS = ['Tech', 'AI', 'Markets', 'Macro / Economics', 'Startups / VC']

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function timeSince(isoString) {
  if (!isoString) return null
  const diff = Date.now() - new Date(isoString).getTime()
  const minutes = Math.floor(diff / 60000)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

function topicsFromString(csv) {
  if (!csv) return []
  return csv.split(',').map(t => t.trim()).filter(Boolean)
}

function topicsToString(arr) {
  return arr.join(',')
}

// ---------------------------------------------------------------------------
// TopicChips — inline chip editor (terminal-styled)
// ---------------------------------------------------------------------------

function TopicChips({ selected, onChange }) {
  const [custom, setCustom] = useState('')

  function toggle(topic) {
    if (selected.includes(topic)) {
      onChange(selected.filter(t => t !== topic))
    } else {
      onChange([...selected, topic])
    }
  }

  function addCustom() {
    const t = custom.trim()
    if (t && !selected.includes(t)) onChange([...selected, t])
    setCustom('')
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {CORE_TOPICS.map(t => (
          <button
            key={t}
            type="button"
            onClick={() => toggle(t)}
            className={`px-2.5 py-0.5 text-[10px] font-label uppercase tracking-widest border transition-colors ${
              selected.includes(t)
                ? 'border-secondary text-on-secondary bg-secondary'
                : 'border-outline-variant text-on-surface-variant hover:border-secondary hover:text-secondary'
            }`}
          >
            {t}
          </button>
        ))}
        {selected.filter(t => !CORE_TOPICS.includes(t)).map(t => (
          <button
            key={t}
            type="button"
            onClick={() => toggle(t)}
            className="px-2.5 py-0.5 text-[10px] font-label uppercase tracking-widest border border-secondary text-on-secondary bg-secondary"
          >
            {t} ×
          </button>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          type="text"
          value={custom}
          onChange={e => setCustom(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), addCustom())}
          placeholder="Add custom topic…"
          className="flex-1 font-mono text-[11px] border border-outline-variant bg-surface-container px-2 py-1 text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-secondary"
        />
        <button
          type="button"
          onClick={addCustom}
          className="font-label text-[10px] uppercase tracking-widest px-3 py-1 border border-outline-variant text-on-surface-variant hover:border-secondary hover:text-secondary transition-colors"
        >
          ADD
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// AddSourceModal — three-state modal: discovery → config → success
// ---------------------------------------------------------------------------

const CATEGORY_OPTIONS = [
  { value: 'Tech', label: 'TECH' },
  { value: 'AI', label: 'AI' },
  { value: 'Markets', label: 'MARKETS' },
  { value: 'Macro / Economics', label: 'MACRO / ECONOMICS' },
  { value: 'Startups / VC', label: 'STARTUPS / VC' },
]

function ToggleGroup({ value, options, onChange }) {
  return (
    <div className="flex gap-0">
      {options.map(opt => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={`font-label text-[10px] uppercase tracking-widest px-3 py-1.5 border transition-colors ${
            value === opt.value
              ? 'border-secondary bg-secondary text-on-secondary'
              : 'border-outline-variant text-on-surface-variant hover:border-secondary hover:text-secondary -ml-px first:ml-0'
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}

function AddSourceModal({ onCreated, onClose }) {
  const [step,         setStep]         = useState('discovery') // discovery | config | success
  const [activeTab,    setActiveTab]    = useState('search')    // search | manual

  // Search tab
  const [searchQuery,  setSearchQuery]  = useState('')
  const [searching,    setSearching]    = useState(false)
  const [searchResults,setSearchResults]= useState([])
  const [searchError,  setSearchError]  = useState(null)
  const searchDebounce = useRef(null)

  // Manual URL tab
  const [manualUrl,    setManualUrl]    = useState('')
  const [validating,   setValidating]   = useState(false)
  const [validateResult,setValidateResult] = useState(null)
  const [validateError,setValidateError]= useState(null)

  // Shared selected source (carries into config)
  const [selected,     setSelected]     = useState(null)

  // Config step
  const [configName,   setConfigName]   = useState('')
  const [configType,   setConfigType]   = useState('podcast')
  const [configCategory,setConfigCategory] = useState('')
  const [configContentType,setConfigContentType] = useState('informative')
  const [configPriority,setConfigPriority] = useState('always')
  const [saving,       setSaving]       = useState(false)
  const [saveError,    setSaveError]    = useState(null)

  // Success step
  const [createdSource,setCreatedSource]= useState(null)

  // Close on Escape
  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  // Debounced Taddy search (400 ms, min 2 chars)
  useEffect(() => {
    clearTimeout(searchDebounce.current)
    if (!searchQuery || searchQuery.trim().length < 2) {
      setSearchResults([])
      setSearchError(null)
      return
    }
    searchDebounce.current = setTimeout(async () => {
      setSearching(true)
      setSearchError(null)
      try {
        const results = await searchSources(searchQuery.trim())
        setSearchResults(results)
        if (results.length === 0) setSearchError('No results — try the Manual URL tab')
      } catch (err) {
        setSearchError(err.message)
      } finally {
        setSearching(false)
      }
    }, 400)
    return () => clearTimeout(searchDebounce.current)
  }, [searchQuery])

  function pickResult(result) {
    setSelected({
      name: result.name,
      type: 'podcast',
      url: result.rss_url,
      description: result.description,
      image_url: result.image_url,
      author_name: result.author_name,
      taddy_uuid: result.taddy_uuid,
    })
    setConfigName(result.name)
    setConfigType('podcast')
    setConfigCategory('')
    setConfigContentType('informative')
    setConfigPriority('always')
    setStep('config')
  }

  async function handleValidate() {
    setValidating(true)
    setValidateError(null)
    setValidateResult(null)
    try {
      const result = await validateSourceUrl(manualUrl.trim())
      setValidateResult(result)
    } catch (err) {
      setValidateError(err.message)
    } finally {
      setValidating(false)
    }
  }

  function proceedManual() {
    setSelected({
      name: validateResult.name,
      type: validateResult.source_type,
      url: validateResult.url,
      description: validateResult.description,
      image_url: null,
      author_name: null,
      taddy_uuid: null,
    })
    setConfigName(validateResult.name)
    setConfigType(validateResult.source_type === 'podcast' ? 'podcast' : 'newsletter')
    setConfigCategory('')
    setConfigContentType('informative')
    setConfigPriority('always')
    setStep('config')
  }

  async function handleCreate() {
    setSaving(true)
    setSaveError(null)
    try {
      const payload = {
        name: configName.trim(),
        type: configType,
        url: selected.url,
        description: selected.description || null,
        taddy_uuid: selected.taddy_uuid || null,
        default_topics: configCategory || null,
        content_type: configContentType,
      }
      if (configType === 'podcast') {
        payload.transcript_priority = configPriority
      }
      const created = await createSource(payload)
      setCreatedSource(created)
      setStep('success')
    } catch (err) {
      setSaveError(err.message)
    } finally {
      setSaving(false)
    }
  }

  function handleAddAnother() {
    setStep('discovery')
    setActiveTab('search')
    setSearchQuery('')
    setSearchResults([])
    setSearchError(null)
    setManualUrl('')
    setValidateResult(null)
    setValidateError(null)
    setSelected(null)
    setSaveError(null)
  }

  function handleClose() {
    if (createdSource) onCreated(createdSource)
    onClose()
  }

  const inputCls = 'font-mono text-[11px] border border-outline-variant bg-surface-container px-3 py-1.5 text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-secondary w-full'
  const labelCls = 'font-label text-[9px] uppercase tracking-widest text-on-surface-variant/60 mb-1 block'

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 px-4"
      onClick={e => e.target === e.currentTarget && handleClose()}
    >
      <div className="w-full max-w-lg bg-surface border border-outline-variant max-h-[90vh] flex flex-col">

        {/* Modal header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-outline-variant flex-shrink-0">
          <span className="font-label text-[10px] uppercase tracking-widest text-secondary">
            {step === 'discovery' && 'ADD SOURCE'}
            {step === 'config'    && 'CONFIGURE SOURCE'}
            {step === 'success'   && 'SOURCE ADDED'}
          </span>
          <button
            type="button"
            onClick={handleClose}
            className="font-mono text-on-surface-variant hover:text-on-surface transition-colors min-h-[36px] px-2 text-lg leading-none"
          >
            ×
          </button>
        </div>

        <div className="overflow-y-auto flex-1 px-5 py-5 space-y-5">

          {/* ── STATE 1: DISCOVERY ── */}
          {step === 'discovery' && (
            <>
              {/* Tabs */}
              <div className="flex border-b border-outline-variant">
                {['search', 'manual'].map(tab => (
                  <button
                    key={tab}
                    type="button"
                    onClick={() => setActiveTab(tab)}
                    className={`font-label text-[10px] uppercase tracking-widest px-4 py-2 transition-colors border-b-2 -mb-px ${
                      activeTab === tab
                        ? 'border-secondary text-secondary'
                        : 'border-transparent text-on-surface-variant hover:text-on-surface'
                    }`}
                  >
                    {tab === 'search' ? 'SEARCH' : 'MANUAL URL'}
                  </button>
                ))}
              </div>

              {/* SEARCH tab */}
              {activeTab === 'search' && (
                <div className="space-y-3">
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 material-symbols-outlined text-[16px] text-on-surface-variant/50 select-none">
                      search
                    </span>
                    <input
                      type="text"
                      value={searchQuery}
                      onChange={e => setSearchQuery(e.target.value)}
                      placeholder="Search podcasts…"
                      autoFocus
                      className="font-mono text-[11px] border border-outline-variant bg-surface-container pl-8 pr-3 py-1.5 text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-secondary w-full"
                    />
                    {searching && (
                      <span className="absolute right-3 top-1/2 -translate-y-1/2 font-mono text-[9px] text-on-surface-variant/50 uppercase tracking-widest animate-pulse">
                        …
                      </span>
                    )}
                  </div>

                  {searchError && (
                    <p className="font-mono text-[10px] text-on-surface-variant/60">{searchError}</p>
                  )}

                  {searchResults.length > 0 && (
                    <ul className="space-y-1 max-h-72 overflow-y-auto">
                      {searchResults.map((r, i) => (
                        <li key={i}>
                          <button
                            type="button"
                            onClick={() => pickResult(r)}
                            className="w-full text-left border border-outline-variant bg-surface hover:border-secondary hover:bg-surface-container transition-colors px-3 py-2 flex gap-3 items-start"
                          >
                            {r.image_url && (
                              <img
                                src={r.image_url}
                                alt=""
                                className="w-10 h-10 flex-shrink-0 object-cover"
                              />
                            )}
                            <div className="min-w-0 flex-1">
                              <p className="font-body text-sm font-medium text-on-surface truncate">{r.name}</p>
                              {r.author_name && (
                                <p className="font-mono text-[10px] text-secondary/80 truncate">{r.author_name}</p>
                              )}
                              {r.description && (
                                <p className="font-mono text-[10px] text-on-surface-variant/60 line-clamp-2 mt-0.5">
                                  {r.description}
                                </p>
                              )}
                            </div>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}

                  {!searching && searchQuery.length < 2 && searchQuery.length > 0 && (
                    <p className="font-mono text-[10px] text-on-surface-variant/50">Type at least 2 characters to search</p>
                  )}
                </div>
              )}

              {/* MANUAL URL tab */}
              {activeTab === 'manual' && (
                <div className="space-y-3">
                  <div className="flex gap-2">
                    <input
                      type="url"
                      value={manualUrl}
                      onChange={e => { setManualUrl(e.target.value); setValidateResult(null); setValidateError(null) }}
                      onKeyDown={e => e.key === 'Enter' && manualUrl.trim() && handleValidate()}
                      placeholder="Paste RSS / Substack URL…"
                      autoFocus
                      className="flex-1 font-mono text-[11px] border border-outline-variant bg-surface-container px-3 py-1.5 text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-secondary"
                    />
                    <button
                      type="button"
                      onClick={handleValidate}
                      disabled={validating || !manualUrl.trim()}
                      className="font-label text-[10px] uppercase tracking-widest px-4 py-1.5 border border-secondary text-secondary hover:bg-secondary hover:text-on-secondary disabled:opacity-50 transition-colors"
                    >
                      {validating ? '…' : 'VALIDATE'}
                    </button>
                  </div>

                  {validateError && (
                    <p className="font-mono text-[10px] text-error">{validateError}</p>
                  )}

                  {validateResult && (
                    <div className="border border-outline-variant bg-surface-container-low p-3 space-y-2">
                      <p className="font-body text-sm font-medium text-on-surface">{validateResult.name}</p>
                      {validateResult.description && (
                        <p className="font-mono text-[10px] text-on-surface-variant/70 line-clamp-3">{validateResult.description}</p>
                      )}
                      <p className="font-mono text-[10px] text-secondary uppercase tracking-wider">
                        Detected: {validateResult.source_type}
                      </p>
                      <button
                        type="button"
                        onClick={proceedManual}
                        className="font-label text-[10px] uppercase tracking-widest px-4 py-1.5 bg-secondary text-on-secondary hover:opacity-90 transition-opacity"
                      >
                        CONFIGURE →
                      </button>
                    </div>
                  )}
                </div>
              )}
            </>
          )}

          {/* ── STATE 2: CONFIGURATION ── */}
          {step === 'config' && selected && (
            <div className="space-y-5">
              {/* Selected source preview */}
              <div className="flex gap-3 items-start border border-outline-variant bg-surface-container-low p-3">
                {selected.image_url && (
                  <img src={selected.image_url} alt="" className="w-12 h-12 flex-shrink-0 object-cover" />
                )}
                <div className="min-w-0">
                  <p className="font-body text-sm font-medium text-on-surface">{selected.name}</p>
                  {selected.author_name && (
                    <p className="font-mono text-[10px] text-secondary/80">{selected.author_name}</p>
                  )}
                  <p className="font-mono text-[10px] text-on-surface-variant/50 truncate mt-0.5">{selected.url}</p>
                </div>
              </div>

              {/* Name */}
              <div>
                <label className={labelCls}>NAME</label>
                <input
                  type="text"
                  value={configName}
                  onChange={e => setConfigName(e.target.value)}
                  className={inputCls}
                />
              </div>

              {/* Source type */}
              <div>
                <label className={labelCls}>SOURCE TYPE</label>
                <ToggleGroup
                  value={configType}
                  options={[{ value: 'podcast', label: 'PODCAST' }, { value: 'newsletter', label: 'NEWSLETTER' }]}
                  onChange={setConfigType}
                />
              </div>

              {/* Category */}
              <div>
                <label className={labelCls}>CATEGORY</label>
                <select
                  value={configCategory}
                  onChange={e => setConfigCategory(e.target.value)}
                  className="font-mono text-[11px] border border-outline-variant bg-surface-container px-3 py-1.5 text-on-surface focus:outline-none focus:border-secondary w-full"
                >
                  <option value="">— select category —</option>
                  {CATEGORY_OPTIONS.map(o => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </div>

              {/* Content type */}
              <div>
                <label className={labelCls}>CONTENT TYPE</label>
                <ToggleGroup
                  value={configContentType}
                  options={[{ value: 'news', label: 'NEWS' }, { value: 'informative', label: 'INFORMATIVE' }]}
                  onChange={setConfigContentType}
                />
              </div>

              {/* Transcript priority — podcasts only */}
              {configType === 'podcast' && (
                <div>
                  <label className={labelCls}>TRANSCRIPT PRIORITY</label>
                  <ToggleGroup
                    value={configPriority}
                    options={[{ value: 'always', label: 'ALWAYS' }, { value: 'on_demand', label: 'ON DEMAND' }]}
                    onChange={setConfigPriority}
                  />
                  <p className="font-mono text-[9px] text-on-surface-variant/50 mt-1.5">
                    {configPriority === 'always'
                      ? 'Episodes will be automatically transcribed each pipeline run'
                      : 'Episodes ingested with description only; transcribe manually from the feed'}
                  </p>
                </div>
              )}

              {saveError && (
                <p className="font-mono text-[10px] text-error">{saveError}</p>
              )}

              <div className="flex gap-2 justify-between pt-1">
                <button
                  type="button"
                  onClick={() => setStep('discovery')}
                  className="font-label text-[10px] uppercase tracking-widest px-4 py-1.5 border border-outline-variant text-on-surface-variant hover:border-outline hover:text-on-surface transition-colors"
                >
                  ← BACK
                </button>
                <button
                  type="button"
                  onClick={handleCreate}
                  disabled={saving || !configName.trim()}
                  className="font-label text-[10px] uppercase tracking-widest px-5 py-1.5 bg-secondary text-on-secondary hover:opacity-90 disabled:opacity-50 transition-opacity"
                >
                  {saving ? 'ADDING...' : 'ADD SOURCE'}
                </button>
              </div>
            </div>
          )}

          {/* ── STATE 3: SUCCESS ── */}
          {step === 'success' && createdSource && (
            <div className="space-y-5">
              <div className="border border-secondary/30 bg-secondary/5 p-4 space-y-2">
                <p className="font-label text-[10px] uppercase tracking-widest text-secondary">
                  ✓ SOURCE ADDED
                </p>
                <p className="font-body text-sm font-medium text-on-surface">{createdSource.name}</p>
                <p className="font-mono text-[10px] text-on-surface-variant/70">
                  Episodes will appear on the next pipeline run.
                </p>
              </div>

              {/* Scraper status */}
              <div className="border border-outline-variant bg-surface-container-low px-4 py-3">
                {createdSource.scraper_available ? (
                  <p className="font-mono text-[11px] text-secondary">
                    Scraper available — transcripts fetched from the show's website
                  </p>
                ) : (
                  <p className="font-mono text-[11px] text-on-surface-variant/70">
                    {createdSource.type === 'podcast'
                      ? 'Groq transcription will be used for this source'
                      : 'Newsletter content ingested from RSS feed'}
                  </p>
                )}
              </div>

              <div className="flex gap-2 justify-between pt-1">
                <button
                  type="button"
                  onClick={handleAddAnother}
                  className="font-label text-[10px] uppercase tracking-widest px-4 py-1.5 border border-outline-variant text-on-surface-variant hover:border-secondary hover:text-secondary transition-colors"
                >
                  ADD ANOTHER
                </button>
                <button
                  type="button"
                  onClick={handleClose}
                  className="font-label text-[10px] uppercase tracking-widest px-5 py-1.5 bg-secondary text-on-secondary hover:opacity-90 transition-opacity"
                >
                  CLOSE
                </button>
              </div>
            </div>
          )}

        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ActiveToggle — custom toggle switch
// ---------------------------------------------------------------------------

function ActiveToggle({ active, onToggle, disabled }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={disabled}
      title={active ? 'Disable source' : 'Enable source'}
      className="flex items-center justify-center min-h-[44px] min-w-[44px] focus:outline-none disabled:opacity-50"
      aria-label={active ? 'Disable source' : 'Enable source'}
    >
      <span
        className={`relative inline-flex h-4 w-8 items-center transition-colors ${
          active ? 'bg-secondary' : 'bg-surface-container-highest'
        }`}
      >
        <span
          className={`inline-block h-3 w-3 transform bg-white transition-transform ${
            active ? 'translate-x-4' : 'translate-x-0.5'
          }`}
        />
      </span>
    </button>
  )
}

// ---------------------------------------------------------------------------
// SourceTableRow — desktop table row
// ---------------------------------------------------------------------------

function SourceTableRow({ source, onUpdated, onDeleted }) {
  const [editing,         setEditing]         = useState(false)
  const [topics,          setTopics]          = useState(topicsFromString(source.default_topics))
  const [saving,          setSaving]          = useState(false)
  const [confirmDelete,   setConfirmDelete]   = useState(false)
  const [deleting,        setDeleting]        = useState(false)
  const [toggling,        setToggling]        = useState(false)
  const [togglingPriority,setTogglingPriority]= useState(false)
  const [error,           setError]           = useState(null)

  async function handleToggleActive() {
    setToggling(true)
    setError(null)
    try {
      const updated = await updateSource(source.id, { active: !source.active })
      onUpdated(updated)
    } catch (err) { setError(err.message) }
    finally { setToggling(false) }
  }

  async function handleTogglePriority() {
    if (source.type !== 'podcast') return
    setTogglingPriority(true)
    setError(null)
    const next = source.transcript_priority === 'always' ? 'on_demand' : 'always'
    try {
      const updated = await updateSource(source.id, { transcript_priority: next })
      onUpdated(updated)
    } catch (err) { setError(err.message) }
    finally { setTogglingPriority(false) }
  }

  async function handleSaveTopics() {
    setSaving(true)
    setError(null)
    try {
      const updated = await updateSource(source.id, {
        default_topics: topicsToString(topics) || null,
      })
      onUpdated(updated)
      setEditing(false)
    } catch (err) { setError(err.message) }
    finally { setSaving(false) }
  }

  async function handleDelete() {
    setDeleting(true)
    setError(null)
    try {
      await deleteSource(source.id)
      onDeleted(source.id)
    } catch (err) {
      setError(err.message)
      setDeleting(false)
      setConfirmDelete(false)
    }
  }

  const tdClass = 'border border-outline-variant px-3 py-3 text-sm align-top'

  return (
    <>
      <tr className="hover:bg-surface-container-high transition-colors">
        {/* SOURCE NAME */}
        <td className={tdClass}>
          <p className={`font-body font-bold text-sm uppercase tracking-wide ${source.active ? 'text-on-surface' : 'text-on-surface-variant/40'}`}>
            {source.name}
          </p>
          <p className="font-mono text-[10px] text-on-surface-variant/50 truncate max-w-[200px] mt-0.5">
            {source.url}
          </p>
          {topicsFromString(source.default_topics).length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-1.5">
              {topicsFromString(source.default_topics).map(t => (
                <span key={t} className="font-mono text-[9px] text-on-surface-variant/60 uppercase tracking-wider">
                  #{t}
                </span>
              ))}
            </div>
          )}
          {error && <p className="font-mono text-[10px] text-error mt-1">{error}</p>}
        </td>

        {/* TYPE */}
        <td className={tdClass}>
          <span className={`font-mono text-[10px] uppercase tracking-wider border px-2 py-0.5 ${
            source.type === 'podcast'
              ? 'border-secondary/40 text-secondary'
              : 'border-outline-variant text-on-surface-variant'
          }`}>
            {source.type === 'podcast' ? 'PODCAST' : 'NEWSLETTER'}
          </span>
        </td>

        {/* PRIORITY */}
        <td className={tdClass}>
          {source.type === 'podcast' && source.transcript_priority !== 'skip' ? (
            <button
              type="button"
              onClick={handleTogglePriority}
              disabled={togglingPriority}
              title={source.transcript_priority === 'always'
                ? 'Switch to on-demand'
                : 'Enable auto-transcribe'}
              className={`font-mono text-[10px] uppercase tracking-wider border px-2 py-0.5 transition-colors disabled:opacity-50 ${
                source.transcript_priority === 'always'
                  ? 'border-secondary/40 text-secondary hover:bg-secondary/10'
                  : 'border-outline-variant text-on-surface-variant hover:border-secondary hover:text-secondary'
              }`}
            >
              {source.transcript_priority === 'always' ? 'AUTO' : 'ON DEMAND'}
            </button>
          ) : (
            <span className="font-mono text-[10px] text-on-surface-variant/30 uppercase">—</span>
          )}
        </td>

        {/* STATUS */}
        <td className={`${tdClass} text-center`}>
          <ActiveToggle
            active={source.active}
            onToggle={handleToggleActive}
            disabled={toggling}
          />
        </td>

        {/* ACTIONS */}
        <td className={tdClass}>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => { setEditing(!editing); setTopics(topicsFromString(source.default_topics)) }}
              title="Edit topics"
              className="font-label text-[9px] uppercase tracking-widest text-on-surface-variant hover:text-secondary transition-colors min-h-[36px] px-2"
            >
              EDIT
            </button>
            {!confirmDelete ? (
              <button
                type="button"
                onClick={() => setConfirmDelete(true)}
                title="Delete source"
                className="font-label text-[9px] uppercase tracking-widest text-on-surface-variant hover:text-error transition-colors min-h-[36px] px-2"
              >
                DEL
              </button>
            ) : (
              <span className="flex items-center gap-1 font-mono text-[10px]">
                <span className="text-error">DELETE?</span>
                <button
                  type="button"
                  onClick={handleDelete}
                  disabled={deleting}
                  className="text-error hover:opacity-70 disabled:opacity-50 min-h-[36px] px-1"
                >
                  {deleting ? '...' : 'YES'}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmDelete(false)}
                  className="text-on-surface-variant hover:text-on-surface min-h-[36px] px-1"
                >
                  NO
                </button>
              </span>
            )}
          </div>
        </td>
      </tr>

      {/* Inline topic editor row */}
      {editing && (
        <tr className="bg-surface-container">
          <td colSpan={5} className="border border-outline-variant px-4 py-3">
            <p className="font-label text-[10px] uppercase tracking-widest text-on-surface-variant/50 mb-2">
              EDIT TOPICS
            </p>
            <TopicChips selected={topics} onChange={setTopics} />
            <div className="flex gap-2 justify-end mt-3">
              <button
                type="button"
                onClick={() => setEditing(false)}
                className="font-label text-[10px] uppercase tracking-widest px-3 py-1.5 border border-outline-variant text-on-surface-variant hover:border-outline hover:text-on-surface transition-colors"
              >
                CANCEL
              </button>
              <button
                type="button"
                onClick={handleSaveTopics}
                disabled={saving}
                className="font-label text-[10px] uppercase tracking-widest px-3 py-1.5 bg-secondary text-on-secondary hover:opacity-90 disabled:opacity-50"
              >
                {saving ? 'SAVING...' : 'SAVE'}
              </button>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// SourceCard — mobile card layout
// ---------------------------------------------------------------------------

function SourceCard({ source, onUpdated, onDeleted }) {
  const [editing,          setEditing]          = useState(false)
  const [topics,           setTopics]           = useState(topicsFromString(source.default_topics))
  const [saving,           setSaving]           = useState(false)
  const [confirmDelete,    setConfirmDelete]    = useState(false)
  const [deleting,         setDeleting]         = useState(false)
  const [toggling,         setToggling]         = useState(false)
  const [togglingPriority, setTogglingPriority] = useState(false)
  const [togglingCategory, setTogglingCategory] = useState(false)
  const [error,            setError]            = useState(null)

  async function handleToggleActive() {
    setToggling(true)
    try {
      onUpdated(await updateSource(source.id, { active: !source.active }))
    } catch (err) { setError(err.message) }
    finally { setToggling(false) }
  }

  async function handleTogglePriority() {
    if (source.type !== 'podcast') return
    setTogglingPriority(true)
    const next = source.transcript_priority === 'always' ? 'on_demand' : 'always'
    try {
      onUpdated(await updateSource(source.id, { transcript_priority: next }))
    } catch (err) { setError(err.message) }
    finally { setTogglingPriority(false) }
  }

  async function handleToggleCategory() {
    setTogglingCategory(true)
    const next = source.content_category === 'news' ? 'informative' : 'news'
    try {
      onUpdated(await updateSource(source.id, { content_type: next }))
    } catch (err) { setError(err.message) }
    finally { setTogglingCategory(false) }
  }

  async function handleSaveTopics() {
    setSaving(true)
    try {
      onUpdated(await updateSource(source.id, { default_topics: topicsToString(topics) || null }))
      setEditing(false)
    } catch (err) { setError(err.message) }
    finally { setSaving(false) }
  }

  async function handleDelete() {
    setDeleting(true)
    try {
      await deleteSource(source.id)
      onDeleted(source.id)
    } catch (err) {
      setError(err.message)
      setDeleting(false)
      setConfirmDelete(false)
    }
  }

  return (
    <li className={`border bg-[#181818] p-4 flex flex-col gap-2 ${source.active ? 'border-secondary/40' : 'border-outline-variant'}`}>
      {/* Header row */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className={`font-headline font-bold text-base break-words ${source.active ? 'text-on-surface' : 'text-on-surface-variant/40'}`}>
            {source.name}
          </p>
          <div className="flex items-center gap-2 mt-1 flex-wrap">
            <span
              className="font-label text-[9px] uppercase tracking-wider border px-1.5 py-0.5"
              style={source.type === 'podcast'
                ? { color: '#3ECF6E', borderColor: '#3ECF6E66' }
                : { color: '#E8813B', borderColor: '#E8813B66' }}
            >
              {source.type === 'podcast' ? 'PODCAST' : 'NEWSLETTER'}
            </span>
            {source.type === 'podcast' && source.transcript_priority && source.transcript_priority !== 'skip' && (
              <button
                type="button"
                onClick={handleTogglePriority}
                disabled={togglingPriority}
                className={`font-label text-[9px] uppercase tracking-wider border px-1.5 py-0.5 transition-colors disabled:opacity-50 ${
                  source.transcript_priority === 'always'
                    ? 'border-secondary/40 text-secondary'
                    : 'border-outline-variant text-on-surface-variant hover:border-secondary hover:text-secondary'
                }`}
              >
                {source.transcript_priority === 'always' ? 'AUTO' : 'ON DEMAND'}
              </button>
            )}
            <button
              type="button"
              onClick={handleToggleCategory}
              disabled={togglingCategory}
              className={`font-label text-[9px] uppercase tracking-wider border px-1.5 py-0.5 transition-colors disabled:opacity-50 ${
                source.content_category === 'news'
                  ? 'border-secondary/40 text-secondary'
                  : 'border-outline-variant text-on-surface-variant hover:border-secondary hover:text-secondary'
              }`}
            >
              {source.content_category === 'news' ? 'NEWS' : 'INFORMATIVE'}
            </button>
          </div>
          {topicsFromString(source.default_topics).length > 0 && (
            <div className="flex flex-wrap gap-2 mt-1.5">
              {topicsFromString(source.default_topics).map(t => (
                <span key={t} className="font-mono text-[9px] text-on-surface-variant/50 uppercase tracking-wider">
                  #{t}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="flex flex-col items-end gap-1 flex-shrink-0">
          {source.last_ingested_at ? (
            <p className="font-mono text-[10px] text-on-surface-variant/50">
              {timeSince(source.last_ingested_at)}
            </p>
          ) : (
            <p className="font-mono text-[10px] text-on-surface-variant/30">not ingested</p>
          )}
          <ActiveToggle active={source.active} onToggle={handleToggleActive} disabled={toggling} />
        </div>
      </div>

      {/* Action row */}
      <div className="flex items-center gap-3 mt-2 pt-2 border-t border-outline-variant/40">
        <button
          type="button"
          onClick={() => { setEditing(!editing); setTopics(topicsFromString(source.default_topics)) }}
          className="font-label text-[9px] uppercase tracking-widest text-on-surface-variant hover:text-secondary transition-colors min-h-[36px]"
        >
          EDIT TOPICS
        </button>
        {!confirmDelete ? (
          <button
            type="button"
            onClick={() => setConfirmDelete(true)}
            className="font-label text-[9px] uppercase tracking-widest text-on-surface-variant hover:text-error transition-colors min-h-[36px]"
          >
            DELETE
          </button>
        ) : (
          <span className="flex items-center gap-2 font-mono text-[10px]">
            <span className="text-error">DELETE?</span>
            <button type="button" onClick={handleDelete} disabled={deleting} className="text-error disabled:opacity-50 min-h-[36px]">
              {deleting ? '...' : 'YES'}
            </button>
            <button type="button" onClick={() => setConfirmDelete(false)} className="text-on-surface-variant min-h-[36px]">
              NO
            </button>
          </span>
        )}
      </div>

      {error && <p className="font-mono text-[10px] text-error mt-1">{error}</p>}

      {/* Inline topic editor */}
      {editing && (
        <div className="mt-3 pt-3 border-t border-outline-variant space-y-3">
          <TopicChips selected={topics} onChange={setTopics} />
          <div className="flex gap-2 justify-end">
            <button type="button" onClick={() => setEditing(false)}
              className="font-label text-[10px] uppercase tracking-widest px-3 py-1.5 border border-outline-variant text-on-surface-variant hover:border-outline transition-colors">
              CANCEL
            </button>
            <button type="button" onClick={handleSaveTopics} disabled={saving}
              className="font-label text-[10px] uppercase tracking-widest px-3 py-1.5 bg-secondary text-on-secondary hover:opacity-90 disabled:opacity-50">
              {saving ? 'SAVING...' : 'SAVE'}
            </button>
          </div>
        </div>
      )}
    </li>
  )
}

// ---------------------------------------------------------------------------
// Main Sources page
// ---------------------------------------------------------------------------

const SOURCE_TAGS = ['AI', 'Markets', 'Macro / Economics', 'Startups / VC', 'Tech']

export default function Sources() {
  const [sources,        setSources]        = useState([])
  const [loading,        setLoading]        = useState(true)
  const [error,          setError]          = useState(null)
  const [showModal,      setShowModal]      = useState(false)
  const [sourceSearch,   setSourceSearch]   = useState('')
  const [sourceQuery,    setSourceQuery]    = useState('')
  const [formatFilter,   setFormatFilter]   = useState('all')   // 'all' | 'podcast' | 'substack'
  const [tagFilter,      setTagFilter]      = useState('')       // '' | tag string
  const [priorityFilter, setPriorityFilter] = useState('all')   // 'all' | 'always' | 'on_demand'
  const searchDebounce = useRef(null)

  useEffect(() => {
    getSources()
      .then(setSources)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  // Debounce source filter (300 ms)
  useEffect(() => {
    clearTimeout(searchDebounce.current)
    searchDebounce.current = setTimeout(() => setSourceQuery(sourceSearch.trim()), 300)
    return () => clearTimeout(searchDebounce.current)
  }, [sourceSearch])

  const displaySources = useMemo(() => {
    let result = sources
    if (sourceQuery) {
      const q = sourceQuery.toLowerCase()
      result = result.filter(s => s.name.toLowerCase().includes(q))
    }
    if (formatFilter !== 'all') {
      result = result.filter(s => s.type === formatFilter)
    }
    if (tagFilter) {
      result = result.filter(s =>
        s.default_topics
          ?.split(',')
          .map(t => t.trim().toLowerCase())
          .includes(tagFilter.toLowerCase())
      )
    }
    if (priorityFilter !== 'all') {
      result = result.filter(s => s.transcript_priority === priorityFilter)
    }
    return result
  }, [sources, sourceQuery, formatFilter, tagFilter, priorityFilter])

  const hasPodcasts = sources.some(s => s.type === 'podcast')

  function handleCreated(newSource) {
    if (newSource) setSources(prev => [...prev, newSource])
    setShowModal(false)
  }

  function handleUpdated(updated) {
    setSources(prev => prev.map(s => (s.id === updated.id ? updated : s)))
  }

  function handleDeleted(id) {
    setSources(prev => prev.filter(s => s.id !== id))
  }

  const thClass = 'border border-outline-variant px-3 py-2 text-left font-label text-[10px] uppercase tracking-widest text-on-surface-variant/60 bg-surface-container'

  return (
    <div>
      {/* Add source modal */}
      {showModal && (
        <AddSourceModal
          onCreated={handleCreated}
          onClose={() => setShowModal(false)}
        />
      )}

      {/* Page header */}
      <div className="px-4 pt-6 pb-4 border-b border-outline-variant">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <h1 className="font-headline font-black text-4xl md:text-5xl text-on-surface leading-none">
            Sources
          </h1>
          <button
            type="button"
            onClick={() => setShowModal(true)}
            className="font-label text-[10px] uppercase tracking-widest text-on-secondary bg-secondary border border-secondary px-4 py-2 hover:opacity-90 transition-opacity min-h-[36px]"
          >
            + ADD SOURCE
          </button>
        </div>
      </div>

      <div className="px-4 pt-4">

        {loading && (
          <div className="flex items-center justify-center h-40">
            <span className="font-label text-[11px] text-on-surface-variant uppercase tracking-widest animate-pulse">
              LOADING SOURCES...
            </span>
          </div>
        )}

        {error && (
          <div className="border border-error/30 bg-error/5 p-3 font-label text-sm text-error mb-4">
            ERROR: {error}
          </div>
        )}

        {!loading && !error && sources.length === 0 && (
          <p className="font-label text-sm text-on-surface-variant py-6">
            No sources yet — click + ADD SOURCE to get started
          </p>
        )}

        {!loading && !error && sources.length > 0 && (
          <>
            {/* Filter bar */}
            <div className="mb-4 space-y-2">
              {/* FORMAT */}
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-label text-[10px] uppercase tracking-widest text-on-surface-variant/50 font-bold">FORMAT:</span>
                {[
                  { value: 'all',      label: 'ALL',        color: null },
                  { value: 'podcast',  label: 'PODCAST',    color: '#3ECF6E' },
                  { value: 'substack', label: 'NEWSLETTER', color: '#E8813B' },
                ].map(({ value, label, color }) => (
                  <button
                    key={value}
                    onClick={() => setFormatFilter(value)}
                    className={`font-label text-[10px] uppercase tracking-widest px-1 py-1 min-h-[36px] transition-colors whitespace-nowrap ${
                      formatFilter === value
                        ? 'underline underline-offset-4'
                        : 'text-on-surface-variant hover:text-on-surface'
                    }`}
                    style={formatFilter === value ? { color: color ?? '#00E5FF' } : {}}
                  >
                    {label}
                  </button>
                ))}
                <span className="text-outline-variant select-none px-1">|</span>
                {/* PRIORITY — only when podcasts exist */}
                {hasPodcasts && (
                  <>
                    <span className="font-label text-[10px] uppercase tracking-widest text-on-surface-variant/50 font-bold">PRIORITY:</span>
                    {[
                      { value: 'all',       label: 'ALL' },
                      { value: 'always',    label: 'AUTO' },
                      { value: 'on_demand', label: 'ON DEMAND' },
                    ].map(({ value, label }) => (
                      <button
                        key={value}
                        onClick={() => setPriorityFilter(value)}
                        className={`font-label text-[10px] uppercase tracking-widest px-1 py-1 min-h-[36px] transition-colors whitespace-nowrap ${
                          priorityFilter === value
                            ? 'text-secondary underline underline-offset-4'
                            : 'text-on-surface-variant hover:text-on-surface'
                        }`}
                      >
                        {label}
                      </button>
                    ))}
                    <span className="text-outline-variant select-none px-1">|</span>
                  </>
                )}
                {/* TAGS */}
                <span className="font-label text-[10px] uppercase tracking-widest text-on-surface-variant/50 font-bold">TOPIC:</span>
                <button
                  onClick={() => setTagFilter('')}
                  className={`font-label text-[10px] uppercase tracking-widest px-1 py-1 min-h-[36px] transition-colors whitespace-nowrap ${
                    tagFilter === '' ? 'text-secondary underline underline-offset-4' : 'text-on-surface-variant hover:text-on-surface'
                  }`}
                >
                  ALL
                </button>
                {SOURCE_TAGS.map(tag => (
                  <button
                    key={tag}
                    onClick={() => setTagFilter(tagFilter === tag ? '' : tag)}
                    className={`font-label text-[10px] uppercase tracking-widest px-1 py-1 min-h-[36px] transition-colors whitespace-nowrap ${
                      tagFilter === tag ? 'text-secondary underline underline-offset-4' : 'text-on-surface-variant hover:text-on-surface'
                    }`}
                  >
                    {tag.toUpperCase()}
                  </button>
                ))}
              </div>

              {/* Search */}
              <div className="relative">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 material-symbols-outlined text-[16px] text-on-surface-variant/50 select-none">
                  search
                </span>
                <input
                  type="text"
                  value={sourceSearch}
                  onChange={e => setSourceSearch(e.target.value)}
                  placeholder="Search sources…"
                  className="font-label text-sm border border-outline-variant bg-surface-container pl-9 py-1.5 text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-secondary w-full"
                  style={{ paddingRight: sourceSearch ? '2rem' : '0.75rem' }}
                />
                {sourceSearch && (
                  <button
                    type="button"
                    onClick={() => setSourceSearch('')}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-on-surface-variant/50 hover:text-on-surface transition-colors min-h-[24px] px-1 font-label text-base leading-none"
                  >
                    ×
                  </button>
                )}
              </div>
            </div>

            {displaySources.length === 0 && (
              <p className="font-label text-sm text-on-surface-variant uppercase tracking-wider py-4">
                No sources match current filters
              </p>
            )}

            {/* All screens: card grid */}
            {displaySources.length > 0 && (
              <ul className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
                {displaySources.map(s => (
                  <SourceCard
                    key={s.id}
                    source={s}
                    onUpdated={handleUpdated}
                    onDeleted={handleDeleted}
                  />
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </div>
  )
}
