import { useState } from 'react'
import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import MasterFeed from './pages/MasterFeed'
import Archive from './pages/Archive'
import ArticleDetail from './pages/ArticleDetail'
import Sources from './pages/Sources'
import GroqBudget from './components/GroqBudget'
import GeminiBudget from './components/GeminiBudget'

const NAV_LINKS = [
  { to: '/',        label: 'FEED',    icon: 'dynamic_feed',             end: true  },
  { to: '/archive', label: 'ARCHIVE', icon: 'archive',                  end: false },
  { to: '/sources', label: 'SOURCES', icon: 'settings_input_component', end: false },
]

/**
 * Module-level sidebar state — persists across route navigation, resets on F5.
 * Default collapsed (icon-only, 64 px wide).
 */
let _sidebarExpanded = false

function Sidebar() {
  const [expanded, setExpanded] = useState(_sidebarExpanded)

  function toggle() {
    const next = !expanded
    _sidebarExpanded = next
    setExpanded(next)
  }

  function collapse() {
    _sidebarExpanded = false
    setExpanded(false)
  }

  return (
    <>
      {/* Sidebar — icon-only (w-16) by default, expands to w-44 as overlay */}
      <aside
        className={`hidden md:flex fixed left-0 top-0 bottom-0 flex-col bg-surface-container-lowest border-r border-outline-variant z-50 overflow-hidden transition-[width] duration-200 ${
          expanded ? 'w-44' : 'w-16'
        }`}
      >
        {/* Toggle button */}
        <button
          onClick={toggle}
          title={expanded ? 'Collapse sidebar' : 'Expand sidebar'}
          className="flex items-center justify-center h-12 border-b border-outline-variant text-on-surface-variant hover:text-on-surface transition-colors flex-shrink-0"
        >
          <span className="material-symbols-outlined text-[20px]">
            {expanded ? 'chevron_left' : 'menu'}
          </span>
        </button>

        {/* Nav links */}
        <nav className="flex-1 py-2">
          {NAV_LINKS.map(({ to, label, icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              onClick={collapse}
              className={({ isActive }) =>
                `flex items-center h-12 transition-colors border-l-2 ${
                  expanded ? 'gap-3 px-4' : 'justify-center'
                } ${
                  isActive
                    ? 'bg-surface-container-low text-secondary border-secondary'
                    : 'text-white/40 border-transparent hover:bg-surface-container-low hover:text-white'
                }`
              }
            >
              <span className="material-symbols-outlined flex-shrink-0">{icon}</span>
              {expanded && (
                <span className="font-label text-[11px] font-medium uppercase tracking-widest truncate">
                  {label}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* Backdrop — closes sidebar when clicking outside */}
      {expanded && (
        <div
          className="hidden md:block fixed inset-0 z-40"
          onClick={collapse}
        />
      )}
    </>
  )
}

function TopBar() {
  return (
    <header className="fixed top-0 left-0 right-0 md:left-16 h-12 z-40 bg-[#131313] border-b border-outline-variant flex items-center justify-between px-4" style={{ backgroundColor: '#131313' }}>
      {/* Desktop spacer */}
      <span className="hidden md:block" />
      {/* Budget — both mobile and desktop */}
      <div className="flex items-center gap-4">
        <GroqBudget variant="topbar" />
        <GeminiBudget variant="topbar" />
      </div>
    </header>
  )
}

function BottomNav() {
  return (
    <nav className="md:hidden fixed bottom-0 left-0 right-0 h-16 bg-surface-container-lowest border-t border-outline-variant z-20 flex items-center">
      {NAV_LINKS.map(({ to, label, icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className={({ isActive }) =>
            `flex-1 flex flex-col items-center justify-center gap-1 min-h-[44px] transition-colors ${
              isActive ? 'text-secondary' : 'text-white/40'
            }`
          }
        >
          <span className="material-symbols-outlined">{icon}</span>
          <span className="font-label text-[8px] font-medium uppercase tracking-widest">{label}</span>
        </NavLink>
      ))}
    </nav>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-surface text-on-surface">
        <Sidebar />
        <TopBar />
        <BottomNav />
        {/* Content is always offset by the icon-only sidebar (w-16 = 64px) on desktop */}
        <main className="md:pl-16 pt-12 pb-16 md:pb-0">
          <Routes>
            <Route path="/" element={<MasterFeed />} />
            <Route path="/archive" element={<Archive />} />
            <Route path="/article/:id" element={<ArticleDetail />} />
            <Route path="/sources" element={<Sources />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
