import { useState, useEffect, useRef } from 'react'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { runOptimization, pollJob } from '../api'

const POLL_INTERVAL = 2000

// Wettelijke vermogensgrenzen Fluvius/Synergrid
const CONN = {
  mono: { label: 'Monofase', sub: '1×230V', maxAfname: 9.2,  maxInjectie: 5.0  },
  drie: { label: 'Driefasig', sub: '3×230V', maxAfname: 15.9, maxInjectie: 10.0 },
}

const defaultBattery = {
  battery_kwh:  10.0,
  initial_soc:  0.50,
  min_soc:      0.10,
  min_end_soc:  0.20,
  efficiency:   0.92,
  horizon_hours: 24,
}

const StatusBadge = ({ status }) => {
  const cls   = { pending:'badge-pending', running:'badge-running', completed:'badge-done', failed:'badge-failed' }
  const lbl   = { pending:'⏳ Wachten', running:'⚙️ Bezig…', completed:'✅ Klaar', failed:'❌ Mislukt' }
  return <span className={`badge ${cls[status] || 'badge-pending'}`}>{lbl[status] || status}</span>
}

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background:'#1a1d27', border:'1px solid #2a2d3e', borderRadius:8, padding:'10px 14px' }}>
      <div style={{ color:'#8892a4', fontSize:12 }}>Uur {label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.fill, fontWeight:600 }}>{p.name}: {p.value?.toFixed(2)} kWh</div>
      ))}
    </div>
  )
}

