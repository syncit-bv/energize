import { useState, useEffect } from 'react'
import Prices from './pages/Prices'
import Optimizer from './pages/Optimizer'
import Elia from './pages/Elia'

const NAV = [
  { id: 'prices',    icon: '⚡', label: 'Dag-ahead Prijzen' },
  { id: 'optimizer', icon: '🔋', label: 'MILP Optimizer' },
  { id: 'elia',      icon: '🌿', label: 'Elia Netwerk' },
]

function getInitialTheme() {
  try {
    const stored = localStorage.getItem('fluxy-theme')
    if (stored === 'light' || stored === 'dark') return stored
  } catch {}
  return 'dark'
}

export default function App() {
  const [page, setPage] = useState('prices')
  const [theme, setTheme] = useState(getInitialTheme)

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    try { localStorage.setItem('fluxy-theme', theme) } catch {}
  }, [theme])

  function toggleTheme() {
    setTheme(t => t === 'dark' ? 'light' : 'dark')
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-logo">
          <span style={{ fontSize: 28 }}>⚡</span>
          <div>
            <div style={{ fontWeight: 700, color: 'var(--text)', fontSize: 17, letterSpacing: '-0.3px' }}>Fluxy</div>
            <div style={{ color: 'var(--muted)', fontSize: 11 }}>Energy Management System</div>
          </div>
        </div>

        <nav className="sidebar-nav">
          {NAV.map(({ id, icon, label }) => (
            <button key={id} onClick={() => setPage(id)}
              className={`nav-item ${page === id ? 'active' : ''}`}>
              <span className="nav-icon">{icon}</span>
              <span>{label}</span>
            </button>
          ))}
        </nav>

        <div style={{ marginTop: 'auto', borderTop: '1px solid var(--border)', paddingTop: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <button
            onClick={toggleTheme}
            style={{
              background: 'var(--bg)',
              border: '1px solid var(--border)',
              borderRadius: 8,
              color: 'var(--muted)',
              cursor: 'pointer',
              fontSize: 12,
              padding: '7px 12px',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              width: '100%',
              transition: 'all 0.15s',
            }}
            title={theme === 'dark' ? 'Schakel naar lichtmodus' : 'Schakel naar donkermodus'}
          >
            <span style={{ fontSize: 15 }}>{theme === 'dark' ? '☀️' : '🌙'}</span>
            <span>{theme === 'dark' ? 'Lichtmodus' : 'Donkermodus'}</span>
          </button>
          <div style={{ color: 'var(--muted2)', fontSize: 11, textAlign: 'center' }}>
            v2.0.0 · FastAPI + React
          </div>
          <div style={{ color: 'var(--muted2)', fontSize: 10, textAlign: 'center' }}>
            🇧🇪 Belgian Grid Data
          </div>
        </div>
      </aside>

      <main className="main-content">
        {page === 'prices'    && <Prices/>}
        {page === 'optimizer' && <Optimizer/>}
        {page === 'elia'      && <Elia/>}
      </main>
    </div>
  )
}
