import { useState, useEffect, useRef, useMemo } from 'react'
import {
  BarChart, Bar, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, ComposedChart,
  ReferenceLine,
} from 'recharts'
import { runOptimization, pollJob, fetchDayAhead, fetchYesterdaySoc, startBatterySizing } from '../api'

const POLL_INTERVAL   = 2000
const CAP_EUR_KW_YEAR = 60   // Fluvius capaciteitstarief €/kW/jaar

const CONN = {
  mono: { label: 'Monofase', sub: '1×230V', maxAfname: 9.2,  maxInjectie: 5.0  },
  drie: { label: 'Driefasig', sub: '3×230V', maxAfname: 15.9, maxInjectie: 10.0 },
}

// Belgisch gesimplificeerd zonprofiel (schaalfactor per maand)
const SOLAR_PEAK_FRAC = [0.15, 0.25, 0.38, 0.50, 0.58, 0.62, 0.60, 0.55, 0.40, 0.27, 0.16, 0.12]

function generateSolarProfile(slots, kWp) {
  if (kWp <= 0) return null
  const now      = new Date()
  const month    = now.getMonth()
  const peakFrac = SOLAR_PEAK_FRAC[month]
  const dayLen   = 12 + 4 * Math.sin((month - 2) * Math.PI / 6)
  const halfDay  = dayLen / 2
  const isDST         = now.getTimezoneOffset() < -60
  const solarNoonUTC  = isDST ? 11 : 12
  const PR = 0.78
  const profile = []
  for (let i = 0; i < slots; i++) {
    const t        = new Date(now.getTime() + i * 15 * 60 * 1000)
    const hUTC     = t.getUTCHours() + t.getUTCMinutes() / 60
    const fromNoon = hUTC - solarNoonUTC
    if (Math.abs(fromNoon) >= halfDay) {
      profile.push(0)
    } else {
      const irr = Math.cos((Math.PI / 2) * fromNoon / halfDay)
      profile.push(Math.max(0, kWp * irr * peakFrac * PR))
    }
  }
  return profile
}

function estimateDailyKwh(kWp) {
  if (kWp <= 0) return 0
  const month    = new Date().getMonth()
  const peakFrac = SOLAR_PEAK_FRAC[month]
  const dayLen   = 12 + 4 * Math.sin((month - 2) * Math.PI / 6)
  return kWp * peakFrac * 0.78 * dayLen * (2 / Math.PI)
}

// ── Rule-based simulatie (pure frontend, geen API) ────────────────────────────
// Logica: laden wanneer prijs < laaddrempel, ontladen wanneer prijs > ontlaaddrempel.
// Bij negatieve prijzen wordt altijd maximaal geladen (gratis/betaald energie).
function simulateRuleBased(prices, thresholds, config) {
  const { chargeThreshold, dischargeThreshold } = thresholds
  const { battKwh, initSoc, minSoc, efficiency, chargePow, dischargePow } = config
  // Round-trip efficiency: sqrt voor symmetrische laad/ontlaad verliezen
  const eta = Math.sqrt(Math.max(0.5, Math.min(1, efficiency)))

  let socKwh = initSoc * battKwh
  const minSocKwh = minSoc * battKwh

  const schedule = []

  for (let i = 0; i < prices.length; i++) {
    const price = prices[i]

    // Maximaal laadbare energie vanuit het net dit kwartier
    const maxChargeGrid    = Math.max(0, Math.min(chargePow * 0.25, (battKwh - socKwh) / eta))
    // Maximaal ontlaadbare energie naar het net dit kwartier
    const maxDischargeGrid = Math.max(0, Math.min(dischargePow * 0.25, (socKwh - minSocKwh) * eta))

    let chargeKwh    = 0
    let dischargeKwh = 0

    if (price < 0) {
      // Negatieve prijs: altijd maximaal laden (we worden betaald om energie af te nemen)
      chargeKwh = maxChargeGrid
    } else if (price <= chargeThreshold && maxChargeGrid > 0.001) {
      // Goedkope prijs: laden
      chargeKwh = maxChargeGrid
    } else if (price >= dischargeThreshold && maxDischargeGrid > 0.001) {
      // Dure prijs: ontladen
      dischargeKwh = maxDischargeGrid
    }

    // SOC bijwerken
    if (chargeKwh > 0) {
      socKwh = Math.min(battKwh, socKwh + chargeKwh * eta)
    } else if (dischargeKwh > 0) {
      socKwh = Math.max(minSocKwh, socKwh - dischargeKwh / eta)
    }

    // Netto P&L per slot:
    // laden = kosten (negatief), maar bij negatieve prijs: we worden betaald = positief
    // ontladen = opbrengst (positief bij positieve prijs)
    const netRev = (dischargeKwh - chargeKwh) * price / 1000

    schedule.push({
      slot: i,
      price_eur_mwh:   price,
      charge_kwh:      chargeKwh,
      discharge_kwh:   dischargeKwh,
      soc_kwh:         socKwh,
      soc_pct:         (socKwh / battKwh) * 100,
      net_revenue_eur: netRev,
    })
  }

  const totalChargeKwh    = schedule.reduce((s, r) => s + r.charge_kwh,    0)
  const totalDischargeKwh = schedule.reduce((s, r) => s + r.discharge_kwh, 0)
  const grossRevenue      = schedule.reduce((s, r) => s + r.net_revenue_eur, 0)

  return {
    schedule,
    summary: {
      gross_revenue_eur:   grossRevenue,
      final_soc_pct:       (socKwh / battKwh) * 100,
      charge_events:       schedule.filter(s => s.charge_kwh    > 0.001).length,
      discharge_events:    schedule.filter(s => s.discharge_kwh > 0.001).length,
      total_charge_kwh:    totalChargeKwh,
      total_discharge_kwh: totalDischargeKwh,
      partial_cycles:      totalDischargeKwh / battKwh,
    },
  }
}

// ── Stijlhulpers ──────────────────────────────────────────────────────────────
const sec = (title) => (
  <div style={{
    display: 'flex', alignItems: 'center', gap: 8, margin: '20px 0 12px',
    color: 'var(--muted)', fontSize: 11, fontWeight: 600,
    letterSpacing: '0.06em', textTransform: 'uppercase',
  }}>
    <span style={{ whiteSpace: 'nowrap' }}>{title}</span>
    <div style={{ flex: 1, height: 1, background: 'var(--border)' }}/>
  </div>
)

const Slider = ({ label, value, min, max, step, onChange, fmt, accent }) => (
  <div style={{ marginBottom: 12 }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 5 }}>
      <span style={{ color: 'var(--muted)', fontSize: 13 }}>{label}</span>
      <span style={{ color: accent || 'var(--text)', fontSize: 13, fontWeight: 600 }}>
        {fmt ? fmt(value) : value}
      </span>
    </div>
    <input type="range" min={min} max={max} step={step} value={value}
      onChange={e => onChange(parseFloat(e.target.value))}
      style={{ width: '100%', accentColor: accent || 'var(--accent)' }}/>
  </div>
)

const StatusBadge = ({ status }) => {
  const cls = { pending: 'badge-pending', running: 'badge-running', completed: 'badge-done', failed: 'badge-failed' }
  const lbl = { pending: '⏳ Wachten', running: '⚙️ Bezig…', completed: '✅ Klaar', failed: '❌ Mislukt' }
  return <span className={`badge ${cls[status] || 'badge-pending'}`}>{lbl[status] || status}</span>
}

// Tooltip voor dispatch bar chart
const ChartTip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 14px' }}>
      <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 4 }}>Uur {label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.fill || p.stroke, fontWeight: 600, fontSize: 13 }}>
          {p.name}: {p.value?.toFixed(3)} kWh
        </div>
      ))}
    </div>
  )
}

// Tooltip factory voor vergelijkingsgrafieken
const makeTip = (unitStr, decimals) => ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 14px' }}>
      <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 4 }}>Uur {label}</div>
      {payload.map((p, i) => p.value != null && (
        <div key={i} style={{ color: p.stroke, fontWeight: 600, fontSize: 13 }}>
          {p.name}: {p.value.toFixed(decimals)}{unitStr}
        </div>
      ))}
    </div>
  )
}
const SocTip = makeTip('%', 1)
const RevTip = makeTip(' €', 4)

