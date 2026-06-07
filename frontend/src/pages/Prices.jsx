import { useEffect, useState } from 'react'
import {
  AreaChart, Area, BarChart, Bar, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine
} from 'recharts'
import { fetchDayAhead } from '../api'
import { format, parseISO, addDays, startOfDay } from 'date-fns'

const fmt  = (ts) => { try { return format(parseISO(ts), 'dd/MM HH:mm') } catch { return ts } }
const fmtH = (ts) => { try { return format(parseISO(ts), 'HH:mm') }       catch { return ts } }

const PriceTip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  const p = payload[0]?.value
  return (
    <div style={{ background:'#1a1d27', border:'1px solid #2a2d3e', borderRadius:8, padding:'10px 14px' }}>
      <div style={{ color:'#8892a4', fontSize:12 }}>{label}</div>
      <div style={{ color: p < 0 ? '#ef4444' : p < 50 ? '#22c55e' : '#e2e8f0', fontWeight:700, fontSize:16 }}>
        {p?.toFixed(2)} €/MWh
      </div>
    </div>
  )
}

function barColor(p) {
  if (p < 0)   return '#ef4444'
  if (p < 40)  return '#22c55e'
  if (p < 80)  return '#3b82f6'
  if (p < 120) return '#f59e0b'
  return '#f97316'
}

