import axios from 'axios'

const BASE = import.meta.env.VITE_API_URL || ''

const api = axios.create({ baseURL: BASE })

// Prijsdata
export const fetchDayAhead = (days = 7) =>
  api.get('/api/prices/day-ahead', { params: { days } }).then(r => r.data)

export const fetchHistory = (days = 30) =>
  api.get('/api/prices/history', { params: { days } }).then(r => r.data)

// D+1 prijssignaal
export const fetchTomorrowStatus = () =>
  api.get('/api/prices/tomorrow/status').then(r => r.data)

export const triggerTomorrowCheck = () =>
  api.post('/api/prices/tomorrow/check').then(r => r.data)

// Elia
export const fetchImbalance = (date) =>
  api.get('/api/elia/imbalance', { params: { date } }).then(r => r.data)

export const fetchSolarWind = (date) =>
  api.get('/api/elia/solar-wind', { params: { date } }).then(r => r.data)

// MILP Optimalisatie
export const runOptimization = (payload) =>
  api.post('/api/optimization/run', payload).then(r => r.data)

export const pollJob = (jobId) =>
  api.get(`/api/jobs/${jobId}`).then(r => r.data)

// Feature #22: aanbevolen start-SOC op basis van gisteren's MILP-resultaat
export const fetchYesterdaySoc = () =>
  api.get('/api/optimization/yesterday-soc').then(r => r.data)

// Feature #30/#31: Battery Sizing Advisor — MILP over volledig jaar ENTSO-E data
export const fetchBatterySizing = ({ power_kw, efficiency, days = 365 }) =>
  api.get('/api/optimization/battery-sizing', { params: { power_kw, efficiency, days } }).then(r => r.data)
