import { useState } from 'react'
import Prices from './pages/Prices'
import Optimizer from './pages/Optimizer'
import Elia from './pages/Elia'

const NAV = [
  { id: 'prices',    icon: '⚡', label: 'Dag-ahead Prijzen' },
  { id: 'optimizer', icon: '🔋', label: 'MILP Optimizer' },
  { id: 'elia',      icon: '🌿', label: 'Elia Netwerk' },
]

export default function App() {
  const [page, setPage] = useState('prices')

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-logo">
          <span style={{ fontSize: 28 }}>⚡</span>
          <div>
            <div style={{ fontWeight: 700, color: '#e2e8f0', fontSize: 15 }}>Energize EMS</div>
            <div style={{ color: '#4b5563', fontSize: 11 }}>Energy Management System</div>
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

        <div style={{ marginTop: 'auto', padding: '16px 0', borderTop: '1px solid #2a2d3e' }}>
          <div style={{ color: '#4b5563', fontSize: 11, textAlign: 'center' }}>
            v2.0.0 · FastAPI + React
          </div>
          <div style={{ color: '#374151', fontSize: 10, textAlign: 'center', marginTop: 4 }}>
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
