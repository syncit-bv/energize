# Fluxy EMS — Projectgeheugen voor Claude

## Wat is dit?
Fluxy EMS is een Energy Management System voor Belgische thuisbatterijen.
- **Doel:** optimaliseer laad/ontlaad-strategie op basis van ENTSO-E dag-ahead prijzen (HiGHS MILP-solver)
- **Vorige versie:** Streamlit-app (Python only)
- **Huidige versie:** React + FastAPI, gedeployed op Render.com

## Architectuur
```
energize/
├── app/              ← FastAPI backend (Python)
│   └── main.py       ← /optimization/run, /day-ahead, /elia endpoints
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── Optimizer.jsx   ← Hoofdpagina: MILP + rule-based simulatie
│       │   ├── Prices.jsx      ← Dag-ahead prijzen + D+1 intelligentie
│       │   └── Elia.jsx        ← Elia live data (imbalans, wind, zon)
│       ├── App.jsx             ← Routing + dark/light toggle
│       └── index.css           ← CSS custom properties (themasysteem)
└── static/           ← Pre-built Vite output (gecommit in git, door Render geserveerd)
```

## Deployment
- **URL:** https://fluxy-hh5k.onrender.com/
- **Platform:** Render.com, auto-deploy op `git push origin main`
- **Render serveert static/ rechtstreeks** — Render bouwt NIET zelf. Dus altijd:
  1. `npm run build` in `frontend/`
  2. `git add static/`
  3. Commit broncode + static/ samen
  4. `git push origin main`
- **Env var op Render:** `ENTSOE_API_KEY` (nooit committen in code)
- **secrets.toml** staat in `.gitignore` — oude key is gecompromitteerd, vervang via transparency.entsoe.eu

## Themasysteem (CSS custom properties)
Altijd `var(--bg)`, `var(--surface)`, `var(--border)`, `var(--text)`, `var(--muted)`, `var(--muted2)`, `var(--accent)` gebruiken.
Recharts SVG kan geen CSS vars → gebruik concrete hex: `rgba(100,116,139,0.25)`, `#64748b`.

## Technische keuzes & waarom
- **`useMemo` voor rule-based simulatie:** herberekent instant bij slider-aanpassing, geen extra API-call nodig
- **`latestPrices` state:** sla ENTSO-E prijzen op na fetch, zodat rule-based al draait vóór MILP klaar is
- **Pre-built static in git:** Render Free tier ondersteunt geen buildstap voor deze setup
- **shadcn/ui & dnd-kit: NIET gebruikt** — vermijden; eigen CSS met custom properties volstaat, minder dependencies
- **Net revenue formule:** `(dischargeKwh - chargeKwh) * price / 1000` — werkt correct bij negatieve prijzen

## Afgewerkte features (commits op main)
| # | Feature | Commit |
|---|---------|--------|
| 17 | Dark/light mode toggle | ee34edc |
| 18 | Elia datumkiezer fix (vandaag als default) | 09ea938 |
| 19 | Datumindicator op Dag-ahead Prijzen pagina | 09ea938 |
| 15 | PV-solar slider (kWp + solar_forecast) | 629a511 |
| 16 | CSV-export optimalisatieschema | 629a511 |
| 14 | D+1 intelligentiepanel op Prijzen-pagina | 629a511 |
| 20 | Rule-based simulatie + MILP vergelijking | 5c22d02 + 45bae79 |
| 24 | D+1 prijssignaal — auto-detectie + frontend badge | 95bef41 + a57fbb8 |
| 25 | 1d default + D+1 overlay op prijsgrafiek | 67606bc |
| 26 | 90d/180d/1j knoppen + Nu-lijn + Rolling horizon | d563e66 |
| 22 | Gisteren's SOC als aanbevolen start-SOC | 9832c1c |
| 29 | Mono vs Driefasig vergelijkingstabel | (huidig) |

## Openstaande features
| # | Feature | Omschrijving |
|---|---------|-------------|
| 21 | Scenario vergelijking | Grotendeels afgedekt door #20-vergelijkingskaart; aparte pagina wellicht niet nodig |
| - | Battery Sizing Advisor | Hoeveel kWh batterij is optimaal voor jouw profiel? |
| - | Multi-dag MILP horizon | Optimizer over meerdere dagen i.p.v. 1 dag |
| - | Elia: extra tabbladen | Solar PV forecast, Wind surplus, live imbalans ods161 |

## Stijl & UX-principes
- Altijd streven naar beste gebruikerservaring
- Progressive disclosure: toon resultaten zodra ze beschikbaar zijn (rule-based vóór MILP)
- Uitleg geven waarom technische keuzes gemaakt worden (gebruiker wil dit leren)
- Geen onnodige dependencies toevoegen

## Workflow checklist voor elke feature
```bash
# 1. Code aanpassen in frontend/src/
# 2. Bouwen
cd frontend && npm run build
# 3. Alles stagen + committen
cd ..
git add frontend/src/ static/
git commit -m "feat: beschrijving (#tasknummer)"
# 4. Pushen → Render deployt automatisch
git push origin main
```
