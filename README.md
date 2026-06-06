# Energize EMS — Smart Battery & Grid Platform

**SyncIT BV** | Belgisch Energy Management System voor slim batterijbeheer, arbitrage en grid services.

---

## Architectuur

```
┌─────────────────────────┐        ┌──────────────────────────┐
│   React Frontend        │  HTTPS │   FastAPI Backend        │
│   (Vite + React)        │◄──────►│   ems-api.onrender.com   │
│   energize-ems.onrender │        │   /api/*  + /docs        │
└─────────────────────────┘        └──────────┬───────────────┘
                                              │
                          ┌───────────────────┼───────────────────┐
                          ▼                   ▼                   ▼
                   ENTSO-E API          Elia Open Data     MILP Optimizer
                   (dag-ahead)          (imbalans/wind)    (PuLP + HiGHS)
```

> **Legacy**: de originele Streamlit dashboard (`streamlit_dashboard.py`) blijft beschikbaar
> als referentie en fallback, maar wordt niet actief verder ontwikkeld.

---

## Huidige staat

| Laag | Status | Technologie |
|---|---|---|
| FastAPI backend | ✅ Volledig gebouwd | FastAPI 0.111, Pydantic v2 |
| MILP optimizer | ✅ Productierijp | PuLP + HiGHS, async jobs |
| ENTSO-E integratie | ✅ Live | entsoe-py |
| Elia integratie | ✅ Live | elia-py (imbalans + solar/wind) |
| Deployment config | ✅ Klaar | Render.com (render.yaml) |
| React frontend | 🔨 In ontwikkeling | React + Vite |

---

## API Endpoints

De volledige interactieve documentatie staat op **`/docs`** (Swagger UI).

### Prijsdata
| Method | Endpoint | Beschrijving |
|---|---|---|
| GET | `/api/prices/day-ahead` | ENTSO-E dag-ahead prijzen (EUR/MWh) |
| GET | `/api/prices/history?days=30` | Historische prijsdata |

### Elia Grid Data
| Method | Endpoint | Beschrijving |
|---|---|---|
| GET | `/api/elia/imbalance` | Belgische onbalans (NRV, MIP/MDP) |
| GET | `/api/elia/solar-wind` | Zon- en windproductie forecast |

### MILP Optimalisatie (async)
| Method | Endpoint | Beschrijving |
|---|---|---|
| POST | `/api/optimization/run` | Start batterij-optimalisatie (achtergrond) |
| GET | `/api/jobs/{job_id}` | Peil status: `pending / running / completed / failed` |

### Meta
| Method | Endpoint | Beschrijving |
|---|---|---|
| GET | `/health` | Status check (gebruikt door Render) |
| GET | `/docs` | Swagger UI |
| GET | `/redoc` | ReDoc documentatie |

---

## Lokaal draaien

### Vereisten
- Python 3.11+
- Git

### Backend opstarten
```bash
git clone https://github.com/syncit-bv/energize.git
cd energize
pip install -r requirements.txt

# API keys instellen
export ENTSOE_API_KEY="jouw-entsoe-key"   # transparency.entsoe.eu

# Server starten
uvicorn app.main:app --reload --port 8000
```

Backend draait op: http://localhost:8000
Swagger docs op: http://localhost:8000/docs

### Legacy Streamlit dashboard (optioneel)
```bash
# Maak .streamlit/secrets.toml aan:
# entsoe_key = "jouw-key"

streamlit run streamlit_dashboard.py
```

---

## Deployment op Render.com

Het project bevat een `render.yaml` met twee services:

| Service | Naam | URL |
|---|---|---|
| FastAPI backend | `ems-api` | `https://ems-api.onrender.com` |
| Streamlit frontend (legacy) | `energize-ems` | `https://energize-ems.onrender.com` |

### Environment variables instellen op Render
Ga naar je service → **Environment** tab:

| Key | Service | Verplicht |
|---|---|---|
| `ENTSOE_API_KEY` | ems-api | Ja |
| `CORS_ORIGINS` | ems-api | Ja (bv. `https://energize-ems.onrender.com`) |
| `ENTSOE_KEY` | energize-ems | Ja |

> ⚠️ Zet nooit API keys in `.streamlit/secrets.toml` of commit ze naar GitHub.
> Gebruik altijd environment variables op Render.

---

## Projectstructuur

```
energize/
├── app/                          # FastAPI applicatie
│   ├── main.py                   # Entry point, CORS, router registratie
│   ├── models/
│   │   └── schemas.py            # Pydantic request/response modellen
│   ├── routers/
│   │   ├── prices.py             # /api/prices/* (ENTSO-E)
│   │   ├── elia.py               # /api/elia/* (Elia Open Data)
│   │   ├── optimization.py       # /api/optimization/run (MILP)
│   │   └── jobs.py               # /api/jobs/{id} (job polling)
│   └── services/
│       └── job_manager.py        # Async achtergrondtaken
├── static/                       # React frontend build output (hier deployen)
├── milp_optimizer.py             # MILP kern (PuLP + HiGHS)
├── entsoe_client.py              # ENTSO-E API wrapper
├── elia_client.py                # Elia Open Data wrapper
├── streamlit_dashboard.py        # Legacy dashboard (referentie)
├── requirements.txt              # Python dependencies
├── render.yaml                   # Render.com deployment config
├── start.sh                      # Streamlit startup script (legacy service)
└── Procfile                      # Alternatief startcommando (uvicorn)
```

---

## Roadmap

### Fase 1 — Backend (✅ Afgerond)
- [x] FastAPI backend fundament (routers, schemas, job manager)
- [x] ENTSO-E dag-ahead prijzen API
- [x] Elia imbalans + solar/wind data API
- [x] Async MILP optimalisatie endpoint
- [x] Render.com deployment configuratie

### Fase 2 — Frontend (🔨 Volgende)
- [ ] React + Vite project setup
- [ ] Prijsgrafiek met dag-ahead data (Recharts/Chart.js)
- [ ] Batterij-optimizer UI (parameters instellen + resultaat visualiseren)
- [ ] Elia data dashboard (imbalans, solar/wind)
- [ ] SOC-curve en arbitrage-resultaten weergeven

### Fase 3 — Productie features
- [ ] Dagelijkse automatisatie (cron na 15:00 ENTSO-E publicatie)
- [ ] Yuso aggregator koppeling (real-time imbalans + aFRR biedingen)
- [ ] PV forecast integratie (weer API + eigen verbruiksprofiel)
- [ ] Multi-asset support (thuisbatterij + EV)
- [ ] Marstek Venus V3.0 lokale integratie (via Raspberry Pi bridge)

---

## Tech Stack

| Laag | Technologie |
|---|---|
| Backend | Python 3.11, FastAPI, Pydantic v2, Uvicorn |
| Optimalisatie | PuLP, HiGHS solver, Pandas, NumPy |
| Data | ENTSO-E Transparency Platform, Elia Open Data |
| Frontend | React, Vite (in ontwikkeling) |
| Hosting | Render.com (Frankfurt EU) |
| Versioning | GitHub (syncit-bv/energize) |

---

*SyncIT BV — Belgisch smart energy platform voor prosumers en energy communities.*
