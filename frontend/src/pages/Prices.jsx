import { useEffect, useState } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine
} from 'recharts'
import { fetchDayAhead } from '../api'
import { format, parseISO } from 'date-fns'

const fmt = (ts) => {
  try { return format(parseISO(ts), 'dd/MM HH:mm') } catch { return ts }
}

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  const price = payload[0]?.value
  return (
    <div style={{ background: '#1a1d27', border: '1px solid #2a2d3e', borderRadius: 8, padding: '10px 14px' }}>
      <div style={{ color: '#8892a4', fontSize: 12 }}>{label}</div>
      <div style={{ color: price < 0 ? '#ef4444' : price < 50 ? '#22c55e' : '#e2e8f0', fontWeight: 700, fontSize: 16 }}>
        {price?.toFixed(2)} €/MWh
      </div>
    </div>
  )
}

export default function Prices() {
  const [data, setData] = useState([])
  const [days, setDays] = useState(7)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    setLoading(true); setError(null)
    fetchDayAhead(days)
      .then(r => setData(r.records || []))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [days])

  const prices = data.map(p => p.price_eur_mwh ?? p.price ?? 0)
  const minP = prices.length ? Math.min(...prices) : 0
  const maxP = prices.length ? Math.max(...prices) : 0
  const avgP = prices.length ? prices.reduce((a, b) => a + b, 0) / prices.length : 0
  const negCount = prices.filter(p => p < 0).length

  const chartData = data.map(d => ({
    ts: fmt(d.timestamp),
    price: parseFloat((d.price_eur_mwh ?? d.price ?? 0).toFixed(2)),
  }))

  return (
    <div>
      <div className="page-header">
        <div className="page-title">⚡ Dag-ahead Prijzen</div>
        <div className="page-sub">ENTSO-E Belgische elektriciteitsmarkt</div>
      </div>

      <div className="kpi-grid">
        <div className="kpi"><div className="kpi-label">Gemiddelde</div>
          <div className={`kpi-value ${avgP < 0 ? 'negative' : 'neutral'}`}>{avgP.toFixed(1)}</div>
          <div className="kpi-sub">€/MWh</div></div>
        <div className="kpi"><div className="kpi-label">Minimum</div>
          <div className={`kpi-value ${minP < 0 ? 'negative' : 'positive'}`}>{minP.toFixed(1)}</div>
          <div className="kpi-sub">€/MWh</div></div>
        <div className="kpi"><div className="kpi-label">Maximum</div>
          <div className="kpi-value" style={{ color: '#f97316' }}>{maxP.toFixed(1)}</div>
          <div className="kpi-sub">€/MWh</div></div>
        <div className="kpi"><div className="kpi-label">Negatieve uren</div>
          <div className={`kpi-value ${negCount > 0 ? 'positive' : 'neutral'}`}>{negCount}</div>
          <div className="kpi-sub">gratis laden 🔋</div></div>
      </div>

      <div className="card">
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:16 }}>
          <div className="card-title" style={{ margin:0 }}>Prijsverloop</div>
          <div style={{ display:'flex', gap:8 }}>
            {[1,3,7,14,30].map(d => (
              <button key={d} onClick={() => setDays(d)} className="btn" style={{
                padding:'4px 12px', fontSize:12,
                background: days===d ? 'rgba(59,130,246,0.2)' : 'transparent',
                border:`1px solid ${days===d ? '#3b82f6' : '#2a2d3e'}`,
                color: days===d ? '#3b82f6' : '#8892a4', borderRadius:6 }}>
                {d}d
              </button>
            ))}
          </div>
        </div>
        {loading && <div className="loading">Data ophalen…</div>}
        {error  && <div className="error">⚠️ {error}</div>}
        {!loading && !error && (
          <ResponsiveContainer width="100%" height={320}>
            <AreaChart data={chartData} margin={{ top:4, right:8, bottom:0, left:0 }}>
              <defs>
                <linearGradient id="priceGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#3b82f6" stopOpacity={0.3}/>
                  <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}/>
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e"/>
              <XAxis dataKey="ts" tick={{ fill:'#8892a4', fontSize:11 }} interval="preserveStartEnd"/>
              <YAxis tick={{ fill:'#8892a4', fontSize:11 }} unit=" €"/>
              <Tooltip content={<CustomTooltip/>}/>
              <ReferenceLine y={0} stroke="#ef4444" strokeDasharray="4 4"/>
              <Area type="monotone" dataKey="price" stroke="#3b82f6" strokeWidth={2} fill="url(#priceGrad)" dot={false}/>
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>

      {!loading && !error && chartData.length > 0 && (
        <div className="card">
          <div className="card-title">Detailtabel (eerste 48 uur)</div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Tijdstip</th><th>Prijs (€/MWh)</th><th>Signaal</th></tr></thead>
              <tbody>
                {chartData.slice(0,48).map((row,i) => (
                  <tr key={i}>
                    <td>{row.ts}</td>
                    <td style={{ color: row.price<0 ? '#ef4444' : row.price<50 ? '#22c55e' : '#e2e8f0', fontWeight:600 }}>{row.price}</td>
                    <td>{row.price<0 ? <span className="badge badge-done">🔋 Laden</span>
                          : row.price>150 ? <span className="badge badge-failed">💸 Verkopen</span>
                          : <span className="badge badge-pending">⏸ Wacht</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
