import { useEffect, useState } from 'react'
import {
  LineChart, Line, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend, ComposedChart, Bar,
} from 'recharts'
import {
  fetchImbalance, fetchLiveImbalance,
  fetchSolarWind, fetchSolarForecast, fetchWindForecast,
} from '../api'
import { format, parseISO } from 'date-fns'

const toDateStr = (d) => format(d, 'yyyy-MM-dd')

const TABS = [
  { id: 'imbal',    label: '⚖️ Onbalans 15-min' },
  { id: 'live',     label: '⚡ Live 5-min' },
  { id: 'zonwind',  label: '☀️🌬️ Zon & Wind' },
  { id: 'prognose', label: '📡 Prognose vs Realisatie' },
]

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background:'var(--surface)', border:'1px solid var(--border)', borderRadius:8, padding:'10px 14px' }}>
      <div style={{ color:'var(--muted)', fontSize:12, marginBottom:4 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.stroke || p.fill || p.color, fontWeight:600, fontSize:13 }}>
          {p.name}: {typeof p.value === 'number' ? p.value.toFixed(1) : p.value}
          {p.unit || ''}
        </div>
      ))}
    </div>
  )
}

const fmtTs = (ts) => { try { return format(parseISO(ts), 'HH:mm') } catch { return ts } }

function useEliaData(fetchFn, date, deps = []) {
  const [data,    setData]    = useState([])
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState(null)

  useEffect(() => {
    setLoading(true); setError(null)
    fetchFn(date)
      .then(r => setData(r.data || []))
      .catch(e => setError(e.response?.data?.detail || e.message))
      .finally(() => setLoading(false))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date, ...deps])

  return { data, loading, error }
}

