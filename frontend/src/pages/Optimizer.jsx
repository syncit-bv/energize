import { useState, useEffect, useRef } from 'react'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import { runOptimization, pollJob } from '../api'

const POLL_INTERVAL = 2000

const defaultParams = {
  battery_capacity_kwh: 100,
  max_power_kw: 50,
  initial_soc: 0.5,
  min_soc: 0.1,
  max_soc: 0.9,
  charge_efficiency: 0.95,
  discharge_efficiency: 0.95,
  horizon_hours: 24,
}

const StatusBadge = ({ status }) => {
  const map = {
    pending: 'badge-pending',
    running: 'badge-running',
    completed: 'badge-done',
    failed: 'badge-failed',
  }
  const labels = { pending:'⏳ Wachten', running:'⚙️ Bezig…', completed:'✅ Klaar', failed:'❌ Mislukt' }
  return <span className={`badge ${map[status] || 'badge-pending'}`}>{labels[status] || status}</span>
}

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background:'#1a1d27', border:'1px solid #2a2d3e', borderRadius:8, padding:'10px 14px' }}>
      <div style={{ color:'#8892a4', fontSize:12 }}>Uur {label}</div>
      {payload.map((p,i) => (
        <div key={i} style={{ color: p.fill, fontWeight:600 }}>{p.name}: {p.value?.toFixed(2)} kWh</div>
      ))}
    </div>
  )
}

