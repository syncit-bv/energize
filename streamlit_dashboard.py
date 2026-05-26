#!/usr/bin/env python3
"""
EMS Belgium MVP Dashboard
Run with: streamlit run streamlit_dashboard.py
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
from datetime import date, timedelta, datetime, timezone

from milp_optimizer import (
    optimize_battery_schedule,
    optimize_battery_schedule_solar,
    estimate_own_solar_kwh,
)

try:
    from entsoe_client import EntsoeClient
    ENTSOE_AVAILABLE = True
except ImportError:
    ENTSOE_AVAILABLE = False

try:
    from electricity_maps_client import ElectricityMapsClient
    ELECTRICITY_MAPS_AVAILABLE = True
except ImportError:
    ELECTRICITY_MAPS_AVAILABLE = False

try:
    from elia_client import EliaClient
    ELIA_AVAILABLE = True
except ImportError:
    ELIA_AVAILABLE = False

try:
    from congestion_client import CongestionClient
    CONGESTION_AVAILABLE = True
except ImportError:
    CONGESTION_AVAILABLE = False

try:
    from nodes_client import NodesClient
    NODES_AVAILABLE = True
except ImportError:
    NODES_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
NL_MONTHS = {1:'jan',2:'feb',3:'mrt',4:'apr',5:'mei',6:'jun',
             7:'jul',8:'aug',9:'sep',10:'okt',11:'nov',12:'dec'}

def fdate(d: date) -> str:
    """Format date as '26 mei'."""
    return f"{d.day} {NL_MONTHS[d.month]}"

def now_cet() -> datetime:
    """Current time in CET/CEST (UTC+1/+2). Simple offset from UTC."""
    import time as _t
    utc_offset = -(_t.timezone if not _t.daylight else _t.altzone)
    # Streamlit usually runs local; just use local time
    return datetime.now()

def day_ahead_published() -> bool:
    """Day-ahead prices for D+1 are published around 12:30-13:00 CET."""
    return now_cet().hour >= 13

def _set_period(start: date, end: date):
    """Central helper to update period session state and clear stale MILP."""
    st.session_state.date_start           = start
    st.session_state.date_end             = end
    st.session_state["date_range_picker"] = (start, end)
    st.session_state.milp_schedule        = None
    st.session_state.milp_summary         = None
    st.session_state.milp_pending         = False

# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="EMS Belgium MVP", layout="wide", page_icon="⚡")
st.title("⚡ EMS Belgium — Battery & Grid Intelligence Dashboard")
st.markdown(
    "**MVP Prototype** | Belgische day-ahead prijzen | "
    "Smart arbitrage + gratis laden bij negatieve prijzen | Grid balancing"
)

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar — Battery & Strategy Parameters
# ─────────────────────────────────────────────────────────────────────────────
st.sidebar.header("🔋 Batterij & Strategie")
battery_kwh      = st.sidebar.slider("Capaciteit (kWh)", 5.0, 30.0, 10.0, 0.5)
max_power_kw     = st.sidebar.slider("Max vermogen (kW)", 2.0, 11.0, 5.0, 0.5)
charge_thresh    = st.sidebar.slider("Laden onder (€/MWh)", 0, 80, 50)
discharge_thresh = st.sidebar.slider("Ontladen boven (€/MWh)", 100, 250, 160)
negative_boost   = st.sidebar.checkbox("Agressief laden bij negatieve prijs", value=True)
min_soc_pct      = st.sidebar.slider("Min SOC reserve (%)", 0, 30, 10, 1)
min_end_soc_pct  = st.sidebar.slider("Min End-SOC (%)", 10, 50, 20, 5,
    help="Min SOC op het einde van de horizon. Bij multi-dag MILP is dit het einde van de laatste dag.")

st.sidebar.markdown("---")
st.sidebar.subheader("🚀 MILP Optimalisatie")

# Initial SOC — auto-suggest previous run's end SOC
prev_final_soc = None
if st.session_state.get("milp_summary"):
    prev_final_soc = st.session_state.milp_summary.get("final_soc_pct")

if prev_final_soc is not None:
    st.sidebar.caption(f"💡 Vorige run eindigde op **{prev_final_soc:.1f}%** SOC")
    use_prev = st.sidebar.checkbox(
        f"Start op {prev_final_soc:.1f}% (vorige run)", value=True, key="use_prev_soc"
    )
    default_initial = prev_final_soc / 100 if use_prev else 0.50
else:
    default_initial = 0.50

initial_soc_pct = st.sidebar.slider(
    "Start SOC (%)", 10, 100, int(default_initial * 100), 5,
    help="Werkelijke batterij-SOC nu. In productie: uitlezen uit BMS."
)

own_kwp = st.sidebar.slider(
    "Eigen PV-vermogen (kWp)", 0.0, 20.0, 6.3, 0.1,
    help="Jouw zonnepanelen vermogen. 0 = geen eigen PV. Wordt gebruikt voor MILP+Solar scenario."
)

if st.sidebar.button("🚀 Run MILP Optimalisatie", type="primary", use_container_width=True):
    st.session_state.milp_pending      = True
    st.session_state.scenarios_pending = False
    st.session_state.milp_schedule     = None
    st.session_state.milp_summary      = None
    st.session_state.milp_initial_soc  = initial_soc_pct / 100

if st.sidebar.button("🔬 Vergelijk alle scenario's", type="secondary", use_container_width=True,
                      help="Vergelijkt: Rule-based | MILP basis | MILP+Day-ahead | MILP+Solar"):
    st.session_state.scenarios_pending = True
    st.session_state.milp_pending      = False
    st.session_state.scenarios         = {}
    st.session_state.milp_initial_soc  = initial_soc_pct / 100

st.sidebar.metric("Max energie/slot (15 min)", f"{max_power_kw * 0.25:.2f} kWh")

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar — Data Sources
# ─────────────────────────────────────────────────────────────────────────────
st.sidebar.markdown("---")
st.sidebar.subheader("📡 Prijsdata Ophalen")

# Helper to fetch and merge new prices into df_prices
def _merge_prices(new_df: pd.DataFrame):
    """Merge new prices into existing session_state df_prices, no duplicates."""
    existing = st.session_state.get("df_prices", pd.DataFrame())
    if existing.empty:
        st.session_state.df_prices = new_df
    else:
        merged = pd.concat([existing, new_df]).drop_duplicates(
            subset="datetime").sort_values("datetime").reset_index(drop=True)
        st.session_state.df_prices = merged

if ENTSOE_AVAILABLE:
    with st.sidebar.expander("🔌 ENTSO-E (aanbevolen — gratis, onbeperkt)", expanded=True):
        st.caption("Gratis key via transparency.entsoe.eu")
        entsoe_key = st.text_input(
            "ENTSO-E API Key", type="password",
            value=st.session_state.get("entsoe_key") or st.secrets.get("entsoe_key", ""),
            key="entsoe_key_input"
        )
        if entsoe_key:
            st.session_state.entsoe_key = entsoe_key

        # Preset fetch buttons
        c1, c2, c3 = st.columns(3)
        fetch_days = None
        if c1.button("7 d",  use_container_width=True): fetch_days = 7
        if c2.button("30 d", use_container_width=True): fetch_days = 30
        if c3.button("90 d", use_container_width=True): fetch_days = 90

        if fetch_days:
            if not entsoe_key:
                st.error("Vul je ENTSO-E API key in.")
            else:
                with st.spinner(f"Ophalen {fetch_days} dagen via ENTSO-E…"):
                    try:
                        client  = EntsoeClient(entsoe_key)
                        end     = date.today() + timedelta(days=1)
                        start   = end - timedelta(days=fetch_days)
                        new_df  = client.get_day_ahead_prices(start, end)
                        if not new_df.empty:
                            _merge_prices(new_df)
                            st.success(f"✅ {len(new_df)} slots geladen!")
                            st.rerun()
                        else:
                            st.warning("Geen data ontvangen.")
                    except Exception as e:
                        st.error(str(e)[:150])
else:
    st.sidebar.caption("entsoe_client.py niet gevonden.")

if ELECTRICITY_MAPS_AVAILABLE:
    with st.sidebar.expander("🌍 Electricity Maps (sandbox = 1 dag)", expanded=False):
        st.caption("Sandbox = intentioneel onnauwkeurig, beperkt tot 24u. Gebruik ENTSO-E voor backtest.")
        em_key = st.text_input(
            "EM API Key", type="password",
            value=st.session_state.get("em_key") or st.secrets.get("em_key", ""),
            key="em_key_input"
        )
        if em_key:
            st.session_state.em_key = em_key
        if st.button("📥 Fetch (combined)", key="btn_em"):
            if not em_key:
                st.error("API key nodig.")
            else:
                try:
                    with st.spinner("Fetching Electricity Maps…"):
                        em_client = ElectricityMapsClient(em_key)
                        new_df    = em_client.get_day_ahead_prices("BE")
                        if not new_df.empty:
                            _merge_prices(new_df)
                            st.success(f"✅ {len(new_df)} slots geladen!")
                            st.rerun()
                        else:
                            st.warning("Geen data.")
                except Exception as e:
                    st.error(str(e)[:150])

# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data
def load_parquet():
    p = Path("prices_belgium.parquet")
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()

if "df_prices" not in st.session_state:
    st.session_state.df_prices = load_parquet()
if "milp_pending"       not in st.session_state: st.session_state.milp_pending       = False
if "scenarios_pending"  not in st.session_state: st.session_state.scenarios_pending  = False
if "scenarios"          not in st.session_state: st.session_state.scenarios          = {}
if "milp_schedule" not in st.session_state: st.session_state.milp_schedule = None
if "milp_summary"  not in st.session_state: st.session_state.milp_summary  = None

df = st.session_state.df_prices

if df.empty:
    st.warning("Geen prijsdata. Gebruik de ENTSO-E fetch in de sidebar of upload een bestand.")
    uploaded = st.file_uploader("Upload ENTSO-E XML of prices_belgium.parquet",
                                type=["xml", "parquet"])
    if uploaded:
        if uploaded.name.endswith(".parquet"):
            st.session_state.df_prices = pd.read_parquet(uploaded)
            st.rerun()
        elif uploaded.name.endswith(".xml"):
            Path("temp_upload.xml").write_bytes(uploaded.getvalue())
            from price_parser import parse_entsoe_prices
            st.session_state.df_prices = parse_entsoe_prices("temp_upload.xml")
            st.rerun()

if st.session_state.df_prices.empty:
    st.info("Tip: registreer gratis op transparency.entsoe.eu en gebruik de ENTSO-E knop in de sidebar.")
    st.stop()

df       = st.session_state.df_prices
today    = date.today()
tomorrow = today + timedelta(days=1)
min_date = df["datetime"].min().date()
max_date = df["datetime"].max().date()

# ─────────────────────────────────────────────────────────────────────────────
# Date selection — buttons with actual dates
# ─────────────────────────────────────────────────────────────────────────────
_week_start  = max(min_date, today - timedelta(days=6))
_month_start = max(min_date, today.replace(day=1))

if "date_start" not in st.session_state:
    _set_period(_week_start, today)

# Compute button active states
_ds = st.session_state.date_start
_de = st.session_state.date_end
is_today    = (_ds == today    and _de == today)
is_tomorrow = (_ds == tomorrow and _de == tomorrow)
is_week     = (_ds == _week_start  and _de == today)
is_month    = (_ds == _month_start and _de == today)

st.subheader("📅 Periode")
c1, c2, c3, c4, c5 = st.columns(5)

# Today button
with c1:
    label_today = f"📅 {fdate(today)}"
    if st.button(label_today,
                 type="primary" if is_today else "secondary",
                 use_container_width=True):
        _set_period(today, today); st.rerun()

# Day-ahead button (tomorrow's prices)
with c2:
    tomorrow_available = max_date >= tomorrow
    label_da = f"📈 {fdate(tomorrow)} ▸"
    if st.button(label_da,
                 type="primary" if is_tomorrow else "secondary",
                 use_container_width=True,
                 disabled=not tomorrow_available,
                 help="Day-ahead prijzen voor morgen. Beschikbaar na ~13:00 CET vandaag."
                       if not tomorrow_available else "Toont day-ahead prijzen voor morgen."):
        _set_period(tomorrow, tomorrow); st.rerun()

# Week button
with c3:
    label_week = f"📆 {fdate(_week_start)}–{fdate(today)}"
    if st.button(label_week,
                 type="primary" if is_week else "secondary",
                 use_container_width=True):
        _set_period(_week_start, today); st.rerun()

# Month button
with c4:
    label_month = f"🗓️ {NL_MONTHS[today.month].capitalize()} {today.year}"
    if st.button(label_month,
                 type="primary" if is_month else "secondary",
                 use_container_width=True):
        _set_period(_month_start, today); st.rerun()

# Custom date picker
with c5:
    pass  # spacer; date_input below is full-width

date_range = st.date_input(
    "Of kies eigen periode:",
    min_value=min_date,
    max_value=max(max_date, tomorrow),
    key="date_range_picker",
)
if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
    if date_range[0] != _ds or date_range[1] != _de:
        _set_period(date_range[0], date_range[1])
        # Don't rerun immediately — let auto-fetch below handle it

# ─────────────────────────────────────────────────────────────────────────────
# Auto-fetch missing data for selected period
# ─────────────────────────────────────────────────────────────────────────────
sel_start = st.session_state.date_start
sel_end   = st.session_state.date_end

# Check what's available vs what's requested
available_dates = set(df["datetime"].dt.date.unique()) if not df.empty else set()
requested_dates = set(
    sel_start + timedelta(days=i)
    for i in range((sel_end - sel_start).days + 1)
    if (sel_start + timedelta(days=i)) <= today  # don't request future beyond tomorrow
)
missing_dates = requested_dates - available_dates

if missing_dates and st.session_state.get("entsoe_key"):
    missing_start = min(missing_dates)
    missing_end   = max(missing_dates) + timedelta(days=1)
    st.info(
        f"📡 Ontbrekende data voor {len(missing_dates)} dag(en) "
        f"({fdate(missing_start)} → {fdate(max(missing_dates))}). "
        f"Ophalen via ENTSO-E…"
    )
    try:
        client = EntsoeClient(st.session_state.entsoe_key)
        new_df = client.get_day_ahead_prices(missing_start, missing_end)
        if not new_df.empty:
            _merge_prices(new_df)
            df = st.session_state.df_prices
            st.success(f"✅ {len(new_df)} slots geladen voor geselecteerde periode!")
            st.rerun()
    except Exception as e:
        st.warning(f"Auto-fetch mislukt: {e}. Controleer je ENTSO-E key in de sidebar.")

elif missing_dates:
    st.warning(
        f"Geen data voor {len(missing_dates)} dag(en) in geselecteerde periode. "
        "Vul je ENTSO-E key in de sidebar in om automatisch op te halen."
    )

# Filter data for selected period
mask   = ((df["datetime"].dt.date >= sel_start) &
          (df["datetime"].dt.date <= sel_end))
sim_df = df[mask].copy()

# ─────────────────────────────────────────────────────────────────────────────
# Day-ahead intelligence panel
# ─────────────────────────────────────────────────────────────────────────────
da_published = day_ahead_published()
tomorrow_in_df = not df[df["datetime"].dt.date == tomorrow].empty

st.markdown("---")
da_col1, da_col2 = st.columns([2, 1])

with da_col1:
    if da_published and tomorrow_in_df:
        st.success(
            f"✅ **Day-ahead prijzen beschikbaar** voor {fdate(tomorrow)}  "
            f"(gepubliceerd na 13:00 CET). "
            "MILP kan nu optimaliseren over vandaag + morgen voor maximale winst."
        )
    elif da_published and not tomorrow_in_df:
        st.warning(
            f"⏳ Day-ahead gepubliceerd (na 13:00), maar {fdate(tomorrow)} "
            "staat nog niet in je dataset. Gebruik de ENTSO-E fetch knop om morgen's "
            "prijzen op te halen — daarna kan MILP multi-dag optimaliseren."
        )
    else:
        now_h = now_cet().hour
        mins_left = (13 * 60) - (now_h * 60 + now_cet().minute)
        st.info(
            f"🕐 Day-ahead prijzen voor {fdate(tomorrow)} worden gepubliceerd om ~13:00 CET "
            f"(nog ~{mins_left // 60}u{mins_left % 60:02d}). "
            "MILP optimaliseert enkel voor de geselecteerde periode."
        )

with da_col2:
    # Show tomorrow's price preview if available
    tomorrow_df = df[df["datetime"].dt.date == tomorrow]
    if not tomorrow_df.empty:
        avg_p = tomorrow_df["price_eur_mwh"].mean()
        min_p = tomorrow_df["price_eur_mwh"].min()
        max_p = tomorrow_df["price_eur_mwh"].max()
        st.metric(f"{fdate(tomorrow)} gem. prijs", f"{avg_p:.1f} €/MWh",
                  delta=f"min {min_p:.0f} / max {max_p:.0f} €/MWh",
                  delta_color="off")

# ─────────────────────────────────────────────────────────────────────────────
# Price chart for selected period
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("---")
period_label = (f"{fdate(sel_start)}" if sel_start == sel_end
                else f"{fdate(sel_start)} → {fdate(sel_end)}")
st.subheader(f"💹 Prijsoverzicht — {period_label}  ({len(sim_df)} slots)")

if sim_df.empty:
    st.warning("Geen prijsdata voor de geselecteerde periode.")
    st.stop()

fig_price = go.Figure()
# Color differently: actual vs day-ahead forecast
actual_df   = sim_df[sim_df["datetime"].dt.date <= today]
forecast_df = sim_df[sim_df["datetime"].dt.date >  today]

if not actual_df.empty:
    fig_price.add_trace(go.Scatter(
        x=actual_df["datetime"], y=actual_df["price_eur_mwh"],
        mode="lines", name="Day-ahead (gerealiseerd)", line=dict(color="steelblue", width=2)
    ))
if not forecast_df.empty:
    fig_price.add_trace(go.Scatter(
        x=forecast_df["datetime"], y=forecast_df["price_eur_mwh"],
        mode="lines", name="Day-ahead (morgen)", line=dict(color="orange", width=2, dash="dot")
    ))

fig_price.add_hline(y=charge_thresh,    line_dash="dash", line_color="green",
                    annotation_text="Laaddrempel")
fig_price.add_hline(y=discharge_thresh, line_dash="dash", line_color="red",
                    annotation_text="Ontlaaddrempel")
fig_price.update_layout(title=f"Day-ahead Elektriciteitsprijzen België (€/MWh)",
                         xaxis_title="Tijd", yaxis_title="€/MWh")
st.plotly_chart(fig_price, use_container_width=True)

neg_count = (sim_df["price_eur_mwh"] < 0).sum()
if neg_count > 0:
    st.success(
        f"🎉 {neg_count} kwartieren met **negatieve prijzen** in deze periode "
        "→ gratis / betaald laden + grid support!"
    )

# ─────────────────────────────────────────────────────────────────────────────
# MILP auto-run block (runs before charts so charts pick up results)
# ─────────────────────────────────────────────────────────────────────────────
if st.session_state.milp_pending:
    # Build MILP input: selected period + tomorrow if day-ahead available
    if da_published and tomorrow_in_df and sel_end >= today:
        # Multi-day: extend horizon through tomorrow
        tomorrow_prices = df[df["datetime"].dt.date == tomorrow].copy()
        milp_input = pd.concat([sim_df, tomorrow_prices]).drop_duplicates(
            "datetime").sort_values("datetime").reset_index(drop=True)
        horizon_label = f"{fdate(sel_start)} → {fdate(tomorrow)} (multi-dag)"
        milp_info_txt = (
            f"🌅 **Multi-dag MILP** over {len(milp_input)} slots "
            f"({horizon_label}). MILP bepaalt zelf het optimale SOC "
            "aan het einde van vandaag op basis van morgen's prijzen."
        )
    else:
        milp_input    = sim_df.copy()
        horizon_label = period_label
        milp_info_txt = (
            f"📋 MILP optimaliseert {len(milp_input)} slots ({horizon_label}). "
            + ("Day-ahead voor morgen nog niet beschikbaar." if not da_published
               else "Voeg morgen's data toe voor multi-dag optimalisatie.")
        )

    n_slots = len(milp_input)
    n_days  = round(n_slots / 96, 1)

    with st.status(
        f"⚙️  MILP — {n_slots} slots ({n_days} dagen) oplossen…", expanded=True
    ) as solve_status:
        st.write(milp_info_txt)
        try:
            milp_schedule, milp_summary = optimize_battery_schedule(
                milp_input,
                battery_kwh=battery_kwh,
                max_power_kw=max_power_kw,
                min_soc=min_soc_pct / 100,
                min_end_soc=min_end_soc_pct / 100,
                initial_soc=st.session_state.get("milp_initial_soc", 0.50),
                time_horizon_hours=None,
            )
            milp_summary["horizon_label"]   = horizon_label
            milp_summary["is_multiday"]     = da_published and tomorrow_in_df
            st.session_state.milp_schedule  = milp_schedule
            st.session_state.milp_summary   = milp_summary
            st.session_state.milp_pending   = False
            solve_status.update(
                label=f"✅  Opgelost in {milp_summary['solve_time_sec']} s "
                      f"| {milp_summary['solver_iterations']:,} iteraties "
                      f"| Status: {milp_summary['status']}",
                state="complete"
            )
        except Exception as e:
            st.session_state.milp_pending = False
            solve_status.update(label=f"❌  MILP mislukt: {e}", state="error")
            st.exception(e)

    st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# Scenario comparison runner (runs all 4, stores in session_state.scenarios)
# ─────────────────────────────────────────────────────────────────────────────
if st.session_state.get("scenarios_pending"):
    init_soc   = st.session_state.get("milp_initial_soc", 0.50)
    milp_args  = dict(
        battery_kwh=battery_kwh, max_power_kw=max_power_kw,
        min_soc=min_soc_pct / 100, min_end_soc=min_end_soc_pct / 100,
        initial_soc=init_soc, time_horizon_hours=None,
    )

    # Build day-ahead extended input if available
    da_pub        = day_ahead_published()
    tomorrow_data = df[df["datetime"].dt.date == tomorrow]
    tomorrow_avail = not tomorrow_data.empty

    if da_pub and tomorrow_avail and sel_end >= today:
        milp_da_input = pd.concat([sim_df, tomorrow_data]).drop_duplicates(
            "datetime").sort_values("datetime").reset_index(drop=True)
        da_label = f"Day-ahead beschikbaar ({fdate(tomorrow)})"
    else:
        milp_da_input = sim_df.copy()
        da_label = "Geen day-ahead (periode-only)"

    total_steps = 4 if own_kwp > 0 else 3
    prog = st.progress(0, text="Scenario's voorbereiden…")

    scenarios = {}
    errors    = {}

    try:
        prog.progress(10, text="▶ Scenario 1/4: MILP Basis (geselecteerde periode)…")
        sch1, s1 = optimize_battery_schedule(sim_df, label="MILP Basis", **milp_args)
        scenarios["milp_basic"] = (sch1, s1)
        prog.progress(35, text="▶ Scenario 2/4: MILP + Day-ahead…")
    except Exception as e:
        errors["milp_basic"] = str(e)
        prog.progress(35)

    try:
        sch2, s2 = optimize_battery_schedule(
            milp_da_input, label=f"MILP+DA ({da_label})", **milp_args)
        scenarios["milp_da"] = (sch2, s2)
        prog.progress(65, text="▶ Scenario 3/4: MILP + Day-ahead + Solar…")
    except Exception as e:
        errors["milp_da"] = str(e)
        prog.progress(65)

    if own_kwp > 0:
        try:
            # Fetch solar data for scenario 4
            solar_kwh = pd.Series(dtype=float)
            if ELIA_AVAILABLE:
                try:
                    ec       = EliaClient()
                    df_sol   = ec.get_solar_forecast()
                    if df_sol.empty:
                        df_sol = ec.get_historical_solar(sel_start, sel_end + timedelta(days=1))
                    if not df_sol.empty:
                        solar_kwh = estimate_own_solar_kwh(df_sol, own_kwp=own_kwp)
                except Exception:
                    solar_kwh = pd.Series(dtype=float)

            sch3, s3 = optimize_battery_schedule_solar(
                milp_da_input, solar_kwh, label="MILP+DA+Solar", **milp_args)
            s3["solar_own_kwp"] = own_kwp
            scenarios["milp_solar"] = (sch3, s3)
        except Exception as e:
            errors["milp_solar"] = str(e)
    else:
        prog.progress(90)

    prog.progress(100, text="✅ Alle scenario's berekend!")

    st.session_state.scenarios         = scenarios
    st.session_state.scenarios_pending = False
    st.session_state.scenario_errors   = errors

    # Also store last MILP+DA as the main milp_schedule for the charts above
    if "milp_da" in scenarios:
        sch_da, summ_da = scenarios["milp_da"]
        st.session_state.milp_schedule = sch_da
        st.session_state.milp_summary  = summ_da

    st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
def quick_simulate(data, cap_kwh, pwr_kw, ch_thresh, dis_thresh,
                   neg_boost, min_soc=0.10, init_soc=0.50):
    soc     = init_soc
    cap_mwh = cap_kwh / 1000
    max_e   = (pwr_kw * 0.25) / 1000
    results = []
    cum_rev = 0.0
    for _, row in data.iterrows():
        p = row["price_eur_mwh"]; action = "HOLD"; e_mwh = 0.0; rev = 0.0
        if p < 0 and neg_boost:
            e = min(max_e, (1 - soc) * cap_mwh / 0.96)
            if e > 0.0001:
                e_mwh = e; soc += e_mwh * 0.96 / cap_mwh
                rev = -e_mwh * p; action = "CHARGE (NEG)"
        elif p < ch_thresh:
            e = min(max_e, (1 - soc) * cap_mwh / 0.96)
            if e > 0.0001:
                e_mwh = e; soc += e_mwh * 0.96 / cap_mwh
                rev = -e_mwh * p; action = "CHARGE"
        elif p > dis_thresh:
            dp = min(max_e, max(0.0, (soc - min_soc) * cap_mwh * 0.96))
            if dp > 0.0001:
                e_mwh = dp; soc -= e_mwh / (cap_mwh * 0.96)
                rev = e_mwh * p; action = "DISCHARGE"
        cum_rev += rev
        results.append({"datetime": row["datetime"], "price": p, "action": action,
                         "energy_kwh": e_mwh * 1000, "revenue": rev,
                         "soc": soc * 100, "cum_rev": cum_rev})
    return pd.DataFrame(results)

sim = quick_simulate(sim_df, battery_kwh, max_power_kw, charge_thresh,
                     discharge_thresh, negative_boost, min_soc_pct / 100,
                     initial_soc_pct / 100)

milp_df   = st.session_state.milp_schedule
milp_summ = st.session_state.milp_summary
milp_ready = milp_df is not None

# ─────────────────────────────────────────────────────────────────────────────
# KPI row
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("🔋 Simulatie Resultaten")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Net Revenue (Rule-based)", f"{sim['cum_rev'].iloc[-1]:.2f} €")
m2.metric("Totaal geladen", f"{sim['energy_kwh'].sum():.1f} kWh")
m3.metric("Gem. SOC", f"{sim['soc'].mean():.1f} %")
if milp_ready:
    m4.metric("Net Revenue (MILP)",
              f"{milp_summ['total_net_revenue_eur']:.2f} €",
              delta=f"{milp_summ['total_net_revenue_eur'] - sim['cum_rev'].iloc[-1]:+.2f} € vs Rule-based")
else:
    m4.metric("MILP Revenue", "—", help="Druk op '🚀 Run MILP' in de sidebar")

# ─────────────────────────────────────────────────────────────────────────────
# Rule-based actions chart
# ─────────────────────────────────────────────────────────────────────────────
fig_rb = go.Figure()
fig_rb.add_trace(go.Scatter(x=sim["datetime"], y=sim["price"], mode="lines",
    name="Prijs", line=dict(color="lightgray")))
fig_rb.add_trace(go.Scatter(
    x=sim[sim["action"].str.contains("CHARGE")]["datetime"],
    y=sim[sim["action"].str.contains("CHARGE")]["price"],
    mode="markers", name="LADEN", marker=dict(color="green", size=7)))
fig_rb.add_trace(go.Scatter(
    x=sim[sim["action"] == "DISCHARGE"]["datetime"],
    y=sim[sim["action"] == "DISCHARGE"]["price"],
    mode="markers", name="ONTLADEN", marker=dict(color="red", size=7)))
fig_rb.update_layout(title="Prijs + Rule-based Acties", xaxis_title="Tijd", yaxis_title="€/MWh")
st.plotly_chart(fig_rb, use_container_width=True)

# ─────────────────────────────────────────────────────────────────────────────
# SOC chart — Rule-based (always) + MILP overlay (when available)
# ─────────────────────────────────────────────────────────────────────────────
fig_soc = go.Figure()
fig_soc.add_trace(go.Scatter(x=sim["datetime"], y=sim["soc"],
    mode="lines", name="Rule-based SOC",
    line=dict(color="royalblue", width=2)))
if milp_ready:
    # Only show MILP SOC for slots within the selected period (not extended tomorrow)
    milp_today = milp_df[milp_df["datetime"].dt.date <= sel_end]
    fig_soc.add_trace(go.Scatter(x=milp_today["datetime"], y=milp_today["soc_pct"],
        mode="lines", name="MILP Optimaal SOC",
        line=dict(color="#00AA00", width=2.5, dash="dot")))
fig_soc.add_hline(y=min_soc_pct, line_dash="dash", line_color="orange",
    annotation_text=f"Min {min_soc_pct}% reserve", annotation_position="top right")
fig_soc.update_yaxes(range=[0, 100], title="SOC (%)")
title_soc = "Battery State of Charge (%)"
if milp_ready: title_soc += " — Rule-based vs MILP"
fig_soc.update_layout(title=title_soc, xaxis_title="Tijd")
st.plotly_chart(fig_soc, use_container_width=True)

# ─────────────────────────────────────────────────────────────────────────────
# Revenue chart — Rule-based + MILP
# ─────────────────────────────────────────────────────────────────────────────
fig_rev = go.Figure()
fig_rev.add_trace(go.Scatter(x=sim["datetime"], y=sim["cum_rev"],
    mode="lines", name="Rule-based", fill="tozeroy",
    line=dict(color="royalblue", width=1.5)))
if milp_ready:
    milp_today = milp_df[milp_df["datetime"].dt.date <= sel_end].copy()
    milp_today["cum_rev"] = milp_today["net_revenue_eur"].cumsum()
    fig_rev.add_trace(go.Scatter(x=milp_today["datetime"], y=milp_today["cum_rev"],
        mode="lines", name="MILP Optimaal", fill="tozeroy",
        fillcolor="rgba(0,170,0,0.10)",
        line=dict(color="#00AA00", width=2.5, dash="dot")))
title_rev = "Cumulatieve Revenue (€)"
if milp_ready: title_rev += " — Rule-based vs MILP"
fig_rev.update_layout(title=title_rev, xaxis_title="Tijd", yaxis_title="€")
st.plotly_chart(fig_rev, use_container_width=True)

# ─────────────────────────────────────────────────────────────────────────────
# MILP detail section
# ─────────────────────────────────────────────────────────────────────────────
if milp_ready:
    st.markdown("---")
    is_multi = milp_summ.get("is_multiday", False)
    st.subheader(
        f"🟢 MILP Resultaten — {milp_summ.get('horizon_label', '')}"
        + (" 🌅 Multi-dag" if is_multi else "")
    )

    if is_multi:
        st.info(
            "🌅 **Multi-dag optimalisatie**: MILP kende de prijzen van vandaag én morgen. "
            f"Het heeft het einde-SOC van vandaag automatisch afgestemd op morgen's "
            f"prijsprofiel voor maximale totale winst."
        )

    # Solver stats
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Status",            milp_summ["status"])
    s2.metric("Solve time",        f"{milp_summ['solve_time_sec']} s")
    s3.metric("Simplex iteraties", f"{milp_summ['solver_iterations']:,}")
    s4.metric("Slots",             f"{milp_summ['num_slots']}")

    # Multi-day SOC chart (full horizon including tomorrow)
    if is_multi:
        st.markdown("#### 🌅 MILP Volledig Horizon (vandaag + morgen)")
        midnight = datetime.combine(tomorrow, datetime.min.time())
        fig_multi = go.Figure()
        fig_multi.add_trace(go.Scatter(
            x=milp_df["datetime"], y=milp_df["soc_pct"],
            mode="lines", name="SOC (MILP multi-dag)",
            line=dict(color="#00AA00", width=2.5)))
        fig_multi.add_vline(x=midnight.timestamp() * 1000,
            line_dash="dash", line_color="purple",
            annotation_text="Middernacht →", annotation_position="top left")
        fig_multi.add_hline(y=min_soc_pct, line_dash="dash", line_color="orange",
            annotation_text=f"Min {min_soc_pct}%")
        fig_multi.update_yaxes(range=[0, 100], title="SOC (%)")
        fig_multi.update_layout(title="Multi-dag SOC — MILP kiest optimale SOC aan middernacht",
                                 xaxis_title="Tijd")
        st.plotly_chart(fig_multi, use_container_width=True)

        # Show midnight SOC
        midnight_soc = milp_df[milp_df["datetime"].dt.date == today]["soc_pct"].iloc[-1] \
                       if not milp_df[milp_df["datetime"].dt.date == today].empty else None
        if midnight_soc:
            st.metric(
                f"Optimale SOC aan middernacht (begin {fdate(tomorrow)})",
                f"{midnight_soc:.1f} %",
                help="MILP heeft dit automatisch gekozen op basis van morgen's prijzen. "
                     "Hoog = morgen vroeg zijn er hoge prijzen om te ontladen. "
                     "Laag = morgen vroeg zijn er negatieve prijzen om te laden."
            )

    # Financial breakdown
    st.markdown("#### 💰 Financieel Overzicht")
    pos_ch  = milp_df[(milp_df["charge_kwh"] > 0) & (milp_df["price_eur_mwh"] > 0)]
    neg_ch  = milp_df[(milp_df["charge_kwh"] > 0) & (milp_df["price_eur_mwh"] <= 0)]
    dis_df  = milp_df[milp_df["discharge_kwh"] > 0]
    f1, f2, f3, f4 = st.columns(4)
    f1.metric("Net Revenue (MILP)",    f"{milp_summ['total_net_revenue_eur']:.2f} €",
              delta=f"{milp_summ['total_net_revenue_eur'] - sim['cum_rev'].iloc[-1]:+.2f} vs Rule-based")
    f2.metric("Kosten (laden, p>0)",   f"-{abs(pos_ch['net_revenue_eur'].sum()):.2f} €",
              delta_color="inverse")
    f3.metric("Inkomsten (p≤0)",       f"+{abs(neg_ch['net_revenue_eur'].sum()):.2f} €")
    f4.metric("Ontlaad-inkomsten",     f"+{dis_df['net_revenue_eur'].sum():.2f} €")

    # Comparison table
    st.markdown("#### 📊 Vergelijking (geselecteerde periode)")
    milp_period = milp_df[milp_df["datetime"].dt.date <= sel_end]
    comp = pd.DataFrame({
        "Metric":     ["Net Revenue (€)", "Geladen (kWh)", "Ontladen (kWh)", "Eind SOC (%)"],
        "Rule-based": [round(sim["cum_rev"].iloc[-1], 2),
                       round(sim["energy_kwh"].sum(), 1),
                       round(sim[sim["action"]=="DISCHARGE"]["energy_kwh"].sum(), 1),
                       round(sim["soc"].iloc[-1], 1)],
        "MILP":       [round(milp_period["net_revenue_eur"].sum(), 2),
                       round(milp_period["charge_kwh"].sum(), 2),
                       round(milp_period["discharge_kwh"].sum(), 2),
                       round(milp_period["soc_pct"].iloc[-1], 1)],
    })
    st.dataframe(comp, use_container_width=True, hide_index=True)

    # MILP actions chart
    st.markdown("#### 📋 MILP Acties")
    fig_ma = go.Figure()
    fig_ma.add_trace(go.Scatter(x=milp_df["datetime"], y=milp_df["price_eur_mwh"],
        mode="lines", name="Prijs", line=dict(color="lightgray")))
    c_m = milp_df["charge_kwh"]    > 0.01
    d_m = milp_df["discharge_kwh"] > 0.01
    fig_ma.add_trace(go.Scatter(x=milp_df[c_m]["datetime"], y=milp_df[c_m]["price_eur_mwh"],
        mode="markers", name="LADEN",    marker=dict(color="green", size=9, symbol="triangle-up")))
    fig_ma.add_trace(go.Scatter(x=milp_df[d_m]["datetime"], y=milp_df[d_m]["price_eur_mwh"],
        mode="markers", name="ONTLADEN", marker=dict(color="red",   size=9, symbol="triangle-down")))
    if is_multi:
        fig_ma.add_vline(x=datetime.combine(tomorrow, datetime.min.time()).timestamp() * 1000,
            line_dash="dash", line_color="purple", annotation_text="Morgen →")
    fig_ma.update_layout(title="MILP Optimale Acties", xaxis_title="Tijd", yaxis_title="€/MWh")
    st.plotly_chart(fig_ma, use_container_width=True)

    # Action table
    am = (milp_df["charge_kwh"] > 0.01) | (milp_df["discharge_kwh"] > 0.01)
    dtl = milp_df[am][["datetime","price_eur_mwh","charge_kwh","discharge_kwh",
                         "net_revenue_eur","soc_pct"]].copy()
    dtl["Type"] = dtl["net_revenue_eur"].apply(
        lambda x: "🟢 Inkomsten" if x > 0 else ("🔴 Kosten" if x < 0 else "⚪"))
    dtl = dtl.rename(columns={"datetime":"Tijd","price_eur_mwh":"Prijs (€/MWh)",
        "charge_kwh":"Laden (kWh)","discharge_kwh":"Ontladen (kWh)",
        "net_revenue_eur":"Slot Rev (€)","soc_pct":"SOC (%)"})
    st.dataframe(dtl, use_container_width=True, hide_index=True, height=320)

    if milp_summ.get("is_multiday"):
        mid_soc = milp_df[milp_df["datetime"].dt.date == today]["soc_pct"].iloc[-1] \
                  if not milp_df[milp_df["datetime"].dt.date == today].empty else None
        if mid_soc:
            st.success(
                f"💡 **Aanbeveling**: zorg dat je batterij om middernacht op "
                f"**{mid_soc:.0f}% SOC** staat — dit is het MILP-optimale startpunt voor morgen."
            )

    with st.expander("🔍 CBC Solver log", expanded=False):
        st.code(milp_summ.get("solver_log", "—"), language="text")

    if st.button("🔄 Reset MILP"):
        st.session_state.milp_schedule = None
        st.session_state.milp_summary  = None
        st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# Scenario vergelijking (4 scenario's: Rule-based | MILP basis | +DA | +Solar)
# ─────────────────────────────────────────────────────────────────────────────
if st.session_state.get("scenarios"):
    st.markdown("---")
    st.subheader("🔬 Scenario Vergelijking — De Kracht van EMS Optimalisatie")
    st.markdown(
        "Vier scenario's naast elkaar: van eenvoudige regelgebaseerde logica tot "
        "volledige MILP-optimalisatie met day-ahead én solar intelligence."
    )

    scen        = st.session_state.scenarios
    scen_errors = st.session_state.get("scenario_errors", {})

    # ── Kleurenpalet per scenario ──────────────────────────────────────────
    COLORS = {
        "rule_based":  ("royalblue",  "Rule-based",           "— —"),
        "milp_basic":  ("#E67E22",    "MILP Basis",           "—"),
        "milp_da":     ("#27AE60",    "MILP + Day-ahead",     "dot"),
        "milp_solar":  ("#8E44AD",    "MILP + DA + Solar ☀️", "dashdot"),
    }

    # ── Samenvattingstabel ─────────────────────────────────────────────────
    rows = []
    rb_rev = sim["cum_rev"].iloc[-1]

    rows.append({
        "Scenario":       "1️⃣ Rule-based",
        "Net Revenue (€)": f"{rb_rev:.2f}",
        "Geladen (kWh)":  f"{sim['energy_kwh'].sum():.1f}",
        "Ontladen (kWh)": f"{sim[sim['action']=='DISCHARGE']['energy_kwh'].sum():.1f}",
        "Eind SOC (%)":   f"{sim['soc'].iloc[-1]:.1f}",
        "Verbetering":    "—",
        "Solver":         "n.v.t.",
    })

    emoji = ["2️⃣", "3️⃣", "4️⃣"]
    keys  = ["milp_basic", "milp_da", "milp_solar"]
    names = ["MILP Basis", "MILP + Day-ahead", "MILP + DA + Solar ☀️"]

    for i, (key, name) in enumerate(zip(keys, names)):
        if key in scen:
            _, s = scen[key]
            rev  = s["total_net_revenue_eur"]
            rows.append({
                "Scenario":        f"{emoji[i]} {name}",
                "Net Revenue (€)":  f"{rev:.2f}",
                "Geladen (kWh)":    f"{s['total_charged_kwh']:.1f}",
                "Ontladen (kWh)":   f"{s['total_discharged_kwh']:.1f}",
                "Eind SOC (%)":     f"{s['final_soc_pct']:.1f}",
                "Verbetering":      f"+{rev - rb_rev:.2f} €" if rev > rb_rev else f"{rev - rb_rev:.2f} €",
                "Solver":           f"{s['status']} | {s['solve_time_sec']}s | {s['solver_iterations']:,} iter",
            })
        elif key in scen_errors:
            rows.append({
                "Scenario": f"{emoji[i]} {name}",
                "Net Revenue (€)": "❌",
                "Geladen (kWh)": "—", "Ontladen (kWh)": "—",
                "Eind SOC (%)": "—", "Verbetering": "—",
                "Solver": scen_errors[key][:60],
            })

    comp_df = pd.DataFrame(rows)
    st.dataframe(comp_df, use_container_width=True, hide_index=True)

    # ── Revenue vergelijking bar chart ─────────────────────────────────────
    bar_labels = [r["Scenario"] for r in rows]
    bar_values = []
    bar_colors = ["royalblue", "#E67E22", "#27AE60", "#8E44AD"]
    for r in rows:
        try:
            bar_values.append(float(r["Net Revenue (€)"]))
        except ValueError:
            bar_values.append(0.0)

    fig_bar = go.Figure(go.Bar(
        x=bar_labels, y=bar_values,
        marker_color=bar_colors[:len(bar_labels)],
        text=[f"{v:.2f} €" for v in bar_values],
        textposition="outside",
    ))
    fig_bar.update_layout(
        title="Net Revenue per Scenario (€)",
        yaxis_title="€", xaxis_title="",
        showlegend=False,
        height=350,
    )
    st.plotly_chart(fig_bar, use_container_width=True)

    # ── SOC overlay ────────────────────────────────────────────────────────
    fig_soc_all = go.Figure()
    fig_soc_all.add_trace(go.Scatter(
        x=sim["datetime"], y=sim["soc"],
        mode="lines", name="Rule-based",
        line=dict(color="royalblue", width=1.5, dash="dash")))

    for key, name, dash_style in [
        ("milp_basic", "MILP Basis",         "dash"),
        ("milp_da",    "MILP + Day-ahead",   "dot"),
        ("milp_solar", "MILP + DA + Solar ☀️","dashdot"),
    ]:
        if key in scen:
            sch_k, _ = scen[key]
            # Toon enkel de geselecteerde periode (niet tomorrow extension)
            sch_period = sch_k[sch_k["datetime"].dt.date <= sel_end]
            color = COLORS[key][0]
            fig_soc_all.add_trace(go.Scatter(
                x=sch_period["datetime"], y=sch_period["soc_pct"],
                mode="lines", name=name,
                line=dict(color=color, width=2, dash=dash_style)))

    fig_soc_all.add_hline(y=min_soc_pct, line_dash="dash", line_color="orange",
        annotation_text=f"Min {min_soc_pct}% reserve")
    fig_soc_all.update_yaxes(range=[0, 100], title="SOC (%)")
    fig_soc_all.update_layout(
        title="Battery State of Charge — Alle Scenario's",
        xaxis_title="Tijd",
        legend=dict(orientation="h", y=-0.2))
    st.plotly_chart(fig_soc_all, use_container_width=True)

    # ── Cumulatieve revenue overlay ────────────────────────────────────────
    fig_rev_all = go.Figure()
    fig_rev_all.add_trace(go.Scatter(
        x=sim["datetime"], y=sim["cum_rev"],
        mode="lines", name="Rule-based",
        line=dict(color="royalblue", width=1.5, dash="dash")))

    for key, name, dash_style in [
        ("milp_basic", "MILP Basis",         "dash"),
        ("milp_da",    "MILP + Day-ahead",   "dot"),
        ("milp_solar", "MILP + DA + Solar ☀️","dashdot"),
    ]:
        if key in scen:
            sch_k, _ = scen[key]
            sch_period = sch_k[sch_k["datetime"].dt.date <= sel_end].copy()
            sch_period["cum_rev"] = sch_period["net_revenue_eur"].cumsum()
            color = COLORS[key][0]
            fig_rev_all.add_trace(go.Scatter(
                x=sch_period["datetime"], y=sch_period["cum_rev"],
                mode="lines", name=name,
                line=dict(color=color, width=2, dash=dash_style)))

    fig_rev_all.update_layout(
        title="Cumulatieve Revenue — Alle Scenario's (€)",
        xaxis_title="Tijd", yaxis_title="€",
        legend=dict(orientation="h", y=-0.2))
    st.plotly_chart(fig_rev_all, use_container_width=True)

    # ── Solar detail (alleen als scenario 4 beschikbaar) ──────────────────
    if "milp_solar" in scen:
        sch_sol, s_sol = scen["milp_solar"]
        if "charge_solar_kwh" in sch_sol.columns:
            solar_total = sch_sol["charge_solar_kwh"].sum()
            grid_total  = sch_sol["charge_grid_kwh"].sum()
            if solar_total > 0.01:
                st.markdown("#### ☀️ Solar Self-Consumption Detail")
                sc1, sc2, sc3 = st.columns(3)
                sc1.metric("Geladen via solar (gratis)", f"{solar_total:.1f} kWh",
                           help="Laden vanuit eigen PV — geen gridkost")
                sc2.metric("Geladen via net",            f"{grid_total:.1f} kWh")
                sc3.metric("Solar waarde",
                           f"{solar_total * sch_sol.merge(pd.DataFrame({'datetime': sch_sol['datetime'], 'p': sch_sol['price_eur_mwh']}), on='datetime', how='left')['p'].mean() / 1000:.2f} €" if False else
                           f"~{solar_total * abs(sch_sol['price_eur_mwh'].mean()) / 1000:.2f} €",
                           help="Geschatte besparing t.o.v. kopen van net aan gemiddelde prijs")

                fig_solar_split = go.Figure()
                fig_solar_split.add_trace(go.Bar(
                    x=sch_sol["datetime"], y=sch_sol["charge_grid_kwh"],
                    name="Laden van net", marker_color="#E67E22"))
                fig_solar_split.add_trace(go.Bar(
                    x=sch_sol["datetime"], y=sch_sol["charge_solar_kwh"],
                    name="Laden van solar ☀️", marker_color="#F39C12"))
                fig_solar_split.update_layout(
                    barmode="stack",
                    title="Laadprofiel MILP+Solar: Grid vs Solar per kwartier",
                    xaxis_title="Tijd", yaxis_title="kWh",
                    legend=dict(orientation="h", y=-0.2))
                st.plotly_chart(fig_solar_split, use_container_width=True)

    if st.button("🔄 Reset scenario vergelijking"):
        st.session_state.scenarios         = {}
        st.session_state.scenario_errors   = {}
        st.session_state.scenarios_pending = False
        st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
st.markdown("---")
with st.expander("⚡ Elia Grid Intelligence — Imbalance + Solar PV Forecast", expanded=False):
    st.markdown(
        "**Elia** is de Belgische TSO (opendata.elia.be — geen API key nodig).\n\n"
        "**Imbalance tarieven** tonen hoeveel je verdient als je het net helpt via aggregator (Yuso):\n"
        "- **MIP**: prijs voor ontladen (upward regulation) — hoog = ontladen is winstgevend\n"
        "- **MDP**: prijs voor laden (downward regulation) — laag/negatief = laden wordt betaald\n"
        "- **NRV**: Net Regulation Volume (MW) — positief = grid tekort, negatief = grid overschot\n\n"
        "**Solar PV forecast** (ods087/ods032) toont verwachte zonne-energie productie — "
        "hoge solar morgen = verwacht lage/negatieve prijzen 10u–14u = optimaal laadmoment."
    )

    if not ELIA_AVAILABLE:
        st.warning("elia_client.py niet gevonden.")
    else:
        # ── Tabs: Imbalance | Solar ──
        tab_imb, tab_solar = st.tabs(["⚡ Imbalance Prijzen", "☀️ Solar PV Forecast"])

        # ── TAB 1: Imbalance ──────────────────────────────────────────────────
        with tab_imb:
            btn_col1, btn_col2 = st.columns(2)

            with btn_col1:
                if st.button("📡 Huidige snapshot", key="btn_elia_live"):
                    with st.spinner("Elia real-time ophalen…"):
                        try:
                            ec   = EliaClient()
                            snap = ec.get_latest_imbalance()
                            if snap.get("nrv_mw") is not None:
                                e1, e2, e3 = st.columns(3)
                                e1.metric("NRV (MW)",    f"{snap['nrv_mw']:.0f}")
                                e2.metric("MIP (€/MWh)", f"{snap['mip_eur_mwh']:.2f}" if snap.get("mip_eur_mwh") else "—")
                                e3.metric("MDP (€/MWh)", f"{snap['mdp_eur_mwh']:.2f}" if snap.get("mdp_eur_mwh") else "—")
                                st.info(snap.get("grid_state", ""))
                                st.caption(f"Tijdstip: {snap.get('datetime', '—')}")
                            else:
                                # Geen data (bv. 's nachts eerste kwartier)
                                st.warning(snap.get("status", "Geen data"))
                                if snap.get("tip"):
                                    st.info(snap["tip"])
                        except Exception as e:
                            st.error(f"Elia fout: {e}")

            with btn_col2:
                if st.button("📊 Imbalance profiel", key="btn_elia_today"):
                    with st.spinner("Elia imbalance profiel ophalen (met slimme fallback)…"):
                        try:
                            ec = EliaClient()
                            # Gebruik slimme fallback: real-time → historisch → gisteren
                            df_im, bron_label = ec.get_imbalance_best_available(today)

                            if bron_label.startswith("⚠️"):
                                st.warning(bron_label)
                            else:
                                st.success(f"✅ {bron_label}")

                            if not df_im.empty:
                                # Veilig kolommen ophalen — check bestaan voor plot
                                mip_col = "mip_eur_mwh" if "mip_eur_mwh" in df_im.columns else None
                                mdp_col = "mdp_eur_mwh" if "mdp_eur_mwh" in df_im.columns else None
                                nrv_col = "nrv_mw"      if "nrv_mw"      in df_im.columns else None
                                dt_col  = "datetime"    if "datetime"    in df_im.columns else None

                                if dt_col and (mip_col or nrv_col):
                                    fig_im = go.Figure()
                                    if mip_col:
                                        fig_im.add_trace(go.Scatter(
                                            x=df_im[dt_col], y=df_im[mip_col],
                                            mode="lines", name="MIP (ontladen €/MWh)",
                                            line=dict(color="red", width=2)))
                                    if mdp_col:
                                        fig_im.add_trace(go.Scatter(
                                            x=df_im[dt_col], y=df_im[mdp_col],
                                            mode="lines", name="MDP (laden €/MWh)",
                                            line=dict(color="green", width=2)))
                                    if nrv_col:
                                        fig_im.add_trace(go.Bar(
                                            x=df_im[dt_col], y=df_im[nrv_col],
                                            name="NRV (MW)",
                                            marker_color="rgba(100,100,200,0.3)",
                                            yaxis="y2"))
                                    fig_im.update_layout(
                                        title="Elia Imbalance Prijzen + NRV",
                                        xaxis_title="Tijd",
                                        yaxis=dict(title="€/MWh"),
                                        yaxis2=dict(title="NRV (MW)", overlaying="y", side="right"),
                                        legend=dict(x=0, y=1.1, orientation="h"),
                                    )
                                    st.plotly_chart(fig_im, use_container_width=True)

                                    # EMS metrics — veilig ophalen
                                    intel = ec.get_ems_intelligence(today)
                                    i1, i2, i3, i4 = st.columns(4)
                                    i1.metric("Kwartieren geanalyseerd", intel.get("quarters_analyzed", "—"))
                                    i2.metric("Grid short kwartieren",   intel.get("grid_short_qtrs", "—"))
                                    i3.metric("Gem. MIP", f"{intel.get('avg_mip_eur_mwh') or 0:.2f} €/MWh"
                                              if intel.get("avg_mip_eur_mwh") is not None else "—")
                                    i4.metric("Peak MIP", f"{intel.get('peak_mip_eur_mwh') or 0:.2f} €/MWh"
                                              if intel.get("peak_mip_eur_mwh") is not None else "—")

                                    # Ruwe data tabel (inklapbaar)
                                    with st.expander("📋 Ruwe imbalance data", expanded=False):
                                        show_cols = [c for c in ["datetime","nrv_mw","si_mw",
                                                                   "mip_eur_mwh","mdp_eur_mwh","alpha"]
                                                     if c in df_im.columns]
                                        st.dataframe(df_im[show_cols], use_container_width=True,
                                                     hide_index=True, height=300)
                                else:
                                    st.info(f"Kolommen in data: {list(df_im.columns)}")
                                    st.dataframe(df_im.head(10), use_container_width=True)
                            else:
                                st.info(
                                    "Geen imbalance data beschikbaar. Mogelijke oorzaken:\n"
                                    "- 's Nachts (00:00-00:15): eerste kwartier nog niet verstreken\n"
                                    "- Verbindingsprobleem met opendata.elia.be\n"
                                    "- Elia data vertraging (normaal: < 30 minuten)"
                                )
                        except Exception as e:
                            st.error(f"Elia fout: {e}")
                            st.caption("Tip: controleer of 'elia-py' geïnstalleerd is: `pip install elia-py`")

        # ── TAB 2: Solar PV Forecast ──────────────────────────────────────────
        with tab_solar:
            st.markdown(
                "**Zonnepanelen forecast voor België** via Elia (ods087 = actueel, ods032 = historisch).\n\n"
                "Strategisch belang voor EMS:\n"
                "- Hoge solar piek morgen → verwacht **negatieve/lage prijzen 10u–14u** → plan **laden**\n"
                "- Hoge solar vandaag + hoge prijzen 's avonds → **laden → ontladen** cyclus is winstgevend\n"
                "- Combineer solar forecast met ENTSO-E day-ahead voor nauwkeurige MILP-input"
            )

            sol_col1, sol_col2 = st.columns(2)

            with sol_col1:
                if st.button("☀️ Actuele solar forecast (ods087)", key="btn_solar_now"):
                    with st.spinner("Elia solar forecast ophalen…"):
                        try:
                            ec       = EliaClient()
                            df_sol   = ec.get_solar_forecast()
                            advice   = ec.get_solar_ems_advice()

                            if not df_sol.empty:
                                st.success(f"✅ {len(df_sol)} rijen geladen")
                                st.info(f"💡 **EMS Advies:** {advice.get('advice', '—')}")

                                # Zoek beschikbare forecast kolommen
                                plot_cols = [c for c in df_sol.columns
                                             if c not in ("datetime", "region")
                                             and df_sol[c].dtype in ("float64", "int64")]

                                if plot_cols and "datetime" in df_sol.columns:
                                    fig_sol = go.Figure()
                                    colors = ["#FFA500","#FFD700","#FF8C00","#FFC300"]
                                    for i, col in enumerate(plot_cols[:4]):
                                        fig_sol.add_trace(go.Scatter(
                                            x=df_sol["datetime"], y=df_sol[col],
                                            mode="lines", name=col,
                                            line=dict(color=colors[i % len(colors)], width=2)))
                                    fig_sol.update_layout(
                                        title="Solar PV Forecast België (MW)",
                                        xaxis_title="Tijd", yaxis_title="MW",
                                        legend=dict(x=0, y=1.1, orientation="h"))
                                    st.plotly_chart(fig_sol, use_container_width=True)

                                # Morgen specifiek
                                if advice.get("tomorrow_peak_mw"):
                                    tm1, tm2, tm3 = st.columns(3)
                                    tm1.metric("Piek morgen (MW)",  f"{advice['tomorrow_peak_mw']:.0f}")
                                    tm2.metric("Piek tijdstip",     advice.get("tomorrow_peak_time", "—")[-5:])
                                    tm3.metric("Totaal morgen (MWh)", f"{advice.get('tomorrow_total_mwh', 0):.0f}")

                                with st.expander("📋 Ruwe solar data + kolomnamen", expanded=False):
                                    st.caption(f"Kolommen: {list(df_sol.columns)}")
                                    st.dataframe(df_sol.head(20), use_container_width=True, hide_index=True)
                            else:
                                st.warning("Geen solar forecast data. Controleer verbinding met opendata.elia.be")
                        except Exception as e:
                            st.error(f"Solar fout: {e}")

            with sol_col2:
                st.markdown("**Historische solar productie (ods032)**")
                hist_days = st.slider("Dagen terug", 1, 30, 7, key="solar_hist_days")
                if st.button("📅 Haal historische solar op (ods032)", key="btn_solar_hist"):
                    with st.spinner("Historische solar ophalen…"):
                        try:
                            ec       = EliaClient()
                            hist_end = dt.date.today()
                            hist_start = hist_end - dt.timedelta(days=hist_days)
                            df_hist  = ec.get_historical_solar(hist_start, hist_end)

                            if not df_hist.empty:
                                st.success(f"✅ {len(df_hist)} rijen | {hist_days} dagen historische solar")

                                plot_cols = [c for c in df_hist.columns
                                             if c not in ("datetime","region")
                                             and df_hist[c].dtype in ("float64","int64")]

                                if plot_cols and "datetime" in df_hist.columns:
                                    fig_hist = go.Figure()
                                    for col in plot_cols[:3]:
                                        fig_hist.add_trace(go.Scatter(
                                            x=df_hist["datetime"], y=df_hist[col],
                                            mode="lines", name=col))
                                    fig_hist.update_layout(
                                        title=f"Historische Solar PV (ods032) — {hist_days} dagen",
                                        xaxis_title="Tijd", yaxis_title="MW")
                                    st.plotly_chart(fig_hist, use_container_width=True)

                                st.caption(
                                    "💡 Correleer deze data met de day-ahead prijzen: op dagen met hoge "
                                    "solar productie zie je typisch lage/negatieve prijzen 10u–14u."
                                )
                                with st.expander("📋 Data", expanded=False):
                                    st.dataframe(df_hist.head(50), use_container_width=True, hide_index=True)
                            else:
                                st.warning("Geen historische solar data.")
                        except Exception as e:
                            st.error(f"Solar historisch fout: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# Intraday Pricing (placeholder — EPEX SPOT data niet gratis beschikbaar)
# ─────────────────────────────────────────────────────────────────────────────
with st.expander("🔄 Intraday Pricing (EPEX SPOT)", expanded=False):
    st.markdown("""
    **Intraday markt** (EPEX SPOT / XBID) is continu open tot 60 min voor levering.

    | Aspect | Status |
    |---|---|
    | Data bron | EPEX SPOT (niet gratis publiek) |
    | Alternatief | Electricity Maps — biedt intraday API voor commerciële klanten |
    | Implementatie | `intraday_client.py` klaar zodra API-toegang beschikbaar is |
    | Relevantie | Hoogst voor real-time bijsturing (< 4u voor levering) |

    **Strategie zodra beschikbaar:**
    - Vergelijk day-ahead prijs met actuele intraday prijs
    - Als intraday >> day-ahead: versneld ontladen
    - Als intraday << day-ahead (of negatief): versneld laden
    - Intraday-prijzen reflecteren real-time onbalans en weerswijzigingen (PV-forecast updates)
    """)

# ─────────────────────────────────────────────────────────────────────────────
# Fluvius + NODES
# ─────────────────────────────────────────────────────────────────────────────
with st.expander("🌐 Fluvius Netcongestie & NODES Flex Market", expanded=False):
    fc1, fc2 = st.columns(2)
    with fc1:
        st.subheader("📍 Fluvius Congestie")
        if CONGESTION_AVAILABLE:
            gemeente = st.text_input("Gemeente", value="Gent", key="fluvius_gem")
            if st.button("Ophalen", key="btn_fluvius"):
                cc = CongestionClient()
                st.json(cc.get_congestion_summary(gemeente))
                df_c = cc.get_expected_congestion_hours(gemeente)
                if not df_c.empty: st.dataframe(df_c, use_container_width=True)
        else:
            st.warning("congestion_client.py niet gevonden.")
    with fc2:
        st.subheader("🔌 NODES Flex Market")
        if NODES_AVAILABLE:
            if st.button("Ophalen", key="btn_nodes"):
                nc = NodesClient()
                st.json(nc.get_market_summary())
                df_fx = nc.get_available_flex_requests()
                if not df_fx.empty: st.dataframe(df_fx, use_container_width=True)
                else: st.info("Geen open flex requests.")
        else:
            st.warning("nodes_client.py niet gevonden.")
