import { useState, useEffect } from 'react'
import Prices from './pages/Prices'
import Optimizer from './pages/Optimizer'
import Elia from './pages/Elia'

const FluxyLogo = ({ size = 44 }) => (
  <svg width={size} height={size} viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <linearGradient id="fl-og2" x1="12" y1="72" x2="88" y2="8" gradientUnits="userSpaceOnUse">
        <stop offset="0%"   stopColor="#c2410c"/>
        <stop offset="40%"  stopColor="#f97316"/>
        <stop offset="100%" stopColor="#fde047"/>
      </linearGradient>
      <linearGradient id="fl-og1" x1="20" y1="68" x2="80" y2="14" gradientUnits="userSpaceOnUse">
        <stop offset="0%"   stopColor="#ea580c"/>
        <stop offset="55%"  stopColor="#fb923c"/>
        <stop offset="100%" stopColor="#fef08a"/>
      </linearGradient>
      <linearGradient id="fl-bl2" x1="88" y1="28" x2="12" y2="92" gradientUnits="userSpaceOnUse">
        <stop offset="0%"   stopColor="#1e3a8a"/>
        <stop offset="50%"  stopColor="#1d4ed8"/>
        <stop offset="100%" stopColor="#38bdf8"/>
      </linearGradient>
      <linearGradient id="fl-bl1" x1="80" y1="32" x2="20" y2="86" gradientUnits="userSpaceOnUse">
        <stop offset="0%"   stopColor="#1e40af"/>
        <stop offset="55%"  stopColor="#3b82f6"/>
        <stop offset="100%" stopColor="#7dd3fc"/>
      </linearGradient>
    </defs>
    {/* Oranje achterste laag */}
    <path d="M 8,70 C 2,50 6,22 34,8 C 56,-1 80,5 88,24 C 94,40 86,56 65,62 C 46,68 22,74 8,70 Z"
      fill="url(#fl-og2)" opacity="0.85"/>
    {/* Oranje voorste laag */}
    <path d="M 14,63 C 9,46 14,22 38,12 C 57,4 77,10 83,27 C 88,41 80,53 62,58 C 44,63 24,67 14,63 Z"
      fill="url(#fl-og1)"/>
    {/* Blauwe achterste laag */}
    <path d="M 92,30 C 98,50 94,78 66,92 C 44,101 20,95 12,76 C 6,60 14,44 35,38 C 54,32 78,26 92,30 Z"
      fill="url(#fl-bl2)" opacity="0.85"/>
    {/* Blauwe voorste laag */}
    <path d="M 86,37 C 91,54 86,78 62,88 C 43,96 23,90 17,73 C 12,59 20,47 38,42 C 56,37 76,33 86,37 Z"
      fill="url(#fl-bl1)"/>
  </svg>
)

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
          <img src="/fluxy-logo.jpg" alt="Fluxy logo"
            style={{ width: 44, height: 44, objectFit: 'contain', borderRadius: 6 }}/>
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
