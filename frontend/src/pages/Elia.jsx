import { useEffect, useState } from 'react'
import {
  LineChart, Line, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend
} from 'recharts'
import { fetchImbalance, fetchSolarWind } from '../api'
import { format, parseISO } from 'date-fns'

const toDateStr = (d) => format(d, 'yyyy-MM-dd')

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background:'var(--surface)', border:'1px solid var(--border)', borderRadius:8, padding:'10px 14px' }}>
      <div style={{ color:'var(--muted)', fontSize:12, marginBottom:4 }}>{label}</div>
      {payload.map((p,i) => (
        <div key={i} style={{ color:p.stroke||p.fill, fontWeight:600, fontSize:13 }}>
          {p.name}: {typeof p.value === 'number' ? p.value.toFixed(2) : p.value}
        </div>
      ))}
    </div>
  )
}

export default function Elia() {
  const [date, setDate]               = useState(toDateStr(new Date()))
  const [imbalData, setImbalData]     = useState([])
  const [swData, setSwData]           = useState([])
  const [loadingImbal, setLoadingImbal] = useState(false)
  const [loadingSW, setLoadingSW]     = useState(false)
  const [errorImbal, setErrorImbal]   = useState(null)
  const [errorSW, setErrorSW]         = useState(null)

  useEffect(() => {
    setLoadingImbal(true); setErrorImbal(null)
    fetchImbalance(date)
      .then(r => setImbalData(r.data || []))
      .catch(e => setErrorImbal(e.message))
      .finally(() => setLoadingImbal(false))

    setLoadingSW(true); setErrorSW(null)
    fetchSolarWind(date)
      .then(r => setSwData(r.data || []))
      .catch(e => setErrorSW(e.message))
      .finally(() => setLoadingSW(false))
  }, [date])

  const fmtTs = (ts) => { try { return format(parseISO(ts), 'HH:mm') } catch { return ts } }

  const imbalChart = imbalData.map(d => ({
    ts: fmtTs(d.timestamp || d.ts || ''),
    imbal: parseFloat((d.system_imbalance ?? d.imbalance ?? 0).toFixed(2)),
    nrv: parseFloat((d.nrv ?? 0).toFixed(2)),
  }))

  const swChart = swData.map(d => ({
    ts: fmtTs(d.timestamp || d.ts || ''),
    solar: parseFloat((d.solar_mw ?? d.solar ?? 0).toFixed(1)),
    wind_on: parseFloat((d.wind_onshore_mw ?? d.wind_on ?? 0).toFixed(1)),
    wind_off: parseFloat((d.wind_offshore_mw ?? d.wind_off ?? 0).toFixed(1)),
  }))

  const totalSolar  = swChart.reduce((s,r) => s + r.solar, 0)
  const totalWindOn = swChart.reduce((s,r) => s + r.wind_on, 0)
  const totalWindOf = swChart.reduce((s,r) => s + r.wind_off, 0)
  const posImbal    = imbalChart.filter(r => r.imbal > 0).length
  const negImbal    = imbalChart.filter(r => r.imbal < 0).length

  return (
    <div>
      <div className="page-header">
        <div className="page-title">🌿 Elia Netwerkdata</div>
        <div className="page-sub">Belgisch transmissienet — onbalans en hernieuwbare productie</div>
      </div>

      {/* Date selector */}
      <div className="card" style={{ display:'flex', alignItems:'center', gap:16, padding:'14px 20px' }}>
        <label style={{ color:'var(--muted)', fontSize:13 }}>Datum:</label>
        <input type="date" value={date} onChange={e => setDate(e.target.value)}
          max={toDateStr(new Date())} className="form-input" style={{ width:'auto' }}/>
        <span style={{ color:'var(--muted2)', fontSize:12 }}>Elia open data — 1 dag resolutie</span>
        {date === toDateStr(new Date()) && (
          <span style={{ background:'rgba(59,130,246,0.12)', color:'var(--accent)', fontSize:11,
            fontWeight:600, padding:'2px 8px', borderRadius:999 }}>Vandaag</span>
        )}
      </div>

      {/* Imbalance section */}
      <div className="card">
        <div className="card-title">⚖️ Systeemonbalans (NRV)</div>

        <div style={{ display:'flex', gap:24, marginBottom:16 }}>
          <div className="kpi" style={{ flex:1 }}>
            <div className="kpi-label">Positieve onbalans</div>
            <div className="kpi-value positive">{posImbal}</div>
            <div className="kpi-sub">kwartieren (overschot)</div>
          </div>
          <div className="kpi" style={{ flex:1 }}>
            <div className="kpi-label">Negatieve onbalans</div>
            <div className="kpi-value negative">{negImbal}</div>
            <div className="kpi-sub">kwartieren (tekort)</div>
          </div>
        </div>

        {loadingImbal && <div className="loading">Elia data ophalen…</div>}
        {errorImbal   && <div className="error">⚠️ {errorImbal}</div>}
        {!loadingImbal && !errorImbal && imbalChart.length > 0 && (
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={imbalChart} margin={{ top:4, right:8, bottom:0, left:0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.25)"/>
              <XAxis dataKey="ts" tick={{ fill:'#64748b', fontSize:11 }} interval="preserveStartEnd"/>
              <YAxis tick={{ fill:'#64748b', fontSize:11 }} unit=" MW"/>
              <Tooltip content={<CustomTooltip/>}/>
              <Legend wrapperStyle={{ color:'#64748b', fontSize:12 }}/>
              <Line type="monotone" dataKey="imbal" name="Systeem onbalans" stroke="#f97316" dot={false} strokeWidth={2}/>
              <Line type="monotone" dataKey="nrv" name="NRV" stroke="#8b5cf6" dot={false} strokeWidth={1.5} strokeDasharray="4 2"/>
            </LineChart>
          </ResponsiveContainer>
        )}
        {!loadingImbal && !errorImbal && imbalChart.length === 0 && (
          <div className="error">Geen data beschikbaar voor {date}</div>
        )}
      </div>

      {/* Solar + Wind section */}
      <div className="card">
        <div className="card-title">☀️🌬️ Zon & Wind Productie</div>

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
            <div className="kpi-value" style={{ color:'#3b82f6' }}>{(totalWindOf/1000).toFixed(1)}</div>
            <div className="kpi-sub">GWh totaal</div>
          </div>
          <div className="kpi">
            <div className="kpi-label">Totaal hernieuwbaar</div>
            <div className="kpi-value positive">{((totalSolar+totalWindOn+totalWindOf)/1000).toFixed(1)}</div>
            <div className="kpi-sub">GWh totaal</div>
          </div>
        </div>

        {loadingSW && <div className="loading">Elia data ophalen…</div>}
        {errorSW   && <div className="error">⚠️ {errorSW}</div>}
        {!loadingSW && !errorSW && swChart.length > 0 && (
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={swChart} margin={{ top:4, right:8, bottom:0, left:0 }}>
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
              <Area type="monotone" dataKey="solar" name="Zonne-energie" stroke="#f59e0b" fill="url(#solarGrad)" strokeWidth={2} dot={false}/>
              <Area type="monotone" dataKey="wind_on" name="Wind onshore" stroke="#22c55e" fill="url(#windOnGrad)" strokeWidth={2} dot={false}/>
              <Area type="monotone" dataKey="wind_off" name="Wind offshore" stroke="#3b82f6" fill="url(#windOffGrad)" strokeWidth={2} dot={false}/>
            </AreaChart>
          </ResponsiveContainer>
        )}
        {!loadingSW && !errorSW && swChart.length === 0 && (
          <div className="error">Geen data beschikbaar voor {date}</div>
        )}
      </div>
    </div>
  )
}