// ── CSV export ────────────────────────────────────────────────────────────────
function exportCSV(schedule, summary) {
  const comma  = (n, d = 3) => (n ?? 0).toFixed(d).replace('.', ',')
  const header = ['Tijdstip','Prijs (€/MWh)','Laden (kWh)','Ontladen (kWh)',
                  'Netto laden grid (kWh)','Netto laden solar (kWh)',
                  'SoC (kWh)','SoC (%)','Netto P&L (€)'].join(';')
  const rows = schedule.map(h => [
    h.datetime,
    comma(h.price_eur_mwh, 2),
    comma(h.charge_kwh),
    comma(h.discharge_kwh),
    comma(h.charge_grid_kwh),
    comma(h.charge_solar_kwh),
    comma(h.soc_kwh),
    comma(h.soc_pct, 1),
    comma(h.net_revenue_eur, 4),
  ].join(';'))
  rows.push('')
  rows.push('Samenvatting')
  rows.push(`Netto opbrengst (na cap.tarief);${comma(summary.total_net_revenue_eur, 4)}`)
  rows.push(`Arbitrage bruto;${comma(summary.revenue_execute_eur, 4)}`)
  rows.push(`Cap.tarief periode;${comma(summary.cap_tarief_period_eur, 2)}`)
  rows.push(`Piek afname;${comma(summary.peak_charge_kw, 2)} kW`)
  rows.push(`Eind-SOC;${comma(summary.final_soc_pct, 1)}%`)
  const csv  = [header, ...rows].join('\r\n')
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href     = url
  a.download = `fluxy-schema-${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

// ── Specs berekeningen ────────────────────────────────────────────────────────
function calcSpecs(battKwh, chargePow, dischargePow, minSoc, efficiency) {
  const eta      = Math.sqrt(efficiency)
  const slCh     = chargePow    * 0.25
  const slDis    = dischargePow * 0.25
  const cRateCh  = (eta * slCh  * 4) / battKwh
  const cRateDis = (slDis / eta * 4) / battKwh
  const usable   = battKwh * (1 - minSoc)
  const tChMin   = usable / (eta * slCh)  * 15
  const tDisMin  = usable / (slDis / eta) * 15
  const asymMilp = dischargePow / 2.5
  return { cRateCh, cRateDis, tChMin, tDisMin, asymMilp, maxCRate: Math.max(cRateCh, cRateDis) }
}

function calcCap(dischargePow) {
  const peak    = Math.max(2.5, dischargePow)
  const monthly = peak * CAP_EUR_KW_YEAR / 12
  const forfait = 2.5  * CAP_EUR_KW_YEAR / 12
  const extra   = monthly - forfait
  return { peak, monthly, forfait, extra }
}

// ── Hoofd component ───────────────────────────────────────────────────────────
export default function Optimizer() {
  // Batterij & aansluiting
  const [aansluiting, setAansluiting] = useState('mono')
  const [battKwh,     setBattKwh]     = useState(10)
  const [initSoc,     setInitSoc]     = useState(0.50)
  const [minSoc,      setMinSoc]      = useState(0.10)
  const [endSoc,      setEndSoc]      = useState(0.20)
  const [eff,         setEff]         = useState(0.92)
  const [horizonDays, setHorizonDays] = useState(1)
  const [dischPow,    setDischPow]    = useState(5.0)
  const [solarKwp,    setSolarKwp]    = useState(0)

  // MILP job state
  const [jobId,      setJobId]     = useState(null)
  const [job,        setJob]       = useState(null)
  const [submitting, setSub]       = useState(false)
  const [error,      setError]     = useState(null)
  const [priceInfo,  setPriceInfo] = useState(null)
  const pollRef = useRef(null)

  // Feature #22: aanbevolen start-SOC (gisteren's finale SOC via MILP)
  const [yesterdaySoc,        setYesterdaySoc]        = useState(null)
  const [yesterdaySocLoading, setYesterdaySocLoading] = useState(true)

  useEffect(() => {
    fetchYesterdaySoc()
      .then(d => setYesterdaySoc(d))
      .catch(() => {})  // stil falen — badge verschijnt gewoon niet
      .finally(() => setYesterdaySocLoading(false))
  }, [])

  // Feature #30/#31: Battery Sizing Advisor — polling-gebaseerde voortgang
  const [sizingResult,   setSizingResult]   = useState(null)
  const [sizingLoading,  setSizingLoading]  = useState(false)
  const [sizingError,    setSizingError]    = useState(null)
  const [sizingProgress, setSizingProgress] = useState(0)      // 0–100
  const [sizingMessage,  setSizingMessage]  = useState('')     // label per grootte
  const sizingPollRef = useRef(null)

  const stopSizingPoll = () => {
    if (sizingPollRef.current) { clearInterval(sizingPollRef.current); sizingPollRef.current = null }
  }

  const handleSizingAnalyse = async () => {
    stopSizingPoll()
    setSizingLoading(true); setSizingError(null); setSizingResult(null)
    setSizingProgress(0); setSizingMessage('⬇️ ENTSO-E data ophalen…')
    try {
      const { job_id } = await startBatterySizing({
        power_kw:   conn.maxInjectie,
        efficiency: eff,
        days:       365,
      })
      // Start polling elke 3 s
      sizingPollRef.current = setInterval(async () => {
        try {
          const j = await pollJob(job_id)
          setSizingProgress(j.progress ?? 0)
          if (j.message) setSizingMessage(j.message)
          if (j.status === 'completed') {
            stopSizingPoll()
            setSizingResult(j.result)
            setSizingLoading(false)
          } else if (j.status === 'failed') {
            stopSizingPoll()
            setSizingError(j.error || 'Berekening mislukt.')
            setSizingLoading(false)
          }
        } catch (e) {
          stopSizingPoll()
          setSizingError(e.message)
          setSizingLoading(false)
        }
      }, 3000)
    } catch (e) {
      setSizingError(e.response?.data?.detail || e.message)
      setSizingLoading(false)
    }
  }

  // Opgeslagen prijzen voor rule-based (worden ingesteld zodra ENTSO-E fetch klaar is)
  const [latestPrices, setLatestPrices] = useState(null)  // number[]

  // Rule-based drempelwaarden
  const [rbChargeThr,    setRbChargeThr]    = useState(60)    // €/MWh
  const [rbDischargeThr, setRbDischargeThr] = useState(120)   // €/MWh

  const conn  = CONN[aansluiting]
  const specs = calcSpecs(battKwh, conn.maxAfname, dischPow, minSoc, eff)
  const cap   = calcCap(dischPow)

  // Klem discharge bij wisselen aansluiting
  useEffect(() => {
    setDischPow(prev => Math.min(prev, conn.maxInjectie))
  }, [aansluiting])

  // MILP polling
  const stopPoll = () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null } }
  useEffect(() => {
    if (!jobId) return
    stopPoll()
    pollRef.current = setInterval(async () => {
      try {
        const j = await pollJob(jobId)
        setJob(j)
        if (j.status === 'completed' || j.status === 'failed') stopPoll()
      } catch (e) { setError(e.message); stopPoll() }
    }, POLL_INTERVAL)
    return stopPoll
  }, [jobId])

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError(null); setJob(null); setSub(true); setPriceInfo(null)
    try {
      // Prijzen ophalen: forward-looking (1–3d) vs historisch backtesting (7–365d)
      const fetchDays   = Math.min(horizonDays, 365)
      const pricesData  = await fetchDayAhead(fetchDays)
      let priceFloats
      if (horizonDays <= 3) {
        // Forward-looking: filter vanaf nu, neem de volgende horizonDays * 96 slots
        const nowMs  = Date.now()
        const slotMs = Math.floor(nowMs / (15 * 60 * 1000)) * 15 * 60 * 1000
        priceFloats  = pricesData.records
          .filter(r => new Date(r.timestamp).getTime() >= slotMs)
          .slice(0, horizonDays * 96)
          .map(r => r.price_eur_mwh)
      } else {
        // Historisch backtesting: neem de laatste horizonDays * 96 slots (gesorteerd oplopend)
        priceFloats = pricesData.records
          .slice(-horizonDays * 96)
          .map(r => r.price_eur_mwh)
      }

      if (priceFloats.length < 4) {
        throw new Error(
          `Onvoldoende prijsdata: ${priceFloats.length} slots beschikbaar (min. 4). ` +
          'Controleer of de ENTSO-E API key correct is ingesteld.'
        )
      }
      setPriceInfo({ slots: priceFloats.length, hours: (priceFloats.length / 4).toFixed(1) })
      setLatestPrices(priceFloats)  // ← ook voor rule-based simulatie

      const solarForecast = generateSolarProfile(priceFloats.length, solarKwp)
      const payload = {
        prices:             priceFloats,
        battery_kwh:        battKwh,
        efficiency:         eff,
        initial_soc:        initSoc,
        min_soc:            minSoc,
        min_end_soc:        endSoc,
        discharge_power_kw: dischPow,
        charge_power_kw:    conn.maxAfname,
        ...(solarForecast ? { solar_forecast: solarForecast } : {}),
      }
      const res = await runOptimization(payload)
      setJobId(res.job_id)
    } catch (err) {
      setError(err.response?.data?.detail || err.message)
    } finally {
      setSub(false)
    }
  }

  // ── Rule-based simulatie (useMemo = herberekend zodra drempel of param wijzigt) ──
  const rbResult = useMemo(() => {
    if (!latestPrices?.length) return null
    return simulateRuleBased(
      latestPrices,
      { chargeThreshold: rbChargeThr, dischargeThreshold: rbDischargeThr },
      { battKwh, initSoc, minSoc, efficiency: eff, chargePow: conn.maxAfname, dischargePow: dischPow },
    )
  }, [latestPrices, rbChargeThr, rbDischargeThr, battKwh, initSoc, minSoc, eff, conn.maxAfname, dischPow])

  const result   = job?.result
  const summary  = result?.summary  || {}
  const schedule = result?.schedule || []

  // Vergelijkingsgrafiekdata: SOC en cumulatieve opbrengst per uur
  const compChartData = useMemo(() => {
    const milpSched = result?.schedule || []
    const rbSched   = rbResult?.schedule || []
    if (!milpSched.length && !rbSched.length) return []
    const n = Math.ceil(Math.max(milpSched.length, rbSched.length) / 4)
    let milpCum = 0, rbCum = 0
    return Array.from({ length: n }, (_, h) => {
      const entry = { hour: h }
      if (milpSched.length) {
        const slots = milpSched.slice(h * 4, (h + 1) * 4)
        milpCum += slots.reduce((s, r) => s + (r.net_revenue_eur ?? 0), 0)
        const idx = Math.min((h + 1) * 4 - 1, milpSched.length - 1)
        entry.milpSoc = +milpSched[idx].soc_pct.toFixed(1)
        entry.milpRev = +milpCum.toFixed(5)
      }
      if (rbSched.length) {
        const slots = rbSched.slice(h * 4, (h + 1) * 4)
        rbCum += slots.reduce((s, r) => s + (r.net_revenue_eur ?? 0), 0)
        const idx = Math.min((h + 1) * 4 - 1, rbSched.length - 1)
        entry.rbSoc = +rbSched[idx].soc_pct.toFixed(1)
        entry.rbRev = +rbCum.toFixed(5)
      }
      return entry
    })
  }, [result, rbResult])

  // Dispatch grafiek data voor MILP (per uur)
  const chartData = Array.from(
    { length: Math.ceil(schedule.length / 4) },
    (_, h) => {
      const slots = schedule.slice(h * 4, (h + 1) * 4)
      return {
        hour:      h,
        charge:    slots.reduce((s, r) => s + (r.charge_kwh    > 0 ? r.charge_kwh    : 0), 0),
        discharge: slots.reduce((s, r) => s + (r.discharge_kwh > 0 ? r.discharge_kwh : 0), 0),
      }
    }
  )

  const cColor   = (c) => c > 2 ? '#ef4444' : c > 1 ? '#f59e0b' : '#22c55e'
  const hasSolar = solarKwp > 0
  const estKwh   = estimateDailyKwh(solarKwp)
  const badSpread = rbChargeThr >= rbDischargeThr
  const milpGross = summary.revenue_execute_eur ?? null

  return (
    <div>
      <div className="page-header">
        <div className="page-title">🔋 MILP Optimizer</div>
        <div className="page-sub">
          Batterij dispatch optimalisatie via PuLP + HiGHS solver
          {hasSolar && <span style={{ color: '#f59e0b', marginLeft: 8 }}>· ☀️ MILP+Solar actief</span>}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '340px 1fr', gap: 24, alignItems: 'start' }}>

        {/* ════════════════ LINKS: parameters ════════════════ */}
        <div className="card" style={{ padding: '20px 22px' }}>
          <form onSubmit={handleSubmit}>

            {/* ── Aansluitingstype ── */}
            {sec('Aansluitingstype')}
            <div style={{
              display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6,
              background: 'var(--bg)', borderRadius: 10, padding: 4,
            }}>
              {Object.entries(CONN).map(([key, c]) => {
                const on = aansluiting === key
                return (
                  <button key={key} type="button" onClick={() => setAansluiting(key)} style={{
                    padding: '10px 8px', borderRadius: 8,
                    border:     on ? '1px solid rgba(59,130,246,0.5)' : '1px solid transparent',
                    background: on ? 'rgba(59,130,246,0.12)' : 'transparent',
                    color:      on ? '#60a5fa' : 'var(--muted)',
                    cursor: 'pointer', fontWeight: on ? 600 : 400, fontSize: 13, transition: 'all 0.15s',
                  }}>
                    <div>{c.label}</div>
                    <div style={{ fontSize: 10, opacity: 0.6, marginTop: 2 }}>{c.sub}</div>
                  </button>
                )
              })}
            </div>
            <div style={{ marginTop: 10, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
              {[
                ['Afname van net',  conn.maxAfname,   'kW', '#3b82f6', 'Laadlimiet MILP'],
                ['Injectie op net', conn.maxInjectie, 'kW', '#22c55e', 'Ontlaadlimiet'],
              ].map(([lbl, val, unit, clr, sub]) => (
                <div key={lbl} style={{
                  background: 'var(--bg)', borderRadius: 8,
                  padding: '10px 12px', border: '1px solid var(--border)',
                }}>
                  <div style={{ color: 'var(--muted)', fontSize: 11 }}>{lbl}</div>
                  <div style={{ color: clr, fontSize: 18, fontWeight: 700, marginTop: 2 }}>
                    {val} <span style={{ fontSize: 12 }}>{unit}</span>
                  </div>
                  <div style={{ color: 'var(--muted2)', fontSize: 10, marginTop: 2 }}>{sub}</div>
                </div>
              ))}
            </div>

            {/* ── Vermogen ── */}
            {sec('Vermogen')}
            <Slider label="Max injectievermogen (kW)" value={dischPow}
              min={0.5} max={conn.maxInjectie} step={0.5}
              onChange={setDischPow} fmt={v => `${v.toFixed(1)} kW`}/>
            <div style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              background: 'rgba(34,197,94,0.06)', borderRadius: 7, padding: '8px 12px',
              border: '1px solid rgba(34,197,94,0.12)', marginBottom: 4,
            }}>
              <span style={{ color: 'var(--muted)', fontSize: 13 }}>Laadvermogen MILP</span>
              <span style={{ color: '#22c55e', fontWeight: 700, fontSize: 14 }}>
                {conn.maxAfname} kW
                <span style={{ color: 'var(--muted2)', fontWeight: 400, fontSize: 11, marginLeft: 6 }}>auto</span>
              </span>
            </div>

            {/* ── Batterij ── */}
            {sec('Batterij')}
            <div className="form-row" style={{ marginBottom: 12 }}>
              <label style={{ color: 'var(--muted)', fontSize: 13 }}>Capaciteit (kWh)</label>
              <input type="number" min={1} max={2000} step={0.5} value={battKwh}
                onChange={e => setBattKwh(parseFloat(e.target.value))}
                className="form-input"/>
            </div>
            <Slider label="Efficiëntie (round-trip)" value={eff}
              min={0.7} max={1.0} step={0.01}
              onChange={setEff} fmt={v => `${(v * 100).toFixed(0)}%`}/>

            {/* ── State of Charge ── */}
            {sec('State of Charge')}
            <Slider label="Start SOC"    value={initSoc} min={0.05} max={1}    step={0.05} onChange={setInitSoc} fmt={v => `${(v*100).toFixed(0)}%`}/>
            {/* Feature #22: aanbevolen start-SOC badge */}
            {yesterdaySocLoading ? (
              <div style={{ fontSize: 11, color: 'var(--muted2)', marginBottom: 8, paddingLeft: 2 }}>
                ⏳ Aanbevolen SOC berekenen…
              </div>
            ) : yesterdaySoc?.final_soc_pct != null ? (
              <div style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                background: 'rgba(34,197,94,0.06)', border: '1px solid rgba(34,197,94,0.2)',
                borderRadius: 7, padding: '7px 12px', marginBottom: 8, fontSize: 12,
              }}>
                <span style={{ color: 'var(--muted)' }}>
                  💡 Aanbevolen start-SOC&nbsp;
                  <strong style={{ color: '#22c55e' }}>
                    {yesterdaySoc.final_soc_pct.toFixed(0)}%
                  </strong>
                  <span style={{ color: 'var(--muted2)', marginLeft: 4 }}>
                    (finale SOC gisteren)
                  </span>
                </span>
                <button
                  type="button"
                  onClick={() => setInitSoc(Math.round(yesterdaySoc.final_soc_pct) / 100)}
                  style={{
                    background: 'rgba(34,197,94,0.12)', border: '1px solid rgba(34,197,94,0.3)',
                    borderRadius: 5, padding: '3px 9px', color: '#22c55e',
                    cursor: 'pointer', fontSize: 11, fontWeight: 600, whiteSpace: 'nowrap',
                  }}
                >
                  Overnemen →
                </button>
              </div>
            ) : null}
            <Slider label="Min reserve"  value={minSoc}  min={0}    max={0.40} step={0.05} onChange={setMinSoc}  fmt={v => `${(v*100).toFixed(0)}%`}/>
            <Slider label="Min eind-SOC" value={endSoc}  min={0.05} max={0.50} step={0.05} onChange={setEndSoc}  fmt={v => `${(v*100).toFixed(0)}%`}/>

            {/* ── Zonne-energie ── */}
            {sec('Zonne-energie (optioneel)')}
            <Slider label="PV-vermogen (kWp)" value={solarKwp}
              min={0} max={20} step={0.5}
              onChange={setSolarKwp} fmt={v => v === 0 ? 'Uit' : `${v.toFixed(1)} kWp`}/>
            {hasSolar ? (
              <div style={{
                background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.2)',
                borderRadius: 7, padding: '8px 12px', fontSize: 12, color: '#f59e0b', marginBottom: 4,
              }}>
                ☀️ Geschatte dagopbrengst: <strong>~{estKwh.toFixed(1)} kWh/dag</strong>
                <span style={{ color: 'var(--muted2)', marginLeft: 6 }}>→ MILP+Solar actief</span>
              </div>
            ) : (
              <div style={{ color: 'var(--muted2)', fontSize: 11, marginBottom: 4 }}>
                0 kWp = geen solar → MILP basis arbitrage
              </div>
            )}

            {/* ── Regelgebaseerde strategie ── */}
            {sec('Regelgebaseerde strategie')}
            <div style={{
              background: 'rgba(168,85,247,0.06)', border: '1px solid rgba(168,85,247,0.18)',
              borderRadius: 8, padding: '9px 12px', marginBottom: 12, fontSize: 12, color: 'var(--muted)',
            }}>
              💡 Simpele aan/uit logica als baseline. Resultaat verschijnt zodra prijzen geladen zijn
              (na ▶ Optimaliseer). Drempelwaarden zijn live aanpasbaar.
            </div>
            <Slider label="Laden onder (€/MWh)" value={rbChargeThr}
              min={-50} max={200} step={5}
              onChange={setRbChargeThr} fmt={v => `${v} €/MWh`}
              accent="#3b82f6"/>
            <Slider label="Ontladen boven (€/MWh)" value={rbDischargeThr}
              min={50} max={400} step={5}
              onChange={setRbDischargeThr} fmt={v => `${v} €/MWh`}
              accent="#22c55e"/>
            {badSpread ? (
              <div style={{ color: '#ef4444', fontSize: 11, marginBottom: 6 }}>
                ⚠️ Laaddrempel moet lager zijn dan ontlaaddrempel voor arbitrage
              </div>
            ) : (
              <div style={{
                display: 'flex', justifyContent: 'space-between',
                fontSize: 11, color: 'var(--muted2)', marginBottom: 6,
              }}>
                <span>Spread: <strong style={{ color: rbDischargeThr - rbChargeThr >= 50 ? '#22c55e' : '#f59e0b' }}>
                  {rbDischargeThr - rbChargeThr} €/MWh
                </strong></span>
                <span>Negatieve prijs → altijd laden ⚡</span>
              </div>
            )}

            {/* ── Horizon ── */}
            {sec('Tijdshorizon')}
            <div style={{
              display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6,
              background: 'var(--bg)', borderRadius: 10, padding: 4, marginBottom: 8,
            }}>
              {[
                { d: 1,   label: '1 dag',    sub: '96 slots',    hist: false },
                { d: 2,   label: '2 dagen',  sub: '192 slots',   hist: false },
                { d: 3,   label: '3 dagen',  sub: '288 slots',   hist: false },
                { d: 7,   label: '7 dagen',  sub: '~672 slots',  hist: true  },
                { d: 14,  label: '14 dagen', sub: '~1344 slots', hist: true  },
                { d: 30,  label: '30 dagen', sub: '~2880 slots', hist: true  },
                { d: 90,  label: '3 maanden',sub: '~8640 slots', hist: true  },
                { d: 180, label: '6 maanden',sub: '~17k slots',  hist: true  },
                { d: 365, label: '1 jaar',   sub: '~35k slots',  hist: true  },
              ].map(({ d, label, sub, hist }) => {
                const on = horizonDays === d
                return (
                  <button key={d} type="button" onClick={() => setHorizonDays(d)} style={{
                    padding: '9px 8px', borderRadius: 8,
                    border:     on ? `1px solid rgba(${hist ? '251,146,60' : '59,130,246'},0.5)` : '1px solid transparent',
                    background: on ? `rgba(${hist ? '251,146,60' : '59,130,246'},0.12)` : 'transparent',
                    color:      on ? (hist ? '#fb923c' : '#60a5fa') : 'var(--muted)',
                    cursor: 'pointer', fontWeight: on ? 600 : 400, fontSize: 12, transition: 'all 0.15s',
                    textAlign: 'center',
                  }}>
                    <div>{label}</div>
                    <div style={{ fontSize: 10, opacity: 0.65, marginTop: 2 }}>{sub}</div>
                  </button>
                )
              })}
            </div>
            {horizonDays <= 3 ? (
              horizonDays === 1 ? (
                <div style={{ color: 'var(--muted2)', fontSize: 11, marginBottom: 8 }}>
                  MILP optimaliseert de komende 24 uur in 1 solver-run.
                </div>
              ) : (
                <div style={{
                  background: 'rgba(59,130,246,0.06)', border: '1px solid rgba(59,130,246,0.18)',
                  borderRadius: 7, padding: '8px 12px', fontSize: 11, color: 'var(--muted)', marginBottom: 8,
                }}>
                  🧩 <strong>Rolling horizon:</strong> MILP ziet {horizonDays * 24}h prijzen tegelijk
                  en optimaliseert over de volledige periode in 1 run.{' '}
                  <span style={{ color: 'var(--muted2)' }}>
                    Vereist D+{horizonDays - 1} ENTSO-E prijzen (beschikbaar ~13:00 CET).
                  </span>
                </div>
              )
            ) : (
              <div style={{
                background: 'rgba(251,146,60,0.06)', border: '1px solid rgba(251,146,60,0.25)',
                borderRadius: 7, padding: '8px 12px', fontSize: 11, color: 'var(--muted)', marginBottom: 8,
              }}>
                📊 <strong>Backtesting:</strong> MILP optimaliseert over de afgelopen {
                  horizonDays === 365 ? '1 jaar' : horizonDays === 180 ? '6 maanden' :
                  horizonDays === 90 ? '3 maanden' : `${horizonDays} dagen`
                } historische ENTSO-E prijzen in 1 run.{' '}
                {horizonDays >= 90 && (
                  <span style={{ color: '#fb923c', fontWeight: 600 }}>
                    ⏳ {horizonDays >= 180 ? '10–20 min' : '5–10 min'} rekentijd verwacht.
                  </span>
                )}
                {horizonDays < 90 && (
                  <span style={{ color: 'var(--muted2)' }}>
                    ⏳ ~{horizonDays <= 14 ? '30–60 sec' : '2–5 min'} rekentijd.
                  </span>
                )}
              </div>
            )}

            {error && <div className="error" style={{ marginTop: 8 }}>⚠️ {error}</div>}

            <button type="submit" className="btn btn-primary"
              style={{ marginTop: 16, width: '100%' }}
              disabled={submitting || job?.status === 'running'}>
              {submitting
                ? '📡 Prijzen laden…'
                : job?.status === 'running'
                ? '⚙️ Bezig…'
                : hasSolar
                  ? `▶ Optimaliseer ${horizonDays > 1 ? horizonDays + 'd ' : ''}+ Solar`
                  : `▶ Optimaliseer${horizonDays > 1 ? ' ' + horizonDays + ' dagen' : ''}`}
            </button>
          </form>
        </div>

        {/* ════════════════ RECHTS: specs + resultaten ════════════════ */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

          {/* ── Batterij Specs & Validatie ── */}
          <div className="card" style={{ padding: '20px 22px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <div className="card-title" style={{ margin: 0 }}>🔬 Batterij Specs & Validatie</div>
              <div style={{ fontSize: 12, color: 'var(--muted)' }}>live berekend</div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: 14 }}>
              {[
                ['C-rate laden',    `${specs.cRateCh.toFixed(2)}C`,  cColor(specs.cRateCh)],
                ['C-rate ontladen', `${specs.cRateDis.toFixed(2)}C`, cColor(specs.cRateDis)],
                ['Vol laden',       `${Math.round(specs.tChMin)} min`,  'var(--text)'],
                ['Vol ontladen',    `${Math.round(specs.tDisMin)} min`, 'var(--text)'],
              ].map(([lbl, val, clr]) => (
                <div key={lbl} style={{
                  background: 'var(--bg)', borderRadius: 8,
                  padding: '12px 10px', textAlign: 'center', border: '1px solid var(--border)',
                }}>
                  <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 6 }}>{lbl}</div>
                  <div style={{ color: clr, fontSize: 18, fontWeight: 700 }}>{val}</div>
                </div>
              ))}
            </div>
            {specs.maxCRate > 2
              ? <div style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 7, padding: '8px 12px', fontSize: 13, color: '#ef4444' }}>
                  ⛔ C-rate = {specs.maxCRate.toFixed(1)}C — te hoog, overweeg grotere batterij of minder vermogen
                </div>
              : specs.maxCRate > 1
              ? <div style={{ background: 'rgba(245,158,11,0.1)', border: '1px solid rgba(245,158,11,0.3)', borderRadius: 7, padding: '8px 12px', fontSize: 13, color: '#f59e0b' }}>
                  ⚠️ C-rate = {specs.maxCRate.toFixed(1)}C — boven 1C, controleer batterijspecs
                </div>
              : <div style={{ background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.2)', borderRadius: 7, padding: '8px 12px', fontSize: 13, color: '#22c55e' }}>
                  ✅ C-rate: laden {specs.cRateCh.toFixed(2)}C / ontladen {specs.cRateDis.toFixed(2)}C
                  &nbsp;·&nbsp; ontladen is <strong>{specs.asymMilp.toFixed(1)}×</strong> sneller dan laden (vs forfait 2.5 kW)
                </div>
            }
          </div>

          {/* ── Capaciteitstarief ── */}
          <div className="card" style={{ padding: '20px 22px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <div className="card-title" style={{ margin: 0 }}>💶 Capaciteitstarief (Fluvius)</div>
              <div style={{ fontSize: 12, color: 'var(--muted)' }}>€{CAP_EUR_KW_YEAR}/kW/jaar</div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
              {[
                ['Maandelijkse kost',   `€${cap.monthly.toFixed(2)}`, 'var(--text)'],
                ['Forfait minimum',     `€${cap.forfait.toFixed(2)}`,  'var(--muted)'],
                ['Extra boven forfait', cap.extra > 0.01 ? `+€${cap.extra.toFixed(2)}` : '= forfait',
                                        cap.extra > 0.01 ? '#f59e0b' : '#22c55e'],
              ].map(([lbl, val, clr]) => (
                <div key={lbl} style={{ background: 'var(--bg)', borderRadius: 8, padding: '12px', border: '1px solid var(--border)' }}>
                  <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 4 }}>{lbl}</div>
                  <div style={{ color: clr, fontSize: 16, fontWeight: 700 }}>{val}</div>
                </div>
              ))}
            </div>
            <div style={{ marginTop: 10, fontSize: 12, color: 'var(--muted)' }}>
              Piek: {cap.peak.toFixed(1)} kW ({conn.label}) · {cap.peak.toFixed(1)} × €{(CAP_EUR_KW_YEAR/12).toFixed(2)}/mnd
              &nbsp;·&nbsp; <em>MILP optimaliseert zijn eigen piek</em>
            </div>
          </div>

          {/* ── Feature #29: Mono vs Driefasig vergelijking ── */}
          <div className="card" style={{ padding: '20px 22px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <div className="card-title" style={{ margin: 0 }}>⚡ Mono vs Driefasig</div>
              <div style={{ fontSize: 11, color: 'var(--muted)' }}>aansluiting vergelijking</div>
            </div>

            {/* Header rij */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6, marginBottom: 6 }}>
              <div/>
              {[['mono', CONN.mono], ['drie', CONN.drie]].map(([key, c]) => {
                const active = aansluiting === key
                return (
                  <div key={key} style={{
                    background: active ? 'rgba(59,130,246,0.08)' : 'var(--bg)',
                    border: active ? '1px solid rgba(59,130,246,0.3)' : '1px solid var(--border)',
                    borderRadius: 8, padding: '7px 10px', textAlign: 'center',
                  }}>
                    <div style={{ color: active ? '#60a5fa' : 'var(--muted)', fontWeight: 700, fontSize: 12 }}>{c.label}</div>
                    <div style={{ color: 'var(--muted2)', fontSize: 10 }}>{c.sub}</div>
                    {active && <div style={{ fontSize: 9, color: '#60a5fa', marginTop: 2 }}>● actief</div>}
                  </div>
                )
              })}
            </div>

            {/* Data rijen */}
            {[
              { label: 'Max laadvermogen',  sub: 'van net (laadlimiet)',  mono: `${CONN.mono.maxAfname} kW`,   drie: `${CONN.drie.maxAfname} kW`   },
              { label: 'Max ontlaadvermogen', sub: 'op net (injectie)',   mono: `${CONN.mono.maxInjectie} kW`, drie: `${CONN.drie.maxInjectie} kW` },
              { label: 'Cap.tarief / mnd',  sub: `${CAP_EUR_KW_YEAR} €/kW/jaar`,
                mono: `€${(CONN.mono.maxAfname * CAP_EUR_KW_YEAR / 12).toFixed(2)}`,
                drie: `€${(CONN.drie.maxAfname * CAP_EUR_KW_YEAR / 12).toFixed(2)}` },
              { label: 'Cap.tarief / jaar', sub: 'vaste jaarlijkse kost',
                mono: `€${(CONN.mono.maxAfname * CAP_EUR_KW_YEAR).toFixed(0)}`,
                drie: `€${(CONN.drie.maxAfname * CAP_EUR_KW_YEAR).toFixed(0)}` },
              ...(milpGross != null ? [{
                label: 'MILP bruto / dag', sub: 'huidig resultaat', isRevenue: true,
                mono: aansluiting === 'mono'
                  ? `€${milpGross.toFixed(3)}`
                  : `~€${(milpGross * CONN.mono.maxInjectie / CONN[aansluiting].maxInjectie).toFixed(3)}`,
                drie: aansluiting === 'drie'
                  ? `€${milpGross.toFixed(3)}`
                  : `~€${(milpGross * CONN.drie.maxInjectie / CONN[aansluiting].maxInjectie).toFixed(3)}`,
                monoEst: aansluiting !== 'mono',
                drieEst: aansluiting !== 'drie',
              }] : []),
            ].map(row => (
              <div key={row.label} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6, marginBottom: 6 }}>
                <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
                  <div style={{ color: 'var(--text)', fontSize: 12 }}>{row.label}</div>
                  <div style={{ color: 'var(--muted2)', fontSize: 10 }}>{row.sub}</div>
                </div>
                {[
                  ['mono', row.mono, row.monoEst],
                  ['drie', row.drie, row.drieEst],
                ].map(([key, val, isEst]) => {
                  const active   = aansluiting === key
                  const revStyle = row.isRevenue && active
                  return (
                    <div key={key} style={{
                      background: revStyle ? 'rgba(34,197,94,0.06)' : active ? 'rgba(59,130,246,0.05)' : 'var(--bg)',
                      border: `1px solid ${revStyle ? 'rgba(34,197,94,0.2)' : active ? 'rgba(59,130,246,0.2)' : 'var(--border)'}`,
                      borderRadius: 6, padding: '7px 10px', textAlign: 'center',
                    }}>
                      <div style={{
                        color:      revStyle ? '#22c55e' : active ? 'var(--text)' : 'var(--muted)',
                        fontWeight: active ? 700 : 400,
                        fontSize:   13,
                      }}>{val}</div>
                      {isEst && <div style={{ color: 'var(--muted2)', fontSize: 9, marginTop: 1 }}>geschat</div>}
                    </div>
                  )
                })}
              </div>
            ))}

            {/* Netto verschil footer */}
            <div style={{ marginTop: 6, fontSize: 11, color: 'var(--muted)', borderTop: '1px solid var(--border)', paddingTop: 8 }}>
              💡 Driefasig geeft +{(CONN.drie.maxInjectie - CONN.mono.maxInjectie).toFixed(1)} kW extra ontlaadvermogen,
              maar kost +€{((CONN.drie.maxAfname - CONN.mono.maxAfname) * CAP_EUR_KW_YEAR / 12).toFixed(2)}/mnd meer
              aan capaciteitstarief. Driefasig loont bij grote batterijen met hoge arbitrage-spread.
            </div>
          </div>

          {/* ── Prijzen status ── */}
          {priceInfo && (
            <div style={{
              background: 'rgba(59,130,246,0.06)', border: '1px solid rgba(59,130,246,0.18)',
              borderRadius: 10, padding: '10px 16px', fontSize: 13, color: '#60a5fa',
              display: 'flex', gap: 16, alignItems: 'center',
            }}>
              <span>📡</span>
              <span>
                <strong>{priceInfo.slots} slots</strong> geladen ({priceInfo.hours} uur · {horizonDays} dag{horizonDays > 1 ? 'en' : ''}) van ENTSO-E
                {hasSolar && <span style={{ color: '#f59e0b', marginLeft: 8 }}>· ☀️ Solar forecast {solarKwp} kWp meegestuurd</span>}
              </span>
            </div>
          )}

          {/* ── Job status ── */}
          {job && (
            <div className="card" style={{ padding: '16px 22px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ color: 'var(--text)', fontWeight: 600 }}>Job {jobId?.slice(0, 8)}…</div>
                <StatusBadge status={job.status}/>
              </div>
              {job.status === 'running' && <div className="loading" style={{ marginTop: 10 }}>HiGHS solver bezig…</div>}
              {job.status === 'failed' && job.error && (
                <div style={{ marginTop: 8, color: '#ef4444', fontSize: 13 }}>{job.error}</div>
              )}
            </div>
          )}

          {/* ── Rule-based preview (prijzen geladen, MILP nog niet klaar) ── */}
          {rbResult && !result && (
            <div className="card" style={{ padding: '20px 22px', border: '1px solid rgba(168,85,247,0.3)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
                <div className="card-title" style={{ margin: 0, color: '#a855f7' }}>
                  📏 Regelgebaseerde simulatie
                </div>
                <span style={{ fontSize: 11, color: 'var(--muted)' }}>
                  ≤{rbChargeThr} / ≥{rbDischargeThr} €/MWh
                </span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
                {[
                  ['Arbitrage bruto',   `€${rbResult.summary.gross_revenue_eur.toFixed(3)}`, '#a855f7'],
                  ['Laadevenementen',   `${rbResult.summary.charge_events} slots`,           'var(--text)'],
                  ['Ontladevenementen', `${rbResult.summary.discharge_events} slots`,         'var(--text)'],
                  ['Eind-SOC',          `${rbResult.summary.final_soc_pct.toFixed(1)}%`,     'var(--text)'],
                ].map(([lbl, val, clr]) => (
                  <div key={lbl} className="kpi">
                    <div className="kpi-label">{lbl}</div>
                    <div className="kpi-value" style={{ color: clr, fontSize: 20 }}>{val}</div>
                  </div>
                ))}
              </div>
              <div style={{ color: 'var(--muted2)', fontSize: 11, marginTop: 10 }}>
                Wachten op MILP voor vergelijking…
              </div>
            </div>
          )}

          {/* ── MILP + vergelijking resultaten ── */}
          {result && (
            <>
              {/* ── Strategie vergelijking ── */}
              {rbResult && (
                <div className="card" style={{
                  padding: '20px 22px',
                  border: '1px solid rgba(168,85,247,0.25)',
                  background: 'rgba(168,85,247,0.02)',
                }}>
                  <div className="card-title" style={{ marginBottom: 16 }}>⚖️ Strategie Vergelijking</div>

                  {/* 3-kolom vergelijkingstabel */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginBottom: 14 }}>
                    {/* Header rij */}
                    <div/>
                    <div style={{
                      background: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.25)',
                      borderRadius: 8, padding: '10px 12px', textAlign: 'center',
                    }}>
                      <div style={{ color: '#60a5fa', fontWeight: 700, fontSize: 12 }}>🧮 MILP Optimaal</div>
                      <div style={{ color: 'var(--muted)', fontSize: 10, marginTop: 2 }}>HiGHS solver</div>
                    </div>
                    <div style={{
                      background: 'rgba(168,85,247,0.1)', border: '1px solid rgba(168,85,247,0.25)',
                      borderRadius: 8, padding: '10px 12px', textAlign: 'center',
                    }}>
                      <div style={{ color: '#a855f7', fontWeight: 700, fontSize: 12 }}>📏 Regelgebaseerd</div>
                      <div style={{ color: 'var(--muted)', fontSize: 10, marginTop: 2 }}>
                        ≤{rbChargeThr} / ≥{rbDischargeThr} €/MWh
                      </div>
                    </div>

                    {/* Arbitrage */}
                    <div style={{ color: 'var(--muted)', fontSize: 12, display: 'flex', alignItems: 'center' }}>
                      Arbitrage (bruto)
                    </div>
                    <div style={{ background: 'var(--bg)', borderRadius: 7, padding: '8px 12px', textAlign: 'center', border: '1px solid var(--border)' }}>
                      <span style={{ color: '#60a5fa', fontWeight: 700, fontSize: 15 }}>
                        €{(milpGross ?? 0).toFixed(3)}
                      </span>
                    </div>
                    <div style={{ background: 'var(--bg)', borderRadius: 7, padding: '8px 12px', textAlign: 'center', border: '1px solid var(--border)' }}>
                      <span style={{ color: '#a855f7', fontWeight: 700, fontSize: 15 }}>
                        €{rbResult.summary.gross_revenue_eur.toFixed(3)}
                      </span>
                    </div>

                    {/* Netto opbrengst */}
                    <div style={{ color: 'var(--muted)', fontSize: 12, display: 'flex', alignItems: 'center' }}>
                      Netto (na cap.tarief)
                    </div>
                    <div style={{ background: 'var(--bg)', borderRadius: 7, padding: '8px 12px', textAlign: 'center', border: '1px solid var(--border)' }}>
                      <span style={{
                        color: (summary.total_net_revenue_eur ?? 0) >= 0 ? '#22c55e' : '#ef4444',
                        fontWeight: 700, fontSize: 15,
                      }}>
                        €{(summary.total_net_revenue_eur ?? 0).toFixed(3)}
                      </span>
                    </div>
                    <div style={{ background: 'var(--bg)', borderRadius: 7, padding: '8px 12px', textAlign: 'center', border: '1px solid var(--border)' }}>
                      <span style={{ color: 'var(--muted)', fontSize: 11 }}>cap.tarief zelfde</span>
                    </div>

                    {/* Eind-SOC */}
                    <div style={{ color: 'var(--muted)', fontSize: 12, display: 'flex', alignItems: 'center' }}>Eind-SOC</div>
                    <div style={{ background: 'var(--bg)', borderRadius: 7, padding: '8px 12px', textAlign: 'center', border: '1px solid var(--border)' }}>
                      <span style={{ color: 'var(--text)', fontWeight: 600 }}>{(summary.final_soc_pct ?? 0).toFixed(1)}%</span>
                    </div>
                    <div style={{ background: 'var(--bg)', borderRadius: 7, padding: '8px 12px', textAlign: 'center', border: '1px solid var(--border)' }}>
                      <span style={{ color: 'var(--text)', fontWeight: 600 }}>{rbResult.summary.final_soc_pct.toFixed(1)}%</span>
                    </div>

                    {/* Cycli */}
                    <div style={{ color: 'var(--muted)', fontSize: 12, display: 'flex', alignItems: 'center' }}>Ontlaad-cycli</div>
                    <div style={{ background: 'var(--bg)', borderRadius: 7, padding: '8px 12px', textAlign: 'center', border: '1px solid var(--border)' }}>
                      <span style={{ color: 'var(--muted)', fontSize: 11 }}>—</span>
                    </div>
                    <div style={{ background: 'var(--bg)', borderRadius: 7, padding: '8px 12px', textAlign: 'center', border: '1px solid var(--border)' }}>
                      <span style={{ color: 'var(--text)', fontWeight: 600 }}>{rbResult.summary.partial_cycles.toFixed(2)}×</span>
                    </div>
                  </div>

                  {/* MILP voordeel banner */}
                  {milpGross != null && (() => {
                    const diff     = milpGross - rbResult.summary.gross_revenue_eur
                    const pct      = rbResult.summary.gross_revenue_eur !== 0
                      ? Math.abs(diff / rbResult.summary.gross_revenue_eur * 100)
                      : 0
                    const milpWins = diff >= 0
                    return (
                      <div style={{
                        background: milpWins ? 'rgba(34,197,94,0.08)' : 'rgba(168,85,247,0.08)',
                        border: `1px solid ${milpWins ? 'rgba(34,197,94,0.25)' : 'rgba(168,85,247,0.25)'}`,
                        borderRadius: 8, padding: '10px 14px',
                        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                      }}>
                        <div>
                          <div style={{ color: milpWins ? '#22c55e' : '#a855f7', fontWeight: 700, fontSize: 14 }}>
                            {milpWins ? '🏆 MILP is beter' : '📏 Regelgebaseerd is beter'} met €{Math.abs(diff).toFixed(3)} ({pct.toFixed(1)}%)
                          </div>
                          <div style={{ color: 'var(--muted)', fontSize: 11, marginTop: 2 }}>
                            {milpWins
                              ? 'MILP vindt de optimale volgorde over de hele horizon — regelgebaseerd mist kansen door vaste drempelwaarden.'
                              : 'De huidige drempelwaarden passen goed bij de prijsverdeling van vandaag — MILP en regelgebaseerd convergeren.'}
                          </div>
                        </div>
                        <div style={{ textAlign: 'right', minWidth: 80, marginLeft: 16 }}>
                          <div style={{ color: milpWins ? '#22c55e' : '#a855f7', fontWeight: 700, fontSize: 20 }}>
                            {milpWins ? '+' : '-'}€{Math.abs(diff).toFixed(3)}
                          </div>
                          <div style={{ color: 'var(--muted)', fontSize: 11 }}>arbitrage</div>
                        </div>
                      </div>
                    )
                  })()}

                  <div style={{ color: 'var(--muted2)', fontSize: 11, marginTop: 10 }}>
                    ℹ️ Capaciteitstarief is identiek voor beide strategieën (zelfde hardware, zelfde piekafname klasse).
                    {hasSolar && ' Solar PV is enkel in MILP geïntegreerd — regelgebaseerd gebruikt uitsluitend nettarieven.'}
                  </div>
                </div>
              )}

              {/* ── SOC & Cumulatieve opbrengst vergelijkingsgrafieken ── */}
              {compChartData.length > 0 && (
                <div className="card">
                  <div className="card-title">📈 SOC & Cumulatieve Opbrengst (per uur)</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                    <div>
                      <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 6 }}>State of Charge (%)</div>
                      <ResponsiveContainer width="100%" height={200}>
                        <LineChart data={compChartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.25)"/>
                          <XAxis dataKey="hour" tick={{ fill: '#64748b', fontSize: 10 }} unit="h"/>
                          <YAxis tick={{ fill: '#64748b', fontSize: 10 }} unit="%" domain={[0, 100]}/>
                          <Tooltip content={<SocTip/>}/>
                          <Legend wrapperStyle={{ color: '#64748b', fontSize: 11 }}/>
                          {compChartData.some(d => d.milpSoc != null) && (
                            <Line type="monotone" dataKey="milpSoc" name="MILP" stroke="#3b82f6"
                              strokeWidth={2} dot={false}/>
                          )}
                          {compChartData.some(d => d.rbSoc != null) && (
                            <Line type="monotone" dataKey="rbSoc" name="Regelgebaseerd" stroke="#a855f7"
                              strokeWidth={2} dot={false} strokeDasharray="5 3"/>
                          )}
                          {[24, 48].map(h => compChartData.some(d => d.hour >= h) && (
                            <ReferenceLine key={h} x={h} stroke="#64748b" strokeDasharray="4 3"
                              strokeOpacity={0.5}/>
                          ))}
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                    <div>
                      <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 6 }}>Cumulatieve arbitrage (€)</div>
                      <ResponsiveContainer width="100%" height={200}>
                        <LineChart data={compChartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.25)"/>
                          <XAxis dataKey="hour" tick={{ fill: '#64748b', fontSize: 10 }} unit="h"/>
                          <YAxis tick={{ fill: '#64748b', fontSize: 10 }} unit=" €"/>
                          <Tooltip content={<RevTip/>}/>
                          <Legend wrapperStyle={{ color: '#64748b', fontSize: 11 }}/>
                          {compChartData.some(d => d.milpRev != null) && (
                            <Line type="monotone" dataKey="milpRev" name="MILP" stroke="#3b82f6"
                              strokeWidth={2} dot={false}/>
                          )}
                          {compChartData.some(d => d.rbRev != null) && (
                            <Line type="monotone" dataKey="rbRev" name="Regelgebaseerd" stroke="#a855f7"
                              strokeWidth={2} dot={false} strokeDasharray="5 3"/>
                          )}
                          {[24, 48].map(h => compChartData.some(d => d.hour >= h) && (
                            <ReferenceLine key={h} x={h} stroke="#64748b" strokeDasharray="4 3"
                              strokeOpacity={0.5}/>
                          ))}
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                </div>
              )}

              {/* ── MILP KPI's ── */}
              <div className="kpi-grid">
                {[
                  ['Netto opbrengst',    `€${(summary.total_net_revenue_eur ?? 0).toFixed(3)}`, 'na cap.tarief',   'positive'],
                  ['Arbitrage (bruto)',  `€${(summary.revenue_execute_eur   ?? 0).toFixed(3)}`, 'voor cap.tarief', 'neutral'],
                  ['Cap.tarief periode', `€${(summary.cap_tarief_period_eur ?? 0).toFixed(2)}`, `${(summary.peak_charge_kw ?? 0).toFixed(1)} kW piek`, 'negative'],
                  ['Eind-SOC',          `${(summary.final_soc_pct ?? 0).toFixed(1)}%`,         `${summary.solve_time_sec ?? '?'}s solver`, 'neutral'],
                ].map(([lbl, val, sub, cls]) => (
                  <div key={lbl} className="kpi">
                    <div className="kpi-label">{lbl}</div>
                    <div className={`kpi-value ${cls}`}>{val}</div>
                    <div className="kpi-sub">{sub}</div>
                  </div>
                ))}
              </div>

              {/* ── Per-dag opbrengst breakdown (multi-dag) ── */}
              {schedule.length > 96 && (() => {
                const numDays = Math.ceil(schedule.length / 96)
                return (
                  <div className="card" style={{ padding: '16px 20px' }}>
                    <div style={{ color: 'var(--muted)', fontSize: 11, fontWeight: 600,
                      textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 10 }}>
                      📅 Opbrengst per dag
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: `repeat(${numDays}, 1fr)`, gap: 8 }}>
                      {Array.from({ length: numDays }, (_, d) => {
                        const daySlots   = schedule.slice(d * 96, (d + 1) * 96)
                        const dayGross   = daySlots.reduce((s, r) => s + (r.net_revenue_eur ?? 0), 0)
                        const dayDate    = new Date(Date.now() + d * 86_400_000)
                        const dayLabel   = dayDate.toLocaleDateString('nl-BE', {
                          weekday: 'short', day: 'numeric', month: 'short',
                        })
                        const chSlots    = daySlots.filter(r => r.charge_kwh    > 0.001).length
                        const disSlots   = daySlots.filter(r => r.discharge_kwh > 0.001).length
                        return (
                          <div key={d} style={{
                            background: 'var(--bg)', borderRadius: 8,
                            padding: '12px 14px', border: '1px solid var(--border)', textAlign: 'center',
                          }}>
                            <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 4 }}>
                              Dag {d + 1} — {dayLabel}
                            </div>
                            <div style={{
                              color: dayGross >= 0 ? '#22c55e' : '#ef4444',
                              fontSize: 18, fontWeight: 700,
                            }}>
                              €{dayGross.toFixed(3)}
                            </div>
                            <div style={{ color: 'var(--muted2)', fontSize: 10, marginTop: 4 }}>
                              🔵 {chSlots} laden · 🟢 {disSlots} ontladen
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )
              })()}

              {/* Export */}
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <button onClick={() => exportCSV(schedule, summary)} className="btn" style={{
                  padding: '7px 16px', fontSize: 13,
                  border: '1px solid var(--border)', color: 'var(--muted)', borderRadius: 7,
                  display: 'flex', alignItems: 'center', gap: 6,
                }}>
                  📥 Exporteer CSV (Belgisch formaat)
                </button>
              </div>

              {/* MILP Dispatch grafiek */}
              {chartData.length > 0 && (
                <div className="card">
                  <div className="card-title">
                    Dispatch Schema MILP (per uur)
                    {result.label && (
                      <span style={{ color: 'var(--muted)', fontWeight: 400, marginLeft: 8, fontSize: 12 }}>
                        {result.label}
                      </span>
                    )}
                  </div>
                  <ResponsiveContainer width="100%" height={260}>
                    <BarChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.25)"/>
                      <XAxis dataKey="hour" tick={{ fill: '#64748b', fontSize: 11 }} unit="h"/>
                      <YAxis tick={{ fill: '#64748b', fontSize: 11 }} unit=" kWh"/>
                      <Tooltip content={<ChartTip/>}/>
                      <Legend wrapperStyle={{ color: '#64748b', fontSize: 11 }}/>
                      <Bar dataKey="charge"    name="Laden"    fill="#3b82f6" radius={[2, 2, 0, 0]}/>
                      <Bar dataKey="discharge" name="Ontladen" fill="#22c55e" radius={[2, 2, 0, 0]}/>
                      {/* Dagscheidingslijnen */}
                      {[24, 48].map(h => chartData.some(d => d.hour >= h) && (
                        <ReferenceLine key={h} x={h} stroke="#64748b" strokeDasharray="4 3"
                          strokeOpacity={0.6}
                          label={{ value: `Dag ${h / 24 + 1}`, position: 'top',
                            fill: '#64748b', fontSize: 10 }}/>
                      ))}
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}

              {/* Kwartier-tabel MILP */}
              {schedule.length > 0 && (
                <div className="card">
                  <div className="card-title">Kwartier Schema MILP ({schedule.length} slots)</div>
                  <div className="table-wrap">
                    <table>
                      <thead><tr>
                        <th>#</th>
                        <th>Prijs (€/MWh)</th>
                        <th>Laden (kWh)</th>
                        <th>Ontladen (kWh)</th>
                        <th>SoC (%)</th>
                        <th>P&L (€)</th>
                      </tr></thead>
                      <tbody>
                        {schedule.map((h, i) => (
                          <tr key={i}>
                            <td style={{ color: 'var(--muted)' }}>{i}</td>
                            <td style={{ color: h.price_eur_mwh < 0 ? '#ef4444' : h.price_eur_mwh < 50 ? '#22c55e' : 'var(--text)' }}>
                              {h.price_eur_mwh?.toFixed(2) ?? '—'}
                            </td>
                            <td style={{ color: '#3b82f6' }}>
                              {h.charge_kwh > 0.001 ? h.charge_kwh.toFixed(3) : '—'}
                            </td>
                            <td style={{ color: '#22c55e' }}>
                              {h.discharge_kwh > 0.001 ? h.discharge_kwh.toFixed(3) : '—'}
                            </td>
                            <td>{h.soc_pct?.toFixed(1) ?? '—'}%</td>
                            <td style={{ color: (h.net_revenue_eur ?? 0) >= 0 ? '#22c55e' : '#ef4444' }}>
                              {h.net_revenue_eur != null ? h.net_revenue_eur.toFixed(4) : '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}

          {/* ── Feature #30/#31: Battery Sizing Advisor ── */}
          <div className="card" style={{ padding: '20px 22px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <div className="card-title" style={{ margin: 0 }}>🔭 Battery Sizing Advisor</div>
              <button
                type="button"
                onClick={handleSizingAnalyse}
                disabled={sizingLoading}
                className="btn btn-primary"
                style={{ padding: '6px 14px', fontSize: 12 }}
              >
                {sizingLoading ? '⚙️ Berekenen…' : '▶ Analyseer'}
              </button>
            </div>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10 }}>
              Vergelijkt meerdere capaciteiten op basis van <strong>1 jaar ENTSO-E dag-ahead prijzen</strong>.
              Resultaat wordt gecached en hergebruikt gedurende de dag ({conn.maxInjectie} kW · η={eff}).
            </div>

            {sizingError && (
              <div style={{ color: '#ef4444', fontSize: 12, marginBottom: 8 }}>⚠️ {sizingError}</div>
            )}

            {sizingLoading && (
              <div style={{ marginBottom: 12 }}>
                {/* Label */}
                <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 6 }}>
                  {sizingMessage || '⏳ Bezig…'}
                </div>
                {/* Progress bar */}
                <div style={{
                  background: 'var(--border)', borderRadius: 6, height: 8, overflow: 'hidden',
                }}>
                  <div style={{
                    width: `${sizingProgress}%`,
                    background: 'var(--accent)',
                    height: '100%',
                    borderRadius: 6,
                    transition: 'width 0.4s ease',
                  }}/>
                </div>
                <div style={{ fontSize: 11, color: 'var(--muted2)', marginTop: 4 }}>
                  {sizingProgress}% voltooid — eerste run duurt enkele minuten; volgende klik is instant (cache)
                </div>
              </div>
            )}

            {sizingResult && (() => {
              const rows = sizingResult.results ?? []
              const daysAnalyzed = sizingResult.days_analyzed ?? '?'
              const startDate    = sizingResult.start_date   ?? ''
              const endDate      = sizingResult.end_date     ?? ''
              const bestEffIdx   = rows.reduce((bi, r, i, a) =>
                (r.annualized_per_kwh ?? r.revenue_per_kwh) > (a[bi].annualized_per_kwh ?? a[bi].revenue_per_kwh) ? i : bi, 0)
              const bestEff      = rows[bestEffIdx]
              const current      = rows.find(r => Math.abs(r.battery_kwh - battKwh) < 0.1)
              return (
                <>
                  {/* Datumrange label */}
                  <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8 }}>
                    📅 Gebaseerd op {daysAnalyzed} dagen ({startDate} → {endDate}) · geannualiseerd naar 1 jaar
                  </div>

                  {/* Staafgrafiek: jaaropbrengst + €/kWh */}
                  <ResponsiveContainer width="100%" height={220}>
                    <ComposedChart data={rows} margin={{ top: 4, right: 48, bottom: 0, left: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.25)"/>
                      <XAxis dataKey="battery_kwh" tick={{ fill: '#64748b', fontSize: 11 }} unit=" kWh"/>
                      <YAxis yAxisId="rev" tick={{ fill: '#64748b', fontSize: 11 }} unit=" €"
                        label={{ value: '€/jaar', angle: -90, position: 'insideLeft', fill: '#64748b', fontSize: 10, dy: 35 }}/>
                      <YAxis yAxisId="eff" orientation="right" tick={{ fill: '#64748b', fontSize: 11 }}
                        tickFormatter={v => `€${v.toFixed(1)}`}
                        label={{ value: '€/kWh·j', angle: 90, position: 'insideRight', fill: '#64748b', fontSize: 10, dy: -35 }}/>
                      <Tooltip
                        contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }}
                        labelStyle={{ color: 'var(--text)', fontWeight: 700 }}
                        labelFormatter={v => `${v} kWh batterij`}
                        formatter={(val, name) => [
                          name === 'Opbrengst/jaar'
                            ? `€${val.toFixed(2)}/jaar`
                            : `€${val.toFixed(2)}/kWh·jaar`,
                          name,
                        ]}
                      />
                      <Legend wrapperStyle={{ color: '#64748b', fontSize: 11 }}/>
                      <Bar yAxisId="rev" dataKey="annualized_revenue_eur" name="Opbrengst/jaar"
                        fill="#3b82f6" radius={[3, 3, 0, 0]}
                        label={{ position: 'top', fill: '#64748b', fontSize: 9,
                          formatter: v => v > 0 ? `€${Math.round(v)}` : '' }}/>
                      <Line yAxisId="eff" dataKey="annualized_per_kwh" name="€/kWh·jaar"
                        stroke="#f97316" strokeWidth={2} dot={{ fill: '#f97316', r: 4 }} type="monotone"/>
                    </ComposedChart>
                  </ResponsiveContainer>

                  {/* Samenvatting: beste ROI + huidige keuze */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 10 }}>
                    <div style={{
                      background: 'rgba(249,115,22,0.06)', border: '1px solid rgba(249,115,22,0.25)',
                      borderRadius: 8, padding: '10px 12px',
                    }}>
                      <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 4 }}>
                        🏆 Beste €/kWh rendement
                      </div>
                      <div style={{ color: '#f97316', fontSize: 18, fontWeight: 700 }}>
                        {bestEff.battery_kwh} kWh
                      </div>
                      <div style={{ color: 'var(--muted2)', fontSize: 11, marginTop: 2 }}>
                        €{bestEff.annualized_revenue_eur?.toFixed(2) ?? '—'}/jaar ·{' '}
                        €{bestEff.annualized_per_kwh?.toFixed(2) ?? '—'}/kWh·jaar
                      </div>
                    </div>
                    <div style={{
                      background: 'rgba(59,130,246,0.06)', border: '1px solid rgba(59,130,246,0.2)',
                      borderRadius: 8, padding: '10px 12px',
                    }}>
                      <div style={{ color: 'var(--muted)', fontSize: 11, marginBottom: 4 }}>
                        ⚙️ Huidige instelling
                      </div>
                      {current ? (
                        <>
                          <div style={{ color: '#60a5fa', fontSize: 18, fontWeight: 700 }}>
                            {current.battery_kwh} kWh
                          </div>
                          <div style={{ color: 'var(--muted2)', fontSize: 11, marginTop: 2 }}>
                            €{current.annualized_revenue_eur?.toFixed(2) ?? '—'}/jaar ·{' '}
                            €{current.annualized_per_kwh?.toFixed(2) ?? '—'}/kWh·jaar
                          </div>
                        </>
                      ) : (
                        <div style={{ color: 'var(--muted)', fontSize: 13 }}>
                          {battKwh} kWh (niet in analyseset)
                        </div>
                      )}
                    </div>
                  </div>
                </>
              )
            })()}
          </div>

        </div>
      </div>
    </div>
  )
}