export default function Optimizer() {
  const [params, setParams] = useState(defaultParams)
  const [jobId, setJobId] = useState(null)
  const [job, setJob] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const pollRef = useRef(null)

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
      const res = await runOptimization(params)
      setJobId(res.job_id)
    } catch (e) { setError(e.message) }
    finally { setSubmitting(false) }
  }

  const upd = (k) => (e) => setParams(p => ({ ...p, [k]: parseFloat(e.target.value) }))

  const result = job?.result
  const schedule = result?.schedule || []
  const chartData = schedule.map((h, i) => ({
    hour: i,
    charge: h.charge_kwh > 0 ? h.charge_kwh : 0,
    discharge: h.discharge_kwh > 0 ? h.discharge_kwh : 0,
  }))

  return (
    <div>
      <div className="page-header">
        <div className="page-title">🔋 MILP Optimizer</div>
        <div className="page-sub">Batterij dispatch optimalisatie via PuLP + HiGHS solver</div>
      </div>

      <div style={{ display:'grid', gridTemplateColumns:'340px 1fr', gap:24, alignItems:'start' }}>
        {/* Form */}
        <div className="card">
          <div className="card-title">Parameters</div>
          <form onSubmit={handleSubmit}>
            <div style={{ display:'flex', flexDirection:'column', gap:14 }}>

              <fieldset style={{ border:'1px solid #2a2d3e', borderRadius:8, padding:'12px 16px' }}>
                <legend style={{ color:'#8892a4', fontSize:12, padding:'0 6px' }}>Batterij</legend>
                {[
                  ['battery_capacity_kwh', 'Capaciteit (kWh)', 10, 1000],
                  ['max_power_kw', 'Max vermogen (kW)', 5, 500],
                ].map(([k, label, min, max]) => (
                  <div key={k} className="form-row">
                    <label>{label}</label>
                    <input type="number" min={min} max={max} step="any"
                      value={params[k]} onChange={upd(k)} className="form-input"/>
                  </div>
                ))}
              </fieldset>

              <fieldset style={{ border:'1px solid #2a2d3e', borderRadius:8, padding:'12px 16px' }}>
                <legend style={{ color:'#8892a4', fontSize:12, padding:'0 6px' }}>State of Charge</legend>
                {[
                  ['initial_soc', 'Initieel (%)', 0, 1, 0.01],
                  ['min_soc', 'Minimum (%)', 0, 1, 0.01],
                  ['max_soc', 'Maximum (%)', 0, 1, 0.01],
                ].map(([k, label, min, max, step]) => (
                  <div key={k} className="form-row">
                    <label>{label}</label>
                    <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                      <input type="range" min={min} max={max} step={step}
                        value={params[k]} onChange={upd(k)} style={{ flex:1 }}/>
                      <span style={{ color:'#e2e8f0', minWidth:38, textAlign:'right', fontSize:13 }}>
                        {(params[k]*100).toFixed(0)}%
                      </span>
                    </div>
                  </div>
                ))}
              </fieldset>

              <fieldset style={{ border:'1px solid #2a2d3e', borderRadius:8, padding:'12px 16px' }}>
                <legend style={{ color:'#8892a4', fontSize:12, padding:'0 6px' }}>Efficiëntie & Horizon</legend>
                {[
                  ['charge_efficiency', 'Laad-efficiëntie', 0.7, 1, 0.01],
                  ['discharge_efficiency', 'Ontlaad-efficiëntie', 0.7, 1, 0.01],
                ].map(([k, label, min, max, step]) => (
                  <div key={k} className="form-row">
                    <label>{label}</label>
                    <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                      <input type="range" min={min} max={max} step={step}
                        value={params[k]} onChange={upd(k)} style={{ flex:1 }}/>
                      <span style={{ color:'#e2e8f0', minWidth:38, textAlign:'right', fontSize:13 }}>
                        {(params[k]*100).toFixed(0)}%
                      </span>
                    </div>
                  </div>
                ))}
                <div className="form-row">
                  <label>Horizon (uur)</label>
                  <input type="number" min={1} max={48} step={1}
                    value={params.horizon_hours} onChange={upd('horizon_hours')} className="form-input"/>
                </div>
              </fieldset>

              {error && <div className="error">⚠️ {error}</div>}

              <button type="submit" className="btn btn-primary" disabled={submitting || job?.status === 'running'}>
                {submitting ? 'Versturen…' : job?.status === 'running' ? 'Berekening loopt…' : '▶ Optimaliseer'}
              </button>
            </div>
          </form>
        </div>

        {/* Results */}
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
                <div className="kpi"><div className="kpi-label">Totale winst</div>
                  <div className="kpi-value positive">{result.total_profit_eur?.toFixed(2)}</div>
                  <div className="kpi-sub">EUR</div></div>
                <div className="kpi"><div className="kpi-label">Energiekosten</div>
                  <div className="kpi-value negative">{result.total_charge_cost_eur?.toFixed(2)}</div>
                  <div className="kpi-sub">EUR</div></div>
                <div className="kpi"><div className="kpi-label">Energieopbrengst</div>
                  <div className="kpi-value positive">{result.total_discharge_revenue_eur?.toFixed(2)}</div>
                  <div className="kpi-sub">EUR</div></div>
                <div className="kpi"><div className="kpi-label">Solver status</div>
                  <div className="kpi-value neutral" style={{ fontSize:16 }}>{result.solver_status || 'Optimal'}</div>
                  <div className="kpi-sub">HiGHS</div></div>
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
                      <Bar dataKey="charge" name="Laden" fill="#3b82f6" radius={[2,2,0,0]}/>
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
                      <thead><tr><th>Uur</th><th>Prijs (€/MWh)</th><th>Laden (kWh)</th><th>Ontladen (kWh)</th><th>SoC (%)</th><th>P&L (€)</th></tr></thead>
                      <tbody>
                        {schedule.map((h, i) => (
                          <tr key={i}>
                            <td>{i}</td>
                            <td style={{ color: h.price<0?'#ef4444':h.price<50?'#22c55e':'#e2e8f0' }}>{h.price?.toFixed(2)}</td>
                            <td style={{ color:'#3b82f6' }}>{h.charge_kwh > 0 ? h.charge_kwh.toFixed(2) : '—'}</td>
                            <td style={{ color:'#22c55e' }}>{h.discharge_kwh > 0 ? h.discharge_kwh.toFixed(2) : '—'}</td>
                            <td>{((h.soc ?? 0)*100).toFixed(1)}%</td>
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
              <div style={{ fontSize:13, marginTop:8 }}>De HiGHS solver berekent het optimale laad- en ontlaadschema op basis van dag-ahead prijzen</div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
