import { useEffect, useRef, useState } from 'react'
import {
  AreaChart, Area, BarChart, Bar, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine
} from 'recharts'
import { fetchDayAhead, fetchTomorrowStatus } from '../api'
import { format, parseISO, addDays, startOfDay } from 'date-fns'
import {
  BoltIcon, CalendarDaysIcon, ExclamationTriangleIcon, CheckCircleIcon,
  ClockIcon, ArrowPathIcon, BanknotesIcon,
} from '@heroicons/react/24/outline'

const Icn = ({ Icon, size = 14, style }) => (
  <Icon width={size} height={size} strokeWidth={2} style={{ display:'inline', verticalAlign:'-2px', ...style }}/>
)

const fmt  = (ts) => { try { return format(parseISO(ts), 'dd/MM HH:mm') } catch { return ts } }
const fmtH = (ts) => { try { return format(parseISO(ts), 'HH:mm') }       catch { return ts } }

const PriceTip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  const p = payload[0]?.value
  return (
    <div style={{ background:'var(--surface)', border:'1px solid var(--border)', borderRadius:8, padding:'10px 14px' }}>
      <div style={{ color:'var(--muted)', fontSize:12 }}>{label}</div>
      <div style={{ color: p < 0 ? '#ef4444' : p < 50 ? '#22c55e' : 'var(--text)', fontWeight:700, fontSize:16 }}>
        {p?.toFixed(2)} €/MWh
      </div>
    </div>
  )
}

