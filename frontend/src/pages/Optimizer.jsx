import { useState, useEffect, useRef } from 'react'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { runOptimization, pollJob } from '../api'

const POLL_INTERVAL = 2000
const CAP_EUR_KW_YEAR = 60   // Fluvius capaciteitstarief €/kW/jaar

const CONN = {
  mono: { label: 'Monofase', sub: '1×230V', maxAfname: 9.2,  maxInjectie: 5.0  },
  drie: { label: 'Driefasig', sub: '3×230V', maxAfname: 15.9, maxInjectie: 10.0 },
}

// ── Stijlhulpers ──────────────────────────────────────────────────────────────
const sec = (title) => (
  <div style={{ display:'flex', alignItems:'center', gap:8, margin:'20px 0 12px',
    color:'#6b7280', fontSize:11, fontWeight:600, letterSpacing:'0.06em', textTransform:'uppercase' }}>
    <span style={{ whiteSpace:'nowrap' }}>{title}</span>
    <div style={{ flex:1, height:1, background:'#2a2d3e' }}/>
  </div>
)

const Slider = ({ label, value, min, max, step, onChange, fmt }) => (
  <div style={{ marginBottom:12 }}>
    <div style={{ display:'flex', justifyContent:'space-between', marginBottom:5 }}>
      <span style={{ color:'#9ca3af', fontSize:13 }}>{label}</span>
      <span style={{ color:'#e2e8f0', fontSize:13, fontWeight:600 }}>{fmt ? fmt(value) : value}</span>
    </div>
    <input type="range" min={min} max={max} step={step} value={value}
      onChange={e => onChange(parseFloat(e.target.value))}
      style={{ width:'100%', accentColor:'#3b82f6' }}/>
  </div>
)

const StatusBadge = ({ status }) => {
  const cls = { pending:'badge-pending', running:'badge-running', completed:'badge-done', failed:'badge-failed' }
  const lbl = { pending:'⏳ Wachten', running:'⚙️ Bezig…', completed:'✅ Klaar', failed:'❌ Mislukt' }
  return <span className={`badge ${cls[status]||'badge-pending'}`}>{lbl[status]||status}</span>
}

const ChartTip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background:'#1a1d27', border:'1px solid #2a2d3e', borderRadius:8, padding:'10px 14px' }}>
      <div style={{ color:'#8892a4', fontSize:12, marginBottom:4 }}>Uur {label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color:p.fill, fontWeight:600, fontSize:13 }}>
          {p.name}: {p.value?.toFixed(2)} kWh
        </div>
      ))}
    </div>
  )
}

// ── Specs berekeningen ────────────────────────────────────────────────────────
function calcSpecs(battKwh, chargePow, dischargePow, minSoc, efficiency) {
  const eta   = Math.sqrt(efficiency)
  const slCh  = chargePow   * 0.25          // max kWh laden per 15-min slot
  const slDis = dischargePow * 0.25          // max kWh ontladen per 15-min slot
  const cRateCh  = (eta * slCh  * 4) / battKwh
  const cRateDis = (slDis / eta * 4) / battKwh
  const usable   = battKwh * (1 - minSoc)
  const tChMin   = usable / (eta * slCh)  * 15
  const tDisMin  = usable / (slDis / eta) * 15
  const asymMilp = dischargePow / 2.5       // vs forfait minimum
  return { cRateCh, cRateDis, tChMin, tDisMin, asymMilp, maxCRate: Math.max(cRateCh, cRateDis) }
}

function calcCap(dischargePow) {
  const peak     = Math.max(2.5, dischargePow)
  const monthly  = peak * CAP_EUR_KW_YEAR / 12
  const forfait  = 2.5  * CAP_EUR_KW_YEAR / 12
  const extra    = monthly - forfait
  return { peak, monthly, forfait, extra }
}