export default function Prices() {
  const [data,    setData]    = useState([])
  const [days,    setDays]    = useState(7)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState(null)

  useEffect(() => {
    setLoading(true); setError(null)
    fetchDayAhead(days)
      .then(r => setData(r.records || []))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [days])

  // Overall stats
  const prices   = data.map(p => p.price_eur_mwh ?? 0)
  const minP     = prices.length ? Math.min(...prices) : 0
  const maxP     = prices.length ? Math.max(...prices) : 0
  const avgP     = prices.length ? prices.reduce((a,b) => a+b, 0) / prices.length : 0
  const negCount = prices.filter(p => p < 0).length

  // D+1 detection — split on midnight tomorrow
  const tomorrowStart = startOfDay(addDays(new Date(), 1))
  const dayAfterStart = startOfDay(addDays(new Date(), 2))

  const d1Records = data.filter(d => {
    const ts = new Date(d.timestamp)
    return ts >= tomorrowStart && ts < dayAfterStart
  })
  const hasTomorrow = d1Records.length >= 4

  // D+1 stats
  const d1Prices = d1Records.map(p => p.price_eur_mwh ?? 0)
  const d1Min    = d1Prices.length ? Math.min(...d1Prices) : 0
  const d1Max    = d1Prices.length ? Math.max(...d1Prices) : 0
  const d1Avg    = d1Prices.length ? d1Prices.reduce((a,b) => a+b, 0) / d1Prices.length : 0
  const d1Neg    = d1Prices.filter(p => p < 0).length
  const d1Date   = d1Records.length ? format(parseISO(d1Records[0].timestamp), 'dd/MM/yyyy') : ''

  // Best consecutive hour (4 slots) to charge / sell
  let bestCharge = null, bestSell = null
  if (d1Records.length >= 4) {
    let minAvg = Infinity, maxAvg = -Infinity
    for (let i = 0; i <= d1Records.length - 4; i++) {
      const avg = (d1Prices[i] + d1Prices[i+1] + d1Prices[i+2] + d1Prices[i+3]) / 4
      if (avg < minAvg) { minAvg = avg; bestCharge = { ts: d1Records[i].timestamp, end: d1Records[Math.min(i+4, d1Records.length-1)].timestamp, avg } }
      if (avg > maxAvg) { maxAvg = avg; bestSell   = { ts: d1Records[i].timestamp, end: d1Records[Math.min(i+4, d1Records.length-1)].timestamp, avg } }
    }
  }

  // Chart data
  const chartData = data.map(d => ({
    ts:    fmt(d.timestamp),
    price: parseFloat((d.price_eur_mwh ?? 0).toFixed(2)),
  }))

  const d1ChartData = d1Records.map(d => ({
    ts:    fmtH(d.timestamp),
    price: parseFloat((d.price_eur_mwh ?? 0).toFixed(2)),
  }))

  return (
    <div>
      <div className="page-header">
        <div className="page-title">⚡ Dag-ahead Prijzen</div>
        <div className="page-sub">ENTSO-E Belgische elektriciteitsmarkt</div>
      </div>

      {/* Overall KPIs */}
      <div className="kpi-grid">
        <div className="kpi">
          <div className="kpi-label">Gemiddelde</div>
          <div className={`kpi-value ${avgP < 0 ? 'negative' : 'neutral'}`}>{avgP.toFixed(1)}</div>
          <div className="kpi-sub">€/MWh</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Minimum</div>
          <div className={`kpi-value ${minP < 0 ? 'negative' : 'positive'}`}>{minP.toFixed(1)}</div>
          <div className="kpi-sub">€/MWh</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Maximum</div>
          <div className="kpi-value" style={{ color:'#f97316' }}>{maxP.toFixed(1)}</div>
          <div className="kpi-sub">€/MWh</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Negatieve uren</div>
          <div className={`kpi-value ${negCount > 0 ? 'positive' : 'neutral'}`}>{negCount}</div>
          <div className="kpi-sub">gratis laden 🔋</div>
        </div>
      </div>

      {/* ── D+1 Intelligence Panel ── */}
      {!loading && !error && (
        <div className="card" style={{
          border:     hasTomorrow ? '1px solid rgba(59,130,246,0.35)' : '1px solid rgba(255,255,255,0.06)',
          background: hasTomorrow ? 'rgba(59,130,246,0.04)' : 'transparent',
        }}>
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center',
            marginBottom: hasTomorrow ? 16 : 0 }}>
            <div className="card-title" style={{ margin:0 }}>
              {hasTomorrow
                ? `🎯 D+1 Prijsintelligentie — ${d1Date}`
                : '⏳ D+1 nog niet beschikbaar'}
            </div>
            {!hasTomorrow && (
              <span style={{ color:'#4b5563', fontSize:12 }}>
                ENTSO-E publiceert morgen-prijzen doorgaans na 13:00 CET
              </span>
            )}
          </div>

          {hasTomorrow && (
            <>
              {/* D+1 stats */}
              <div style={{ display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:10, marginBottom:16 }}>
                {[
                  ['Gem. morgen',    `${d1Avg.toFixed(1)} €/MWh`, d1Avg < avgP ? '#22c55e' : '#f59e0b'],
                  ['Min morgen',     `${d1Min.toFixed(1)} €/MWh`, d1Min < 0 ? '#ef4444' : '#22c55e'],
                  ['Max morgen',     `${d1Max.toFixed(1)} €/MWh`, '#f97316'],
                  ['Negatief morgen',`${d1Neg} kwartier${d1Neg !== 1 ? 'en' : ''}`, d1Neg > 0 ? '#22c55e' : '#4b5563'],
                ].map(([lbl, val, clr]) => (
                  <div key={lbl} style={{ background:'rgba(255,255,255,0.03)', borderRadius:8,
                    padding:'10px 12px', border:'1px solid rgba(255,255,255,0.06)' }}>
                    <div style={{ color:'#6b7280', fontSize:11 }}>{lbl}</div>
                    <div style={{ color:clr, fontWeight:700, fontSize:15, marginTop:3 }}>{val}</div>
                  </div>
                ))}
              </div>

              {/* Best charge / sell moments */}
              <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10, marginBottom:16 }}>
                {bestCharge && (
                  <div style={{ background:'rgba(34,197,94,0.08)', borderRadius:8, padding:'12px 14px',
                    border:'1px solid rgba(34,197,94,0.22)' }}>
                    <div style={{ color:'#4b5563', fontSize:11, marginBottom:5 }}>🔋 Beste laaduur</div>
                    <div style={{ color:'#22c55e', fontWeight:700, fontSize:17 }}>
                      {fmtH(bestCharge.ts)} – {fmtH(bestCharge.end)}
                    </div>
                    <div style={{ color:'#374151', fontSize:12, marginTop:3 }}>
                      gem. {bestCharge.avg.toFixed(1)} €/MWh
                    </div>
                  </div>
                )}
                {bestSell && (
                  <div style={{ background:'rgba(249,115,22,0.08)', borderRadius:8, padding:'12px 14px',
                    border:'1px solid rgba(249,115,22,0.22)' }}>
                    <div style={{ color:'#4b5563', fontSize:11, marginBottom:5 }}>💰 Beste ontlaaduur</div>
                    <div style={{ color:'#f97316', fontWeight:700, fontSize:17 }}>
                      {fmtH(bestSell.ts)} – {fmtH(bestSell.end)}
                    </div>
                    <div style={{ color:'#374151', fontSize:12, marginTop:3 }}>
                      gem. {bestSell.avg.toFixed(1)} €/MWh
                    </div>
                  </div>
                )}
              </div>

              {/* D+1 mini bar chart — color-coded by price */}
              <div style={{ color:'#4b5563', fontSize:11, marginBottom:6 }}>
                Kwartier-prijzen morgen (kleurcodering: 🟢 goedkoop · 🔵 midden · 🟡 duur · 🟠 piek · 🔴 negatief)
              </div>
              <ResponsiveContainer width="100%" height={170}>
                <BarChart data={d1ChartData} margin={{ top:2, right:8, bottom:0, left:0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e2130"/>
                  <XAxis dataKey="ts" tick={{ fill:'#8892a4', fontSize:10 }} interval={7}/>
                  <YAxis tick={{ fill:'#8892a4', fontSize:10 }} unit=" €"/>
                  <Tooltip content={<PriceTip/>}/>
                  <ReferenceLine y={0} stroke="#ef4444" strokeDasharray="3 3"/>
                  <Bar dataKey="price" radius={[2,2,0,0]}>
                    {d1ChartData.map((e, i) => (
                      <Cell key={i} fill={barColor(e.price)}/>
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </>
          )}
        </div>
      )}

      {/* ── Historisch prijsverloop ── */}
      <div className="card">
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:16 }}>
          <div className="card-title" style={{ margin:0 }}>Historisch prijsverloop</div>
          <div style={{ display:'flex', gap:8 }}>
            {[1,3,7,14,30].map(d => (
              <button key={d} onClick={() => setDays(d)} className="btn" style={{
                padding:'4px 12px', fontSize:12,
                background: days===d ? 'rgba(59,130,246,0.2)' : 'transparent',
                border:`1px solid ${days===d ? '#3b82f6' : '#2a2d3e'}`,
                color: days===d ? '#3b82f6' : '#8892a4', borderRadius:6,
              }}>
                {d}d
              </button>
            ))}
          </div>
        </div>
        {loading && <div className="loading">Data ophalen…</div>}
        {error   && <div className="error">⚠️ {error}</div>}
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
              <Tooltip content={<PriceTip/>}/>
              <ReferenceLine y={0} stroke="#ef4444" strokeDasharray="4 4"/>
              <Area type="monotone" dataKey="price" stroke="#3b82f6" strokeWidth={2}
                fill="url(#priceGrad)" dot={false}/>
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* ── Detailtabel ── */}
      {!loading && !error && chartData.length > 0 && (
        <div className="card">
          <div className="card-title">Detailtabel (eerste 48 uur)</div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Tijdstip</th><th>Prijs (€/MWh)</th><th>Signaal</th></tr></thead>
              <tbody>
                {chartData.slice(0,48).map((row, i) => (
                  <tr key={i}>
                    <td>{row.ts}</td>
                    <td style={{ color: row.price<0 ? '#ef4444' : row.price<50 ? '#22c55e' : '#e2e8f0',
                      fontWeight:600 }}>{row.price}</td>
                    <td>
                      {row.price < 0   ? <span className="badge badge-done">🔋 Laden</span>
                     : row.price > 150 ? <span className="badge badge-failed">💸 Verkopen</span>
                     :                   <span className="badge badge-pending">⏸ Wacht</span>}
                    </td>
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