const CombinedTip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  const colors = { vandaag:'#3b82f6', morgen:'#f59e0b' }
  const names  = { vandaag:'Vandaag', morgen:'Morgen' }
  return (
    <div style={{ background:'var(--surface)', border:'1px solid var(--border)', borderRadius:8, padding:'10px 14px', minWidth:160 }}>
      <div style={{ color:'var(--muted)', fontSize:12, marginBottom:6 }}>{label}</div>
      {payload.map(p => p.value != null && (
        <div key={p.dataKey} style={{ display:'flex', justifyContent:'space-between', gap:16,
          color: p.value < 0 ? '#ef4444' : colors[p.dataKey] ?? 'var(--text)',
          fontWeight:600, fontSize:14, marginBottom:2 }}>
          <span style={{ color:'var(--muted)', fontWeight:400 }}>{names[p.dataKey]}</span>
          {p.value.toFixed(2)} €/MWh
        </div>
      ))}
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
  const [data,           setData]           = useState([])
  const [days,           setDays]           = useState(1)
  const [rolling,        setRolling]        = useState(false)
  const [loading,        setLoading]        = useState(true)
  const [error,          setError]          = useState(null)
  const [tomorrowStatus, setTomorrowStatus] = useState(null)
  const [justArrived,    setJustArrived]    = useState(false)
  const prevAvailableRef = useRef(false)

  // Rolling mode gebruikt altijd 3 dagen data (gisteren + vandaag + morgen)
  const fetchDays = rolling ? 3 : days

  // Prijsdata ophalen
  useEffect(() => {
    setLoading(true); setError(null)
    fetchDayAhead(fetchDays)
      .then(r => setData(r.records || []))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [fetchDays])

  // 15 min auto-refresh: verse kwartierdata + Nu-lijn positie
  useEffect(() => {
    const id = setInterval(() => {
      fetchDayAhead(fetchDays)
        .then(r => setData(r.records || []))
        .catch(() => {})
    }, 15 * 60 * 1000)  // 900 000 ms — matcht ENTSO-E kwartierresolutie
    return () => clearInterval(id)
  }, [fetchDays])

  // D+1 status pollen — elke 5 min
  useEffect(() => {
    const poll = async () => {
      try {
        const status = await fetchTomorrowStatus()
        if (!prevAvailableRef.current && status.available) {
          setJustArrived(true)
          setTimeout(() => setJustArrived(false), 15_000)
          fetchDayAhead(fetchDays).then(r => setData(r.records || []))
        }
        prevAvailableRef.current = status.available
        setTomorrowStatus(status)
      } catch { /* stille fout */ }
    }
    poll()
    const id = setInterval(poll, 300_000)
    return () => clearInterval(id)
  }, [fetchDays])

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

  // D+1 status badge — toont live staat van morgen's prijzen
  const nowHour        = new Date().getHours()
  const isPollingWindow = nowHour >= 12 && nowHour < 18
  const fmtTime = (isoStr) => {
    try { return new Date(isoStr).toLocaleTimeString('nl-BE', { hour:'2-digit', minute:'2-digit' }) }
    catch { return '–' }
  }

  const statusBadge = (() => {
    if (!tomorrowStatus) return (
      <span style={{ color:'var(--muted)', fontSize:12 }}>
        ENTSO-E publiceert morgen-prijzen doorgaans na 13:00 CET
      </span>
    )
    if (tomorrowStatus.available) return (
      <span style={{ display:'flex', alignItems:'center', gap:6, color:'#22c55e', fontSize:12, fontWeight:600 }}>
        <span style={{ width:8, height:8, borderRadius:'50%', background:'#22c55e', flexShrink:0,
          animation:'pulse 2s ease-in-out infinite' }}/>
        Beschikbaar om {fmtTime(tomorrowStatus.first_available_at)}
      </span>
    )
    if (isPollingWindow) return (
      <span style={{ display:'flex', alignItems:'center', gap:6, color:'#f59e0b', fontSize:12 }}>
        <span style={{ display:'inline-block', animation:'spin 1.2s linear infinite', lineHeight:1 }}>↻</span>
        Controleren… {tomorrowStatus.checked_at ? `laatste check ${fmtTime(tomorrowStatus.checked_at)}` : ''}
      </span>
    )
    if (nowHour < 12) return (
      <span style={{ color:'var(--muted)', fontSize:12, display:'flex', alignItems:'center', gap:5 }}><Icn Icon={ClockIcon}/> Verwacht na 13:00 CET</span>
    )
    return (
      <span style={{ color:'#f97316', fontSize:12, display:'flex', alignItems:'center', gap:5 }}><Icn Icon={ExclamationTriangleIcon}/> Vertraagd — we blijven controleren</span>
    )
  })()

  // Chart data
  const chartData = data.map(d => ({
    ts:    fmt(d.timestamp),
    price: parseFloat((d.price_eur_mwh ?? 0).toFixed(2)),
  }))

  const d1ChartData = d1Records.map(d => ({
    ts:    fmtH(d.timestamp),
    price: parseFloat((d.price_eur_mwh ?? 0).toFixed(2)),
  }))

  // Vandaag's records (voor 1d gecombineerde grafiek)
  const todayStart2   = startOfDay(new Date())
  const todayRecords  = data.filter(d => {
    const ts = new Date(d.timestamp)
    return ts >= todayStart2 && ts < tomorrowStart
  })
  const todayDate = format(new Date(), 'dd/MM/yyyy')

  // Nu-indicator: afronden op 15 min voor x-as matching
  const _nowRaw   = new Date()
  const _nowRound = new Date(_nowRaw.getFullYear(), _nowRaw.getMonth(), _nowRaw.getDate(),
                             _nowRaw.getHours(), Math.floor(_nowRaw.getMinutes() / 15) * 15)
  const nowSlot1d      = format(_nowRound, 'HH:mm')
  const nowSlotRolling = format(_nowRound, 'dd/MM HH:mm')

  // Rolling horizon: 48u-venster (12u terug t.e.m. 36u vooruit)
  const rollingWindowStart = new Date(_nowRaw.getTime() - 12 * 3_600_000)
  const rollingWindowEnd   = new Date(_nowRaw.getTime() + 36 * 3_600_000)
  const rollingChartData   = data
    .filter(d => { const ts = new Date(d.timestamp); return ts >= rollingWindowStart && ts <= rollingWindowEnd })
    .map(d => ({
      ts:    format(parseISO(d.timestamp), 'dd/MM HH:mm'),
      price: parseFloat((d.price_eur_mwh ?? 0).toFixed(2)),
    }))

  // Gecombineerde 1d-data: vandaag + morgen op zelfde tijdas
  const combinedChartData = todayRecords.map((d, i) => ({
    ts:      fmtH(d.timestamp),
    vandaag: parseFloat((d.price_eur_mwh ?? 0).toFixed(2)),
    morgen:  d1Records[i] != null
               ? parseFloat((d1Records[i].price_eur_mwh ?? 0).toFixed(2))
               : undefined,
  }))

  return (
    <div>
      <div className="page-header" style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
        <div>
          <div className="page-title" style={{ display:'flex', alignItems:'center', gap:8 }}><BoltIcon width={22} height={22} strokeWidth={2}/> Dag-ahead Prijzen</div>
          <div className="page-sub">ENTSO-E Belgische elektriciteitsmarkt</div>
        </div>
        <div style={{ textAlign:'right', paddingTop:2 }}>
          <div style={{ color:'var(--text)', fontWeight:600, fontSize:14, display:'flex', alignItems:'center', gap:6, justifyContent:'flex-end' }}>
            <CalendarDaysIcon width={15} height={15} strokeWidth={2}/> {new Date().toLocaleDateString('nl-BE', { weekday:'long', day:'numeric', month:'long', year:'numeric' })}
          </div>
          <div style={{ color:'var(--muted)', fontSize:12, marginTop:2 }}>
            Periode: laatste {days} dag{days !== 1 ? 'en' : ''}
          </div>
        </div>
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
          <div className="kpi-sub" style={{ display:'flex', alignItems:'center', gap:4 }}>gratis laden <BoltIcon width={12} height={12} strokeWidth={2}/></div>
        </div>
      </div>

      {/* ── D+1 Intelligence Panel ── */}
      {!loading && !error && (
        <div className="card" style={{
          border:     hasTomorrow ? '1px solid rgba(59,130,246,0.35)' : '1px solid rgba(255,255,255,0.06)',
          background: hasTomorrow ? 'rgba(59,130,246,0.04)' : 'transparent',
        }}>

          {/* "Net beschikbaar" notificatie — verdwijnt na 15s */}
          {justArrived && (
            <div style={{
              background:'rgba(34,197,94,0.10)', border:'1px solid rgba(34,197,94,0.35)',
              borderRadius:8, padding:'12px 16px', marginBottom:16,
              display:'flex', alignItems:'center', gap:12,
            }}>
              <CheckCircleIcon width={22} height={22} strokeWidth={2} style={{ color:'#22c55e', flexShrink:0 }}/>
              <div>
                <div style={{ color:'#22c55e', fontWeight:700, fontSize:14 }}>
                  Morgen-prijzen zijn net beschikbaar!
                </div>
                <div style={{ color:'var(--muted)', fontSize:12, marginTop:2 }}>
                  Data automatisch herladen — D+1 intelligentie is nu zichtbaar.
                </div>
              </div>
            </div>
          )}

          <div style={{ marginBottom: hasTomorrow ? 16 : 0 }}>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
              <div className="card-title" style={{ margin:0 }}>
                {hasTomorrow
                  ? `🎯 D+1 Prijsintelligentie — ${d1Date}`
                  : '⏳ D+1 nog niet beschikbaar'}
              </div>
              {/* Badge naast titel enkel als beschikbaar */}
              {hasTomorrow && statusBadge}
            </div>
            {/* Badge onder titel als nog niet beschikbaar */}
            {!hasTomorrow && (
              <div style={{ marginTop:6 }}>{statusBadge}</div>
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
                  <div key={lbl} style={{ background:'var(--bg)', borderRadius:8,
                    padding:'10px 12px', border:'1px solid var(--border)' }}>
                    <div style={{ color:'var(--muted)', fontSize:11 }}>{lbl}</div>
                    <div style={{ color:clr, fontWeight:700, fontSize:15, marginTop:3 }}>{val}</div>
                  </div>
                ))}
              </div>

              {/* Best charge / sell moments */}
              <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10, marginBottom:16 }}>
                {bestCharge && (
                  <div style={{ background:'rgba(34,197,94,0.08)', borderRadius:8, padding:'12px 14px',
                    border:'1px solid rgba(34,197,94,0.22)' }}>
                    <div style={{ color:'var(--muted)', fontSize:11, marginBottom:5, display:'flex', alignItems:'center', gap:4 }}><BoltIcon width={13} height={13} strokeWidth={2}/> Beste laaduur</div>
                    <div style={{ color:'#22c55e', fontWeight:700, fontSize:17 }}>
                      {fmtH(bestCharge.ts)} – {fmtH(bestCharge.end)}
                    </div>
                    <div style={{ color:'var(--muted)', fontSize:12, marginTop:3 }}>
                      gem. {bestCharge.avg.toFixed(1)} €/MWh
                    </div>
                  </div>
                )}
                {bestSell && (
                  <div style={{ background:'rgba(249,115,22,0.08)', borderRadius:8, padding:'12px 14px',
                    border:'1px solid rgba(249,115,22,0.22)' }}>
                    <div style={{ color:'var(--muted)', fontSize:11, marginBottom:5, display:'flex', alignItems:'center', gap:4 }}><BanknotesIcon width={13} height={13} strokeWidth={2}/> Beste ontlaaduur</div>
                    <div style={{ color:'#f97316', fontWeight:700, fontSize:17 }}>
                      {fmtH(bestSell.ts)} – {fmtH(bestSell.end)}
                    </div>
                    <div style={{ color:'var(--muted)', fontSize:12, marginTop:3 }}>
                      gem. {bestSell.avg.toFixed(1)} €/MWh
                    </div>
                  </div>
                )}
              </div>

              {/* D+1 mini bar chart — color-coded by price */}
              <div style={{ color:'var(--muted)', fontSize:11, marginBottom:6 }}>
                Kwartier-prijzen morgen (kleurcodering: 🟢 goedkoop · 🔵 midden · 🟡 duur · 🟠 piek · 🔴 negatief)
              </div>
              <ResponsiveContainer width="100%" height={170}>
                <BarChart data={d1ChartData} margin={{ top:2, right:8, bottom:0, left:0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.25)"/>
                  <XAxis dataKey="ts" tick={{ fill:'#64748b', fontSize:10 }} interval={7}/>
                  <YAxis tick={{ fill:'#64748b', fontSize:10 }} unit=" €"/>
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

      {/* ── Prijsverloop ── */}
      <div className="card">
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:16 }}>
          <div>
            <div className="card-title" style={{ margin:0, display:'flex', alignItems:'center', gap:6 }}>
              {rolling     ? <><ArrowPathIcon width={15} height={15} strokeWidth={2}/> Rolling horizon — 48u venster</>
               : days === 1 ? <><BoltIcon width={15} height={15} strokeWidth={2}/> Vandaag — {todayDate}</>
               :              'Historisch prijsverloop'}
            </div>
            {rolling && (
              <div style={{ fontSize:12, color:'var(--muted)', marginTop:3 }}>
                12u verleden · <span style={{ color:'#f59e0b' }}>▶ Nu</span> · 36u toekomst — verschuift mee met de klok
              </div>
            )}
            {!rolling && days === 1 && hasTomorrow && (
              <div style={{ fontSize:12, color:'var(--muted)', marginTop:3 }}>
                <span style={{ color:'#f59e0b', marginRight:4 }}>●</span>
                Morgen — {d1Date} (overlay ter vergelijking)
              </div>
            )}
          </div>
          <div style={{ display:'flex', flexWrap:'wrap', gap:6, justifyContent:'flex-end', maxWidth:340 }}>
            {[
              { v:1, l:'1d' }, { v:3, l:'3d' }, { v:7, l:'7d' }, { v:14, l:'14d' },
              { v:30, l:'30d' }, { v:90, l:'90d' }, { v:180, l:'180d' }, { v:365, l:'1j' },
            ].map(({ v, l }) => (
              <button key={v} onClick={() => { setDays(v); setRolling(false) }} className="btn" style={{
                padding:'4px 10px', fontSize:11,
                background: !rolling && days===v ? 'rgba(59,130,246,0.2)' : 'transparent',
                border:`1px solid ${!rolling && days===v ? '#3b82f6' : 'var(--border)'}`,
                color: !rolling && days===v ? '#3b82f6' : 'var(--muted)', borderRadius:6,
              }}>{l}</button>
            ))}
            <button onClick={() => setRolling(r => !r)} className="btn" style={{
              padding:'4px 10px', fontSize:11,
              background: rolling ? 'rgba(249,115,22,0.2)' : 'transparent',
              border:`1px solid ${rolling ? '#f97316' : 'var(--border)'}`,
              color: rolling ? '#f97316' : 'var(--muted)', borderRadius:6,
            }}>↻ 48u</button>
          </div>
        </div>
        {loading && <div className="loading">Data ophalen…</div>}
        {error   && <div className="error" style={{ display:'flex', alignItems:'center', gap:6 }}><ExclamationTriangleIcon width={15} height={15} strokeWidth={2}/> {error}</div>}
        {!loading && !error && (
          <ResponsiveContainer width="100%" height={320}>
            {rolling ? (
              <AreaChart data={rollingChartData} margin={{ top:4, right:8, bottom:0, left:0 }}>
                <defs>
                  <linearGradient id="rollingGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#3b82f6" stopOpacity={0.3}/>
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.25)"/>
                <XAxis dataKey="ts" tick={{ fill:'#64748b', fontSize:10 }} interval={7}/>
                <YAxis tick={{ fill:'#64748b', fontSize:11 }} unit=" €"/>
                <Tooltip content={<PriceTip/>}/>
                <ReferenceLine y={0} stroke="#ef4444" strokeDasharray="4 4"/>
                <ReferenceLine x={nowSlotRolling} stroke="#f97316" strokeWidth={2}
                  label={{ value:'▶ Nu', position:'insideTopRight', fill:'#f97316', fontSize:11, fontWeight:700 }}/>
                <Area type="monotone" dataKey="price" stroke="#3b82f6" strokeWidth={2}
                  fill="url(#rollingGrad)" dot={false}/>
              </AreaChart>
            ) : days === 1 ? (
              <AreaChart data={combinedChartData} margin={{ top:4, right:8, bottom:0, left:0 }}>
                <defs>
                  <linearGradient id="todayGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#3b82f6" stopOpacity={0.3}/>
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}/>
                  </linearGradient>
                  <linearGradient id="morgenGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#f59e0b" stopOpacity={0.22}/>
                    <stop offset="95%" stopColor="#f59e0b" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.25)"/>
                <XAxis dataKey="ts" tick={{ fill:'#64748b', fontSize:11 }} interval={7}/>
                <YAxis tick={{ fill:'#64748b', fontSize:11 }} unit=" €"/>
                <Tooltip content={<CombinedTip/>}/>
                <ReferenceLine y={0} stroke="#ef4444" strokeDasharray="4 4"/>
                <ReferenceLine x={nowSlot1d} stroke="#f97316" strokeWidth={2}
                  label={{ value:'▶ Nu', position:'insideTopRight', fill:'#f97316', fontSize:11, fontWeight:700 }}/>
                <Area type="monotone" dataKey="vandaag" name="Vandaag"
                  stroke="#3b82f6" strokeWidth={2} fill="url(#todayGrad)" dot={false}/>
                {hasTomorrow && (
                  <Area type="monotone" dataKey="morgen" name="Morgen"
                    stroke="#f59e0b" strokeWidth={2} fill="url(#morgenGrad)"
                    dot={false} strokeDasharray="5 3"/>
                )}
              </AreaChart>
            ) : (
              <AreaChart data={chartData} margin={{ top:4, right:8, bottom:0, left:0 }}>
                <defs>
                  <linearGradient id="priceGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#3b82f6" stopOpacity={0.3}/>
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.25)"/>
                <XAxis dataKey="ts" tick={{ fill:'#64748b', fontSize:11 }} interval="preserveStartEnd"/>
                <YAxis tick={{ fill:'#64748b', fontSize:11 }} unit=" €"/>
                <Tooltip content={<PriceTip/>}/>
                <ReferenceLine y={0} stroke="#ef4444" strokeDasharray="4 4"/>
                <Area type="monotone" dataKey="price" stroke="#3b82f6" strokeWidth={2}
                  fill="url(#priceGrad)" dot={false}/>
              </AreaChart>
            )}
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