export default function Optimizer() {
  const [aansluiting, setAansluiting] = useState('mono')
  const [battery, setBattery]         = useState(defaultBattery)
  const [dischargePower, setDischarge] = useState(5.0)
  const [jobId, setJobId]             = useState(null)
  const [job, setJob]                 = useState(null)
  const [submitting, setSubmitting]   = useState(false)
  const [error, setError]             = useState(null)
  const pollRef = useRef(null)

  const conn = CONN[aansluiting]

  // Klem discharge power als we wisselen naar monofase
  useEffect(() => {
    setDischarge(prev => Math.min(prev, conn.maxInjectie))
  }, [aansluiting])

  const stopPolling = () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null } }

  useEffect(() => {
    if (!jobId) return
    stopPolling()
    pollRef.current = setInterval(async () => {
      try {
        const j = await pollJob(jobId)
        setJob(j)
        if (j.status === 'completed' || j.status === 'failed') stopPolling()
      } catch (e) { setError(e.message); stopPolling() }
    }, POLL_INTERVAL)
    return stopPolling
  }, [jobId])

  const handleSubmit = async (e) => {
    e.preventDefault(); setError(null); setJob(null); setSubmitting(true)
    try {
      const payload = {
        ...battery,
        discharge_power_kw: dischargePower,
        charge_power_kw:    conn.maxAfname,   // MILP gebruikt altijd afname-limiet
        aansluiting,
      }
      const res = await runOptimization(payload)
      setJobId(res.job_id)
    } catch (e) { setError(e.message) }
    finally { setSubmitting(false) }
  }

  const updBat = (k) => (e) => setBattery(p => ({ ...p, [k]: parseFloat(e.target.value) }))

  const result   = job?.result
  const schedule = result?.schedule || []
  const chartData = schedule.map((h, i) => ({
    hour:      i,
    charge:    h.charge_kwh    > 0 ? h.charge_kwh    : 0,
    discharge: h.discharge_kwh > 0 ? h.discharge_kwh : 0,
  }))

  const optKwh = Math.round(conn.maxInjectie * 4 * 0.92)

  return (
    <div>
      <div className="page-header">
        <div className="page-title">🔋 MILP Optimizer</div>
        <div className="page-sub">Batterij dispatch optimalisatie via PuLP + HiGHS solver</div>
      </div>

      <div style={{ display:'grid', gridTemplateColumns:'340px 1fr', gap:24, alignItems:'start' }}>
        {/* ── Parameterformulier ── */}
        <div className="card">
          <div className="card-title">Parameters</div>
          <form onSubmit={handleSubmit}>
            <div style={{ display:'flex', flexDirection:'column', gap:14 }}>

              {/* ── Aansluitingstype ── */}
              <fieldset style={{ border:'1px solid #2a2d3e', borderRadius:8, padding:'12px 16px' }}>
                <legend style={{ color:'#8892a4', fontSize:12, padding:'0 6px' }}>Aansluitingstype</legend>
                <div style={{ display:'flex', gap:8, marginBottom:10 }}>
                  {Object.entries(CONN).map(([key, c]) => (
                    <button key={key} type="button"
                      onClick={() => setAansluiting(key)}
                      style={{
                        flex:1, padding:'8px 6px', fontSize:12, borderRadius:7, cursor:'pointer',
                        border: aansluiting === key ? '1px solid #3b82f6' : '1px solid #2a2d3e',
                        background: aansluiting === key ? 'rgba(59,130,246,0.15)' : '#1a1d27',
                        color: aansluiting === key ? '#60a5fa' : '#8892a4',
                        fontWeight: aansluiting === key ? 600 : 400,
                      }}>
                      {key === 'mono' ? '⚡' : '⚡⚡⚡'} {c.label}<br/>
                      <span style={{ fontSize:10, opacity:0.7 }}>{c.sub}</span>
                    </button>
                  ))}
                </div>
                <div style={{ fontSize:12, background:'rgba(59,130,246,0.06)', borderRadius:6, padding:'10px 12px', display:'flex', flexDirection:'column', gap:4 }}>
                  <div style={{ color:'#8892a4' }}>
                    🔌 Afname van net: <strong style={{ color:'#e2e8f0' }}>{conn.maxAfname} kW</strong>
                    <span style={{ marginLeft:6, color:'#4b5563', fontSize:11 }}>(laadlimiet MILP)</span>
                  </div>
                  <div style={{ color:'#8892a4' }}>
                    💡 Injectie op net: <strong style={{ color:'#e2e8f0' }}>{conn.maxInjectie} kW</strong>
                    <span style={{ marginLeft:6, color:'#4b5563', fontSize:11 }}>(ontlaadlimiet)</span>
                  </div>
                  <div style={{ color:'#4b5563', fontSize:11, marginTop:2 }}>
                    Technisch optimale batterij: ~{optKwh} kWh
                  </div>
                </div>
              </fieldset>

              {/* ── Vermogen ── */}
              <fieldset style={{ border:'1px solid #2a2d3e', borderRadius:8, padding:'12px 16px' }}>
                <legend style={{ color:'#8892a4', fontSize:12, padding:'0 6px' }}>Vermogen</legend>

                {/* Injectievermogen — slider, gecapped op maxInjectie */}
                <div className="form-row">
                  <label>Max injectievermogen (kW)</label>
                  <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                    <input type="range" min={0.5} max={conn.maxInjectie} step={0.5}
                      value={dischargePower}
                      onChange={e => setDischarge(parseFloat(e.target.value))}
                      style={{ flex:1 }}/>
                    <span style={{ color:'#e2e8f0', minWidth:44, textAlign:'right', fontSize:13 }}>
                      {dischargePower.toFixed(1)} kW
                    </span>
                  </div>
                  <div style={{ fontSize:11, color:'#4b5563', marginTop:3 }}>
                    Wettelijke limiet: {conn.maxInjectie} kW ({conn.label})
                  </div>
                </div>

                {/* Laadvermogen — automatisch = afnamelimiet, info only */}
                <div className="form-row" style={{ marginTop:8 }}>
                  <label>Laadvermogen MILP (kW)</label>
                  <div style={{ padding:'6px 10px', background:'rgba(34,197,94,0.07)', borderRadius:6,
                    border:'1px solid rgba(34,197,94,0.15)', color:'#22c55e', fontSize:13, fontWeight:600 }}>
                    {conn.maxAfname} kW
                    <span style={{ color:'#4b5563', fontWeight:400, fontSize:11, marginLeft:8 }}>
                      (auto = afnamelimiet {conn.label})
                    </span>
                  </div>
                </div>
              </fieldset>

              {/* ── Batterij ── */}
              <fieldset style={{ border:'1px solid #2a2d3e', borderRadius:8, padding:'12px 16px' }}>
                <legend style={{ color:'#8892a4', fontSize:12, padding:'0 6px' }}>Batterij</legend>
                <div className="form-row">
                  <label>Capaciteit (kWh)</label>
                  <input type="number" min={1} max={1000} step={0.5}
                    value={battery.battery_kwh} onChange={updBat('battery_kwh')} className="form-input"/>
                </div>
                <div className="form-row" style={{ marginTop:8 }}>
                  <label>Efficiëntie (round-trip)</label>
                  <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                    <input type="range" min={0.7} max={1.0} step={0.01}
                      value={battery.efficiency} onChange={updBat('efficiency')} style={{ flex:1 }}/>
                    <span style={{ color:'#e2e8f0', minWidth:38, textAlign:'right', fontSize:13 }}>
                      {(battery.efficiency * 100).toFixed(0)}%
                    </span>
                  </div>
                </div>
              </fieldset>

              {/* ── State of Charge ── */}
              <fieldset style={{ border:'1px solid #2a2d3e', borderRadius:8, padding:'12px 16px' }}>
                <legend style={{ color:'#8892a4', fontSize:12, padding:'0 6px' }}>State of Charge</legend>
                {[
                  ['initial_soc',  'Start SOC (%)'],
                  ['min_soc',      'Min SOC reserve (%)'],
                  ['min_end_soc',  'Min eind-SOC (%)'],
                ].map(([k, label]) => (
                  <div key={k} className="form-row">
                    <label>{label}</label>
                    <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                      <input type="range" min={0} max={1} step={0.05}
                        value={battery[k]} onChange={updBat(k)} style={{ flex:1 }}/>
                      <span style={{ color:'#e2e8f0', minWidth:38, textAlign:'right', fontSize:13 }}>
                        {(battery[k] * 100).toFixed(0)}%
                      </span>
                    </div>
                  </div>
                ))}
              </fieldset>

              {/* ── Horizon ── */}
              <fieldset style={{ border:'1px solid #2a2d3e', borderRadius:8, padding:'12px 16px' }}>
                <legend style={{ color:'#8892a4', fontSize:12, padding:'0 6px' }}>Horizon</legend>
                <div className="form-row">
                  <label>Tijdshorizon (uur)</label>
                  <input type="number" min={1} max={48} step={1}
                    value={battery.horizon_hours} onChange={updBat('horizon_hours')} className="form-input"/>
                </div>
              </fieldset>

              {error && <div className="error">⚠️ {error}</div>}

              <button type="submit" className="btn btn-primary"
                disabled={submitting || job?.status === 'running'}>
                {submitting ? 'Versturen…' : job?.status === 'running' ? '⚙️ Berekening loopt…' : '▶ Optimaliseer'}
              </button>
            </div>
          </form>
        </div>

        {/* ── Resultaten ── */}
        <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
          {job && (
            <div className="card">
              <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
                <div className="card-title" style={{ margin:0 }}>Job {jobId?.slice(0,8)}…</div>
                <StatusBadge status={job.status}/>
              </div>
              {job.status === 'running' && (
                <div className="loading" style={{ marginTop:12 }}>HiGHS solver bezig…</div>
              )}
            </div>
          )}

          {result && (
            <>
              <div className="kpi-grid">
                <div className="kpi">
                  <div className="kpi-label">Totale winst</div>
                  <div className="kpi-value positive">{result.total_profit_eur?.toFixed(2)}</div>
                  <div className="kpi-sub">EUR</div>
                </div>
                <div className="kpi">
                  <div className="kpi-label">Energiekosten</div>
                  <div className="kpi-value negative">{result.total_charge_cost_eur?.toFixed(2)}</div>
                  <div className="kpi-sub">EUR</div>
                </div>
                <div className="kpi">
                  <div className="kpi-label">Energieopbrengst</div>
                  <div className="kpi-value positive">{result.total_discharge_revenue_eur?.toFixed(2)}</div>
                  <div className="kpi-sub">EUR</div>
                </div>
                <div className="kpi">
                  <div className="kpi-label">Solver status</div>
                  <div className="kpi-value neutral" style={{ fontSize:16 }}>{result.solver_status || 'Optimal'}</div>
                  <div className="kpi-sub">HiGHS</div>
                </div>
              </div>

              {chartData.length > 0 && (
                <div className="card">
                  <div className="card-title">Dispatch Schema</div>
                  <ResponsiveContainer width="100%" height={280}>
                    <BarChart data={chartData} margin={{ top:4, right:8, bottom:0, left:0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e"/>
                      <XAxis dataKey="hour" tick={{ fill:'#8892a4', fontSize:11 }} unit="h"/>
                      <YAxis tick={{ fill:'#8892a4', fontSize:11 }} unit=" kWh"/>
                      <Tooltip content={<CustomTooltip/>}/>
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
                      <thead>
                        <tr><th>Uur</th><th>Prijs (€/MWh)</th><th>Laden (kWh)</th><th>Ontladen (kWh)</th><th>SoC (%)</th><th>P&L (€)</th></tr>
                      </thead>
                      <tbody>
                        {schedule.map((h, i) => (
                          <tr key={i}>
                            <td>{i}</td>
                            <td style={{ color: h.price < 0 ? '#ef4444' : h.price < 50 ? '#22c55e' : '#e2e8f0' }}>
                              {h.price?.toFixed(2)}
                            </td>
                            <td style={{ color:'#3b82f6' }}>{h.charge_kwh    > 0 ? h.charge_kwh.toFixed(2)    : '—'}</td>
                            <td style={{ color:'#22c55e' }}>{h.discharge_kwh > 0 ? h.discharge_kwh.toFixed(2) : '—'}</td>
                            <td>{((h.soc ?? 0) * 100).toFixed(1)}%</td>
                            <td style={{ color: h.pnl >= 0 ? '#22c55e' : '#ef4444' }}>{h.pnl?.toFixed(2) ?? '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}

          {!job && (
            <div className="card" style={{ textAlign:'center', padding:'48px 32px', color:'#4b5563' }}>
              <div style={{ fontSize:48, marginBottom:12 }}>⚡</div>
              <div style={{ fontSize:16 }}>Stel de parameters in en klik op <strong style={{ color:'#3b82f6' }}>Optimaliseer</strong></div>
              <div style={{ fontSize:13, marginTop:8, color:'#374151' }}>
                De HiGHS solver berekent het optimale laad- en ontlaadschema op basis van dag-ahead prijzen
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