// ── Tab: Systeemonbalans 15-min ───────────────────────────────────────────────
function TabImbalance({ date }) {
  const { data, loading, error } = useEliaData(fetchImbalance, date)

  const chart = data.map(d => ({
    ts:    fmtTs(d.timestamp || d.ts || ''),
    imbal: parseFloat((d.system_imbalance ?? 0).toFixed(2)),
    nrv:   parseFloat((d.nrv ?? 0).toFixed(2)),
    alpha: parseFloat((d.alpha ?? 0).toFixed(2)),
  }))

  const posImbal = chart.filter(r => r.imbal > 0).length
  const negImbal = chart.filter(r => r.imbal < 0).length
  const avgAlpha = chart.length
    ? (chart.reduce((s, r) => s + r.alpha, 0) / chart.length).toFixed(2)
    : '—'

  return (
    <>
      <div style={{ display:'flex', gap:16, marginBottom:16, flexWrap:'wrap' }}>
        <div className="kpi" style={{ flex:1, minWidth:120 }}>
          <div className="kpi-label">Positieve onbalans</div>
          <div className="kpi-value positive">{posImbal}</div>
          <div className="kpi-sub">kwartieren (overschot)</div>
        </div>
        <div className="kpi" style={{ flex:1, minWidth:120 }}>
          <div className="kpi-label">Negatieve onbalans</div>
          <div className="kpi-value negative">{negImbal}</div>
          <div className="kpi-sub">kwartieren (tekort)</div>
        </div>
        <div className="kpi" style={{ flex:1, minWidth:120 }}>
          <div className="kpi-label">Gem. alpha-prijs</div>
          <div className="kpi-value">{avgAlpha}</div>
          <div className="kpi-sub">€/MWh</div>
        </div>
      </div>
      {loading && <div className="loading">Elia data ophalen…</div>}
      {error   && <div className="error">⚠️ {error}</div>}
      {!loading && !error && chart.length > 0 && (
        <ResponsiveContainer width="100%" height={280}>
          <ComposedChart data={chart} margin={{ top:4, right:50, bottom:0, left:0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.25)"/>
            <XAxis dataKey="ts" tick={{ fill:'#64748b', fontSize:11 }} interval="preserveStartEnd"/>
            <YAxis yAxisId="mw" tick={{ fill:'#64748b', fontSize:11 }} unit=" MW"/>
            <YAxis yAxisId="eur" orientation="right" tick={{ fill:'#64748b', fontSize:11 }} unit=" €"
              label={{ value:'€/MWh', angle:90, position:'insideRight', fill:'#64748b', fontSize:10, dy:-30 }}/>
            <Tooltip content={<CustomTooltip/>}/>
            <Legend wrapperStyle={{ color:'#64748b', fontSize:12 }}/>
            <Bar  yAxisId="mw"  dataKey="imbal" name="Systeemonbalans" fill="#f97316" opacity={0.7} radius={[2,2,0,0]}/>
            <Line yAxisId="mw"  dataKey="nrv"   name="NRV"   stroke="#8b5cf6" dot={false} strokeWidth={1.5} strokeDasharray="4 2"/>
            <Line yAxisId="eur" dataKey="alpha"  name="Alpha" stroke="#22c55e" dot={false} strokeWidth={1.5}/>
          </ComposedChart>
        </ResponsiveContainer>
      )}
      {!loading && !error && chart.length === 0 && (
        <div style={{ background:'rgba(245,158,11,0.08)', border:'1px solid rgba(245,158,11,0.3)',
          borderRadius:10, padding:'16px 20px', color:'var(--text)' }}>
          <div style={{ fontWeight:600, marginBottom:6 }}>
            ⚠️ Geen gevalideerde data voor {date}
          </div>
          <div style={{ fontSize:13, color:'var(--muted)', lineHeight:1.6 }}>
            Dataset ods047 bevat gevalideerde 15-min onbalansdata tot en met <strong>21 mei 2024</strong>.
            Voor actuele data van vandaag, gebruik de tab <strong>⚡ Live 5-min</strong>.
          </div>
        </div>
      )}
    </>
  )
}

// ── Tab: Live 5-minuten onbalans ──────────────────────────────────────────────
function TabLive({ date }) {
  const { data, loading, error } = useEliaData(fetchLiveImbalance, date)

  const chart = data.map(d => ({
    ts:    fmtTs(d.timestamp || ''),
    imbal: parseFloat((d.system_imbalance ?? 0).toFixed(2)),
    alpha: parseFloat((d.alpha ?? 0).toFixed(2)),
  }))

  const posCount = chart.filter(r => r.imbal > 0).length
  const negCount = chart.filter(r => r.imbal < 0).length
  const maxPos   = chart.length ? Math.max(...chart.map(r => r.imbal)).toFixed(1) : '—'
  const maxNeg   = chart.length ? Math.min(...chart.map(r => r.imbal)).toFixed(1) : '—'

  return (
    <>
      <div style={{ fontSize:12, color:'var(--muted)', marginBottom:12 }}>
        Elia ods161 — 5-minuten real-time onbalans ({chart.length} meetpunten)
      </div>
      <div style={{ display:'flex', gap:16, marginBottom:16, flexWrap:'wrap' }}>
        <div className="kpi" style={{ flex:1, minWidth:120 }}>
          <div className="kpi-label">Positieve slots</div>
          <div className="kpi-value positive">{posCount}</div>
          <div className="kpi-sub">van {chart.length}</div>
        </div>
        <div className="kpi" style={{ flex:1, minWidth:120 }}>
          <div className="kpi-label">Negatieve slots</div>
          <div className="kpi-value negative">{negCount}</div>
          <div className="kpi-sub">van {chart.length}</div>
        </div>
        <div className="kpi" style={{ flex:1, minWidth:120 }}>
          <div className="kpi-label">Max overschot</div>
          <div className="kpi-value positive">{maxPos}</div>
          <div className="kpi-sub">MW</div>
        </div>
        <div className="kpi" style={{ flex:1, minWidth:120 }}>
          <div className="kpi-label">Max tekort</div>
          <div className="kpi-value negative">{maxNeg}</div>
          <div className="kpi-sub">MW</div>
        </div>
      </div>
      {loading && <div className="loading">Elia real-time data ophalen…</div>}
      {error   && <div className="error">⚠️ {error}</div>}
      {!loading && !error && chart.length > 0 && (
        <ResponsiveContainer width="100%" height={280}>
          <ComposedChart data={chart} margin={{ top:4, right:50, bottom:0, left:0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.25)"/>
            <XAxis dataKey="ts" tick={{ fill:'#64748b', fontSize:10 }} interval="preserveStartEnd"/>
            <YAxis yAxisId="mw"  tick={{ fill:'#64748b', fontSize:11 }} unit=" MW"/>
            <YAxis yAxisId="eur" orientation="right" tick={{ fill:'#64748b', fontSize:11 }}
              label={{ value:'€/MWh', angle:90, position:'insideRight', fill:'#64748b', fontSize:10, dy:-30 }}/>
            <Tooltip content={<CustomTooltip/>}/>
            <Legend wrapperStyle={{ color:'#64748b', fontSize:12 }}/>
            <Bar  yAxisId="mw"  dataKey="imbal" name="Systeemonbalans" fill="#f97316" opacity={0.6} radius={[1,1,0,0]}/>
            <Line yAxisId="eur" dataKey="alpha"  name="Alpha" stroke="#22c55e" dot={false} strokeWidth={1.5}/>
          </ComposedChart>
        </ResponsiveContainer>
      )}
      {!loading && !error && chart.length === 0 && (
        <div className="error">Geen real-time data beschikbaar — ods161 is enkel actueel beschikbaar</div>
      )}
    </>
  )
}

// ── Tab: Zon & Wind realisatie ────────────────────────────────────────────────
function TabZonWind({ date }) {
  const { data, loading, error } = useEliaData(fetchSolarWind, date)

  const chart = data.map(d => ({
    ts:       fmtTs(d.timestamp || ''),
    solar:    parseFloat((d.solar_mw ?? 0).toFixed(1)),
    wind_on:  parseFloat((d.wind_onshore_mw ?? 0).toFixed(1)),
    wind_off: parseFloat((d.wind_offshore_mw ?? 0).toFixed(1)),
  }))

  const totalSolar   = chart.reduce((s, r) => s + r.solar, 0)
  const totalWindOn  = chart.reduce((s, r) => s + r.wind_on, 0)
  const totalWindOff = chart.reduce((s, r) => s + r.wind_off, 0)
  const totalRE      = totalSolar + totalWindOn + totalWindOff

  return (
    <>
      <div className="kpi-grid" style={{ marginBottom:16 }}>
        <div className="kpi">
          <div className="kpi-label">Zonne-energie</div>
          <div className="kpi-value" style={{ color:'#f59e0b' }}>{(totalSolar/1000).toFixed(1)}</div>
          <div className="kpi-sub">GWh totaal</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Wind onshore</div>
          <div className="kpi-value" style={{ color:'#22c55e' }}>{(totalWindOn/1000).toFixed(1)}</div>
          <div className="kpi-sub">GWh totaal</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Wind offshore</div>
          <div className="kpi-value" style={{ color:'#3b82f6' }}>{(totalWindOff/1000).toFixed(1)}</div>
          <div className="kpi-sub">GWh totaal</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Totaal hernieuwbaar</div>
          <div className="kpi-value positive">{(totalRE/1000).toFixed(1)}</div>
          <div className="kpi-sub">GWh totaal</div>
        </div>
      </div>
      {loading && <div className="loading">Elia data ophalen…</div>}
      {error   && <div className="error">⚠️ {error}</div>}
      {!loading && !error && chart.length > 0 && (
        <ResponsiveContainer width="100%" height={280}>
          <AreaChart data={chart} margin={{ top:4, right:8, bottom:0, left:0 }}>
            <defs>
              <linearGradient id="solarGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.4}/><stop offset="95%" stopColor="#f59e0b" stopOpacity={0}/>
              </linearGradient>
              <linearGradient id="windOnGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#22c55e" stopOpacity={0.3}/><stop offset="95%" stopColor="#22c55e" stopOpacity={0}/>
              </linearGradient>
              <linearGradient id="windOffGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3}/><stop offset="95%" stopColor="#3b82f6" stopOpacity={0}/>
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.25)"/>
            <XAxis dataKey="ts" tick={{ fill:'#64748b', fontSize:11 }} interval="preserveStartEnd"/>
            <YAxis tick={{ fill:'#64748b', fontSize:11 }} unit=" MW"/>
            <Tooltip content={<CustomTooltip/>}/>
            <Legend wrapperStyle={{ color:'#64748b', fontSize:12 }}/>
            <Area type="monotone" dataKey="solar"    name="Zonne-energie"   stroke="#f59e0b" fill="url(#solarGrad)"   strokeWidth={2} dot={false}/>
            <Area type="monotone" dataKey="wind_on"  name="Wind onshore"    stroke="#22c55e" fill="url(#windOnGrad)"  strokeWidth={2} dot={false}/>
            <Area type="monotone" dataKey="wind_off" name="Wind offshore"   stroke="#3b82f6" fill="url(#windOffGrad)" strokeWidth={2} dot={false}/>
          </AreaChart>
        </ResponsiveContainer>
      )}
      {!loading && !error && chart.length === 0 && (
        <div className="error">Geen data beschikbaar voor geselecteerde datum</div>
      )}
    </>
  )
}

// ── Tab: Prognose vs Realisatie ───────────────────────────────────────────────
function TabPrognose({ date }) {
  const solar = useEliaData(fetchSolarForecast, date)
  const wind  = useEliaData(fetchWindForecast, date)

  const solarChart = solar.data.map(d => ({
    ts:       fmtTs(d.timestamp || ''),
    forecast: parseFloat((d.forecast_mw ?? 0).toFixed(1)),
    measured: parseFloat((d.measured_mw ?? 0).toFixed(1)),
  }))

  const windChart = wind.data.map(d => ({
    ts:           fmtTs(d.timestamp || ''),
    on_fc:        parseFloat((d.onshore_forecast_mw  ?? 0).toFixed(1)),
    on_me:        parseFloat((d.onshore_measured_mw  ?? 0).toFixed(1)),
    off_fc:       parseFloat((d.offshore_forecast_mw ?? 0).toFixed(1)),
    off_me:       parseFloat((d.offshore_measured_mw ?? 0).toFixed(1)),
  }))

  // MAE solar
  const solarMAE = solarChart.length
    ? (solarChart.reduce((s, r) => s + Math.abs(r.forecast - r.measured), 0) / solarChart.length).toFixed(1)
    : '—'

  const loading = solar.loading || wind.loading
  const error   = solar.error || wind.error

  return (
    <>
      {loading && <div className="loading">Elia prognosedata ophalen…</div>}
      {error   && <div className="error">⚠️ {error}</div>}

      {/* Zon: prognose vs gemeten */}
      {!loading && solarChart.length > 0 && (
        <div style={{ marginBottom:24 }}>
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:8 }}>
            <div style={{ fontWeight:600, color:'var(--text)', fontSize:14 }}>☀️ Zonne-energie — prognose vs realisatie</div>
            <div style={{ fontSize:12, color:'var(--muted)' }}>
              Gem. afwijking: <span style={{ color:'#f59e0b', fontWeight:700 }}>{solarMAE} MW</span>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={solarChart} margin={{ top:4, right:8, bottom:0, left:0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.25)"/>
              <XAxis dataKey="ts" tick={{ fill:'#64748b', fontSize:11 }} interval="preserveStartEnd"/>
              <YAxis tick={{ fill:'#64748b', fontSize:11 }} unit=" MW"/>
              <Tooltip content={<CustomTooltip/>}/>
              <Legend wrapperStyle={{ color:'#64748b', fontSize:12 }}/>
              <Line type="monotone" dataKey="forecast" name="Prognose"    stroke="#f59e0b" strokeDasharray="5 3" dot={false} strokeWidth={2}/>
              <Line type="monotone" dataKey="measured"  name="Realisatie" stroke="#fbbf24" dot={false} strokeWidth={2}/>
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Wind: prognose vs gemeten */}
      {!loading && windChart.length > 0 && (
        <div>
          <div style={{ fontWeight:600, color:'var(--text)', fontSize:14, marginBottom:8 }}>
            🌬️ Wind — prognose vs realisatie
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={windChart} margin={{ top:4, right:8, bottom:0, left:0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.25)"/>
              <XAxis dataKey="ts" tick={{ fill:'#64748b', fontSize:11 }} interval="preserveStartEnd"/>
              <YAxis tick={{ fill:'#64748b', fontSize:11 }} unit=" MW"/>
              <Tooltip content={<CustomTooltip/>}/>
              <Legend wrapperStyle={{ color:'#64748b', fontSize:12 }}/>
              <Line type="monotone" dataKey="on_fc"  name="Onshore prognose"  stroke="#22c55e" strokeDasharray="5 3" dot={false} strokeWidth={1.5}/>
              <Line type="monotone" dataKey="on_me"  name="Onshore realisatie" stroke="#22c55e" dot={false} strokeWidth={2}/>
              <Line type="monotone" dataKey="off_fc" name="Offshore prognose" stroke="#3b82f6" strokeDasharray="5 3" dot={false} strokeWidth={1.5}/>
              <Line type="monotone" dataKey="off_me" name="Offshore realisatie" stroke="#3b82f6" dot={false} strokeWidth={2}/>
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {!loading && !error && solarChart.length === 0 && windChart.length === 0 && (
        <div className="error">Geen prognosedata beschikbaar voor geselecteerde datum</div>
      )}
    </>
  )
}

// ── Hoofdcomponent ────────────────────────────────────────────────────────────
export default function Elia() {
  const [date,    setDate]    = useState(toDateStr(new Date()))
  const [activeTab, setActiveTab] = useState('imbal')

  return (
    <div>
      <div className="page-header">
        <div className="page-title">🌿 Elia Netwerkdata</div>
        <div className="page-sub">Belgisch transmissienet — onbalans, hernieuwbare productie en prognoses</div>
      </div>

      {/* Date selector */}
      <div className="card" style={{ display:'flex', alignItems:'center', gap:16, padding:'14px 20px', marginBottom:0 }}>
        <label style={{ color:'var(--muted)', fontSize:13 }}>Datum:</label>
        <input type="date" value={date} onChange={e => setDate(e.target.value)}
          max={toDateStr(new Date())} className="form-input" style={{ width:'auto' }}/>
        {date === toDateStr(new Date()) && (
          <span style={{ background:'rgba(59,130,246,0.12)', color:'var(--accent)', fontSize:11,
            fontWeight:600, padding:'2px 8px', borderRadius:999 }}>Vandaag</span>
        )}
        <span style={{ color:'var(--muted2)', fontSize:12, marginLeft:'auto' }}>Elia Open Data</span>
      </div>

      {/* Tab bar */}
      <div style={{
        display:'flex', gap:4, padding:'0 0 0 0', marginBottom:0,
        borderBottom:'1px solid var(--border)', background:'var(--surface)',
        borderRadius:'0 0 0 0', overflowX:'auto',
      }}>
        {TABS.map(tab => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setActiveTab(tab.id)}
            style={{
              background:'none', border:'none', cursor:'pointer',
              padding:'12px 18px', fontSize:13, fontWeight: activeTab === tab.id ? 700 : 400,
              color: activeTab === tab.id ? 'var(--accent)' : 'var(--muted)',
              borderBottom: activeTab === tab.id ? '2px solid var(--accent)' : '2px solid transparent',
              whiteSpace:'nowrap', transition:'color 0.15s',
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="card" style={{ marginTop:0, borderTop:'none', borderRadius:'0 0 12px 12px' }}>
        {activeTab === 'imbal'    && <TabImbalance date={date}/>}
        {activeTab === 'live'     && <TabLive      date={date}/>}
        {activeTab === 'zonwind'  && <TabZonWind   date={date}/>}
        {activeTab === 'prognose' && <TabPrognose  date={date}/>}
      </div>
    </div>
  )
}
