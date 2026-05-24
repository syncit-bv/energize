# EMS Belgium MVP - Smart Battery & Grid Platform

**Doel**: Eigen EMS software platform bouwen voor slim batterijbeheer op het Belgische elektriciteitsnet. 
Focus op arbitrage, "free/paid electricity" charging bij negatieve prijzen, grid balancing (equilibratie) en verdienen aan disbalans via aggregator (Yuso).

## MVP v0.1 (huidige status)
- **Data layer**: Parser voor ENTSO-E day-ahead prijzen (XML → clean DataFrame)
- **Backtester**: Rule-based battery arbitrage simulator (10 kWh voorbeeld batterij)
- **Dashboard**: Interactieve Streamlit app met prijsgrafieken, SOC, acties en cumulatieve revenue
- **Key insight uit data**: 32 dagen met negatieve prijzen in de dataset, extreme uitschieters tot -499 €/MWh (1 mei 2026). 
  Dit creëert unieke kansen: je wordt betaald om te laden (absorbeer overtollige zonne-energie) + later verkopen bij hoge prijzen.

## Resultaten backtest (25 apr - 3 mei 2026, 10 kWh batterij, 5 kW power)
- Net revenue: ~7.89 €
- Op 1 mei alone: +1.40 € (tijdens extreme negatieve prijzen rond 11u-12u30)
- 8 dagen met negatieve prijzen in window → "free electricity" + grid support

## Hoe starten
1. Zorg dat de XML in `/home/workdir/attachments/` staat (of pas pad aan)
2. `cd ems_mvp`
3. `python price_parser.py` → genereert prices_belgium.parquet
4. `python battery_arbitrage_backtester.py` → backtest + dashboard plot (png)
5. `streamlit run streamlit_dashboard.py` → interactieve web dashboard (pas parameters live aan)

## Volgende stappen (roadmap)
- [ ] Volledige MILP optimalisatie (PuLP) i.p.v. rules (multi-objective: cost + battery health + grid support)
- [ ] Integratie PV forecast (weer API) + eigen verbruiksprofiel
- [ ] Yuso aggregator koppeling → real-time imbalance prijzen + biedingen op onbalansmarkt (aFRR etc.)
- [ ] Behind-the-meter optimalisatie (self-consumption + V2G Tesla)
- [ ] Multi-asset support (thuisbatterij + EV + eventueel extra sites)
- [ ] GitHub repo + CI voor tests
- [ ] Vennootschap fiscale optimalisatie + ROI calculator

## Tech stack (voorgesteld)
- Python + Pandas + PuLP (optimization)
- Streamlit / Plotly Dash (dashboard)
- TimescaleDB of InfluxDB (time-series prijzen + metingen)
- FastAPI (backend API voor later multi-user platform)
- MQTT / Home Assistant (lokale real-time sturing)
- Yuso / Elia API (imbalance + flexibility)

## Waarom eigen platform?
- Volledige controle over Belgische markt (negatieve prijzen, Elia rules)
- Data ownership + geen vendor lock-in
- Uitbreidbaar naar SaaS/platform voor andere prosumers of energy communities
- Combinatie met jouw bestaande setup (6300 Wp PV, Tesla, vennootschap)

Contact / collab: Maak een GitHub repo aan met deze bestanden + de XML, dan kunnen we via branches/PRs verder bouwen. Of zeg waar je de setup makkelijk wil doen (Codespaces, Replit, lokale devcontainer...).

Laten we dit tot een killer EMS platform maken dat écht geld verdient aan grid disbalance én het Belgische net helpt equilibreren. ⚡🇧🇪