// ── Hoofd component ───────────────────────────────────────────────────────────
export default function Optimizer() {
  const [aansluiting, setAansluiting] = useState('mono')
  const [battKwh,  setBattKwh]  = useState(10)
  const [initSoc,  setInitSoc]  = useState(0.50)
  const [minSoc,   setMinSoc]   = useState(0.10)
  const [endSoc,   setEndSoc]   = useState(0.20)
  const [eff,      setEff]      = useState(0.92)
  const [horizon,  setHorizon]  = useState(24)
  const [dischPow, setDischPow] = useState(5.0)
  const [jobId,    setJobId]    = useState(null)
  const [job,      setJob]      = useState(null)
  const [submitting, setSub]    = useState(false)
  const [error,    setError]    = useState(null)
  const pollRef = useRef(null)

  const conn  = CONN[aansluiting]
  const specs = calcSpecs(battKwh, conn.maxAfname, dischPow, minSoc, eff)
  const cap   = calcCap(dischPow)

  // Klem discharge bij wisselen aansluiting
  useEffect(() => {
    setDischPow(prev => Math.min(prev, conn.maxInjectie))
  }, [aansluiting])

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
    e.preventDefault(); setError(null); setJob(null); setSub(true)
    try {
      const res = await runOptimization({
        battery_kwh: battKwh, efficiency: eff,
        initial_soc: initSoc, min_soc: minSoc, min_end_soc: endSoc,
        discharge_power_kw: dischPow, charge_power_kw: conn.maxAfname,
        horizon_hours: horizon, aansluiting,
      })
      setJobId(res.job_id)
    } catch (e) { setError(e.message) }
    finally { setSub(false) }
  }

  const result   = job?.result
  const schedule = result?.schedule || []
  const chartData = schedule.map((h, i) => ({
    hour: i,
    charge:    h.charge_kwh    > 0 ? h.charge_kwh    : 0,
    discharge: h.discharge_kwh > 0 ? h.discharge_kwh : 0,
  }))

  // ── C-rate kleur ─────────────────────────────────────────────────────────
  const cColor = (c) => c > 2 ? '#ef4444' : c > 1 ? '#f59e0b' : '#22c55e'
  const cIcon  = (c) => c > 2 ? '⛔' : c > 1 ? '⚠️' : '✅'

  return (
    <div>
      <div className="page-header">
        <div className="page-title">🔋 MILP Optimizer</div>
        <div className="page-sub">Batterij dispatch optimalisatie via PuLP + HiGHS solver</div>
      </div>

      <div style={{ display:'grid', gridTemplateColumns:'340px 1fr', gap:24, alignItems:'start' }}>

        {/* ════════════════ LINKS: parameters ════════════════ */}
        <div className="card" style={{ padding:'20px 22px' }}>
          <form onSubmit={handleSubmit}>

            {/* ── Aansluitingstype ── */}
            {sec('Aansluitingstype')}
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:6,
              background:'#13151f', borderRadius:10, padding:4 }}>
              {Object.entries(CONN).map(([key, c]) => {
                const on = aansluiting === key
                return (
                  <button key={key} type="button" onClick={() => setAansluiting(key)} style={{
                    padding:'10px 8px', borderRadius:8, border: on ? '1px solid rgba(59,130,246,0.5)' : '1px solid transparent',
                    background: on ? 'rgba(59,130,246,0.12)' : 'transparent',
                    color: on ? '#60a5fa' : '#6b7280', cursor:'pointer',
                    fontWeight: on ? 600 : 400, fontSize:13, transition:'all 0.15s',
                  }}>
                    <div>{c.label}</div>
                    <div style={{ fontSize:10, opacity:0.6, marginTop:2 }}>{c.sub}</div>
                  </button>
                )
              })}
            </div>
            <div style={{ marginTop:10, display:'grid', gridTemplateColumns:'1fr 1fr', gap:6 }}>
              {[
                ['Afname van net', conn.maxAfname, 'kW', '#3b82f6', 'Laadlimiet MILP'],
                ['Injectie op net', conn.maxInjectie, 'kW', '#22c55e', 'Ontlaadlimiet'],
              ].map(([lbl, val, unit, clr, sub]) => (
                <div key={lbl} style={{ background:'rgba(255,255,255,0.03)', borderRadius:8, padding:'10px 12px',
                  border:`1px solid rgba(255,255,255,0.06)` }}>
                  <div style={{ color:'#6b7280', fontSize:11 }}>{lbl}</div>
                  <div style={{ color: clr, fontSize:18, fontWeight:700, marginTop:2 }}>
                    {val} <span style={{ fontSize:12 }}>{unit}</span>
                  </div>
                  <div style={{ color:'#374151', fontSize:10, marginTop:2 }}>{sub}</div>
                </div>
              ))}
            </div>

            {/* ── Vermogen ── */}
            {sec('Vermogen')}
            <Slider label="Max injectievermogen (kW)" value={dischPow}
              min={0.5} max={conn.maxInjectie} step={0.5}
              onChange={setDischPow} fmt={v => `${v.toFixed(1)} kW`}/>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center',
              background:'rgba(34,197,94,0.06)', borderRadius:7, padding:'8px 12px',
              border:'1px solid rgba(34,197,94,0.12)', marginBottom:4 }}>
              <span style={{ color:'#6b7280', fontSize:13 }}>Laadvermogen MILP</span>
              <span style={{ color:'#22c55e', fontWeight:700, fontSize:14 }}>
                {conn.maxAfname} kW
                <span style={{ color:'#374151', fontWeight:400, fontSize:11, marginLeft:6 }}>auto</span>
              </span>
            </div>

            {/* ── Batterij ── */}
            {sec('Batterij')}
            <div className="form-row" style={{ marginBottom:12 }}>
              <label style={{ color:'#9ca3af', fontSize:13 }}>Capaciteit (kWh)</label>
              <input type="number" min={1} max={2000} step={0.5} value={battKwh}
                onChange={e => setBattKwh(parseFloat(e.target.value))}
                className="form-input"/>
            </div>
            <Slider label="Efficiëntie (round-trip)" value={eff}
              min={0.7} max={1.0} step={0.01}
              onChange={setEff} fmt={v => `${(v*100).toFixed(0)}%`}/>

            {/* ── State of Charge ── */}
            {sec('State of Charge')}
            <Slider label="Start SOC"    value={initSoc} min={0.05} max={1}    step={0.05} onChange={setInitSoc} fmt={v=>`${(v*100).toFixed(0)}%`}/>
            <Slider label="Min reserve"  value={minSoc}  min={0}    max={0.40} step={0.05} onChange={setMinSoc}  fmt={v=>`${(v*100).toFixed(0)}%`}/>
            <Slider label="Min eind-SOC" value={endSoc}  min={0.05} max={0.50} step={0.05} onChange={setEndSoc}  fmt={v=>`${(v*100).toFixed(0)}%`}/>

            {/* ── Horizon ── */}
            {sec('Horizon')}
            <div className="form-row" style={{ marginBottom:8 }}>
              <label style={{ color:'#9ca3af', fontSize:13 }}>Tijdshorizon (uur)</label>
              <input type="number" min={1} max={48} step={1} value={horizon}
                onChange={e => setHorizon(parseInt(e.target.value))}
                className="form-input"/>
            </div>

            {error && <div className="error" style={{ marginTop:8 }}>⚠️ {error}</div>}

            <button type="submit" className="btn btn-primary" style={{ marginTop:16, width:'100%' }}
              disabled={submitting || job?.status === 'running'}>
              {submitting ? 'Versturen…' : job?.status === 'running' ? '⚙️ Bezig…' : '▶ Optimaliseer'}
            </button>
          </form>
        </div>

        {/* ════════════════ RECHTS: specs + resultaten ════════════════ */}
        <div style={{ display:'flex', flexDirection:'column', gap:16 }}>

          {/* ── Batterij Specs & Validatie (altijd zichtbaar) ── */}
          <div className="card" style={{ padding:'20px 22px' }}>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:16 }}>
              <div className="card-title" style={{ margin:0 }}>🔬 Batterij Specs & Validatie</div>
              <div style={{ fontSize:12, color:'#4b5563' }}>live berekend</div>
            </div>
            <div style={{ display:'grid', gridTemplateColumns:'repeat(4, 1fr)', gap:10, marginBottom:14 }}>
              {[
                ['C-rate laden',    `${specs.cRateCh.toFixed(2)}C`,  cColor(specs.cRateCh)],
                ['C-rate ontladen', `${specs.cRateDis.toFixed(2)}C`, cColor(specs.cRateDis)],
                ['Vol laden',       `${Math.round(specs.tChMin)} min`,  '#e2e8f0'],
                ['Vol ontladen',    `${Math.round(specs.tDisMin)} min`, '#e2e8f0'],
              ].map(([lbl, val, clr]) => (
                <div key={lbl} style={{ background:'rgba(255,255,255,0.03)', borderRadius:8,
                  padding:'12px 10px', textAlign:'center', border:'1px solid rgba(255,255,255,0.05)' }}>
                  <div style={{ color:'#6b7280', fontSize:11, marginBottom:6 }}>{lbl}</div>
                  <div style={{ color: clr, fontSize:18, fontWeight:700 }}>{val}</div>
                </div>
              ))}
            </div>
            {specs.maxCRate > 2
              ? <div style={{ background:'rgba(239,68,68,0.1)', border:'1px solid rgba(239,68,68,0.3)',
                  borderRadius:7, padding:'8px 12px', fontSize:13, color:'#ef4444' }}>
                  ⛔ C-rate = {specs.maxCRate.toFixed(1)}C — te hoog, overweeg grotere batterij of minder vermogen
                </div>
              : specs.maxCRate > 1
              ? <div style={{ background:'rgba(245,158,11,0.1)', border:'1px solid rgba(245,158,11,0.3)',
                  borderRadius:7, padding:'8px 12px', fontSize:13, color:'#f59e0b' }}>
                  ⚠️ C-rate = {specs.maxCRate.toFixed(1)}C — boven 1C, controleer batterijspecs
                </div>
              : <div style={{ background:'rgba(34,197,94,0.08)', border:'1px solid rgba(34,197,94,0.2)',
                  borderRadius:7, padding:'8px 12px', fontSize:13, color:'#22c55e' }}>
                  ✅ C-rate: laden {specs.cRateCh.toFixed(2)}C / ontladen {specs.cRateDis.toFixed(2)}C
                  &nbsp;·&nbsp; ontladen is <strong>{specs.asymMilp.toFixed(1)}×</strong> sneller dan laden (vs forfait 2.5 kW)
                </div>
            }
          </div>

          {/* ── Capaciteitstarief ── */}
          <div className="card" style={{ padding:'20px 22px' }}>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:16 }}>
              <div className="card-title" style={{ margin:0 }}>💶 Capaciteitstarief (Fluvius)</div>
              <div style={{ fontSize:12, color:'#4b5563' }}>€{CAP_EUR_KW_YEAR}/kW/jaar</div>
            </div>
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:10 }}>
              {[
                ['Maandelijkse kost', `€${cap.monthly.toFixed(2)}`, '#e2e8f0'],
                ['Forfait minimum',   `€${cap.forfait.toFixed(2)}`,  '#6b7280'],
                ['Extra boven forfait', cap.extra > 0.01 ? `+€${cap.extra.toFixed(2)}` : '= forfait', cap.extra > 0.01 ? '#f59e0b' : '#22c55e'],
              ].map(([lbl, val, clr]) => (
                <div key={lbl} style={{ background:'rgba(255,255,255,0.03)', borderRadius:8,
                  padding:'12px', border:'1px solid rgba(255,255,255,0.05)' }}>
                  <div style={{ color:'#6b7280', fontSize:11, marginBottom:4 }}>{lbl}</div>
                  <div style={{ color: clr, fontSize:16, fontWeight:700 }}>{val}</div>
                </div>
              ))}
            </div>
            <div style={{ marginTop:10, fontSize:12, color:'#4b5563' }}>
              Piek: {cap.peak.toFixed(1)} kW ({conn.label}) · {cap.peak.toFixed(1)} × €{(CAP_EUR_KW_YEAR/12).toFixed(2)}/mnd
              &nbsp;·&nbsp; <em>MILP optimaliseert zijn eigen piek</em>
            </div>
          </div>

          {/* ── Job status ── */}
          {job && (
            <div className="card" style={{ padding:'16px 22px' }}>
              <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
                <div style={{ color:'#e2e8f0', fontWeight:600 }}>Job {jobId?.slice(0,8)}…</div>
                <StatusBadge status={job.status}/>
              </div>
              {job.status === 'running' && (
                <div className="loading" style={{ marginTop:10 }}>HiGHS solver bezig…</div>
              )}
            </div>
          )}

          {/* ── Resultaten ── */}
          {result && (
            <>
              <div className="kpi-grid">
                {[
                  ['Totale winst',       result.total_profit_eur?.toFixed(2),           'EUR', 'positive'],
                  ['Energiekosten',      result.total_charge_cost_eur?.toFixed(2),      'EUR', 'negative'],
                  ['Energieopbrengst',   result.total_discharge_revenue_eur?.toFixed(2),'EUR', 'positive'],
                  ['Solver',             result.solver_status || 'Optimal',             'HiGHS','neutral'],
                ].map(([lbl, val, sub, cls]) => (
                  <div key={lbl} className="kpi">
                    <div className="kpi-label">{lbl}</div>
                    <div className={`kpi-value ${cls}`} style={cls==='neutral'?{fontSize:15}:{}}>{val}</div>
                    <div className="kpi-sub">{sub}</div>
                  </div>
                ))}
              </div>

              {chartData.length > 0 && (
                <div className="card">
                  <div className="card-title">Dispatch Schema</div>
                  <ResponsiveContainer width="100%" height={260}>
                    <BarChart data={chartData} margin={{ top:4, right:8, bottom:0, left:0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e"/>
                      <XAxis dataKey="hour" tick={{ fill:'#8892a4', fontSize:11 }} unit="h"/>
                      <YAxis tick={{ fill:'#8892a4', fontSize:11 }} unit=" kWh"/>
                      <Tooltip content={<ChartTip/>}/>
                      <Bar dataKey="charge"    name="Laden"    fill="#3b82f6" radius={[2,2,0,0]}/>
                      <Bar dataKey="discharge" name="Ontladen" fill="#22c55e" radius={[2,2,0,0]}/>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}

              {schedule.length > 0 && (
                <div className="card">
                  <div className="card-title">Uurschema</div>
                  <div className="table-wrap">
                    <table>
                      <thead><tr>
                        <th>Uur</th><th>Prijs (€/MWh)</th><th>Laden (kWh)</th>
                        <th>Ontladen (kWh)</th><th>SoC (%)</th><th>P&L (€)</th>
                      </tr></thead>
                      <tbody>
                        {schedule.map((h, i) => (
                          <tr key={i}>
                            <td>{i}</td>
                            <td style={{ color: h.price<0?'#ef4444':h.price<50?'#22c55e':'#e2e8f0' }}>
                              {h.price?.toFixed(2)}</td>
                            <td style={{ color:'#3b82f6' }}>{h.charge_kwh    > 0 ? h.charge_kwh.toFixed(2)    : '—'}</td>
                            <td style={{ color:'#22c55e' }}>{h.discharge_kwh > 0 ? h.discharge_kwh.toFixed(2) : '—'}</td>
                            <td>{((h.soc??0)*100).toFixed(1)}%</td>
                            <td style={{ color: h.pnl>=0?'#22c55e':'#ef4444' }}>{h.pnl?.toFixed(2)??'—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
