#!/usr/bin/env python3
"""
EMS MVP Dashboard - Streamlit App
Run with: streamlit run streamlit_dashboard.py
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
from datetime import date, timedelta, datetime

# MILP Optimizer
from milp_optimizer import optimize_battery_schedule

# Optional integrations
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
# Page config
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="EMS Belgium MVP Dashboard", layout="wide")
st.title("⚡ EMS Belgium - Battery & Grid Intelligence Dashboard")
st.markdown(
    "**MVP Prototype** | Belgian day-ahead prices | "
    "Smart arbitrage + free electricity charging | Grid balancing"
)

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar – Battery & Strategy Parameters
# ─────────────────────────────────────────────────────────────────────────────
st.sidebar.header("Battery & Strategy Parameters")
battery_kwh      = st.sidebar.slider("Usable Battery Capacity (kWh)", 5.0, 30.0, 10.0, 0.5)
max_power_kw     = st.sidebar.slider("Max Charge/Discharge Power (kW)", 2.0, 11.0, 5.0, 0.5)
charge_thresh    = st.sidebar.slider("Charge if price below (€/MWh)", 0, 80, 50)
discharge_thresh = st.sidebar.slider("Discharge if price above (€/MWh)", 100, 250, 160)
negative_boost   = st.sidebar.checkbox("Aggressive charge on negative prices", value=True)
min_soc_pct      = st.sidebar.slider(
    "Minimum SOC reserve (%)", 0, 30, 10, 1,
    help="Batterij nooit verder ontladen dan dit percentage.",
)
min_end_soc_pct  = st.sidebar.slider(
    "Minimum End-of-Horizon SOC (%)", 10, 50, 20, 5,
    help="Minimum SOC aan het einde van de optimalisatie horizon.",
)

max_energy_per_slot = max_power_kw * 0.25
st.sidebar.metric(
    "Max energy per 15-min slot", f"{max_energy_per_slot:.3f} kWh",
    help="Bij 5 kW max power mag je per 15 min maximaal 1,25 kWh laden of ontladen.",
)
st.sidebar.info(
    "Rule-based simulator + PuLP MILP optimization engine active. "
    f"{min_soc_pct}% SOC reserve is enforced in both."
)

st.sidebar.markdown("---")

# MILP trigger button
if "run_milp" not in st.session_state:
    st.session_state.run_milp = False

if st.sidebar.button("🚀 Run MILP Optimization", type="primary"):
    st.session_state.run_milp     = True
    st.session_state.milp_schedule = None   # reset previous results
    st.session_state.milp_summary  = None

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar – ENTSO-E Live Data
# ─────────────────────────────────────────────────────────────────────────────
st.sidebar.markdown("---")
st.sidebar.subheader("🔌 ENTSO-E Live Data")

if ENTSOE_AVAILABLE:
    with st.sidebar.expander("Live prijzen ophalen via ENTSO-E", expanded=False):
        st.caption("Vereist gratis API key van transparency.entsoe.eu")
        entsoe_key = st.text_input(
            "ENTSO-E API Key",
            type="password",
            value=st.session_state.get("entsoe_key") or st.secrets.get("entsoe_key", ""),
            help="My Account → API Key (of zet in .streamlit/secrets.toml)",
        )
        if entsoe_key:
            st.session_state.entsoe_key = entsoe_key

        if st.button("📥 Haal laatste 7 dagen op", type="secondary"):
            if not entsoe_key:
                st.error("Voer eerst je ENTSO-E API key in.")
            else:
                try:
                    with st.spinner("Ophalen van ENTSO-E…"):
                        client  = EntsoeClient(entsoe_key)
                        end     = date.today() + timedelta(days=1)
                        start   = end - timedelta(days=7)
                        new_df  = client.get_day_ahead_prices(start, end)
                        if not new_df.empty:
                            st.session_state.df_prices = new_df
                            st.success(f"✅ {len(new_df)} prijzen geladen!")
                            st.rerun()
                        else:
                            st.warning("Geen data gevonden.")
                except Exception as e:
                    st.error(f"Fout: {str(e)[:150]}")
else:
    st.sidebar.caption("entsoe_client.py niet gevonden.")

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar – Electricity Maps
# ─────────────────────────────────────────────────────────────────────────────
if ELECTRICITY_MAPS_AVAILABLE:
    with st.sidebar.expander("🌍 Electricity Maps - Day-Ahead Prices", expanded=False):
        st.caption("Fetch fresh Day-Ahead prices (v4 API)")
        em_key = st.text_input(
            "Electricity Maps API Key",
            type="password",
            value=st.session_state.get("em_key") or st.secrets.get("em_key", ""),
            help="Sandbox or Production key (of zet in .streamlit/secrets.toml)",
        )
        if em_key:
            st.session_state.em_key = em_key

        if st.button("📥 Fetch Prices", type="secondary"):
            if not em_key:
                st.error("Please enter your Electricity Maps API key.")
            else:
                try:
                    with st.spinner("Fetching from Electricity Maps…"):
                        em_client = ElectricityMapsClient(em_key)
                        end       = date.today() + timedelta(days=1)
                        start     = end - timedelta(days=7)
                        new_df    = em_client.get_day_ahead_prices("BE", start, end)
                        if not new_df.empty:
                            st.session_state.df_prices = new_df
                            st.success(f"✅ {len(new_df)} prijzen geladen!")
                            st.rerun()
                        else:
                            st.warning("Geen data gevonden.")
                except Exception as e:
                    st.error(f"Fout: {str(e)[:150]}")
else:
    st.sidebar.caption("electricity_maps_client.py niet gevonden.")

# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data
def load_data():
    p = Path("prices_belgium.parquet")
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()

if "df_prices" not in st.session_state:
    st.session_state.df_prices = load_data()

df = st.session_state.df_prices

if df.empty:
    st.warning(
        "Geen prijzen-data gevonden. Upload ENTSO-E XML, gebruik de live ENTSO-E fetch, "
        "of laad een prices_belgium.parquet."
    )
    uploaded_file = st.file_uploader(
        "Upload ENTSO-E XML of prices_belgium.parquet",
        type=["xml", "parquet"],
    )
    if uploaded_file is not None:
        if uploaded_file.name.endswith(".parquet"):
            df = pd.read_parquet(uploaded_file)
            st.session_state.df_prices = df
            st.success("Parquet succesvol geladen!")
            st.rerun()
        elif uploaded_file.name.endswith(".xml"):
            temp_xml = Path("temp_upload.xml")
            temp_xml.write_bytes(uploaded_file.getvalue())
            from price_parser import parse_entsoe_prices
            df = parse_entsoe_prices(temp_xml)
            st.session_state.df_prices = df
            st.success("XML geüpload en succesvol geparsed!")
            parquet_bytes = df.to_parquet(index=False)
            st.download_button(
                "📥 Download als prices_belgium.parquet",
                data=parquet_bytes,
                file_name="prices_belgium.parquet",
                mime="application/octet-stream",
            )
            st.rerun()

if st.session_state.df_prices.empty:
    st.info("Tip: Run lokaal `python price_parser.py` om de parquet te genereren.")
    st.stop()

df       = st.session_state.df_prices
min_date = df["datetime"].min().date()
max_date = df["datetime"].max().date()

# ─────────────────────────────────────────────────────────────────────────────
# Date range – session-state driven (single source of truth)
# ─────────────────────────────────────────────────────────────────────────────
_week_start  = max(min_date, max_date - timedelta(days=6))
_month_start = max(min_date, max_date.replace(day=1))

# Initialise session state on very first run → default = current week
if "date_start" not in st.session_state:
    st.session_state.date_start = _week_start
    st.session_state.date_end   = max_date
    # Pre-populate the date_input widget key so it shows the right value
    st.session_state["date_range_picker"] = (_week_start, max_date)

# ─────────────────────────────────────────────────────────────────────────────
# Quick-view buttons
# ─────────────────────────────────────────────────────────────────────────────
st.subheader("📅 Analyse Periode")

is_today = (st.session_state.date_start == max_date and
            st.session_state.date_end   == max_date)
is_week  = (st.session_state.date_start == _week_start and
            st.session_state.date_end   == max_date)
is_month = (st.session_state.date_start == _month_start and
            st.session_state.date_end   == max_date)

col1, col2, col3, _ = st.columns([1, 1, 1, 2])

with col1:
    if st.button(
        "📅 Vandaag",
        type="primary" if is_today else "secondary",
        use_container_width=True,
    ):
        st.session_state.date_start              = max_date
        st.session_state.date_end                = max_date
        st.session_state["date_range_picker"]    = (max_date, max_date)
        st.session_state.milp_schedule           = None   # clear stale MILP
        st.session_state.milp_summary            = None
        st.rerun()

with col2:
    if st.button(
        "📆 Deze Week",
        type="primary" if is_week else "secondary",
        use_container_width=True,
    ):
        st.session_state.date_start           = _week_start
        st.session_state.date_end             = max_date
        st.session_state["date_range_picker"] = (_week_start, max_date)
        st.session_state.milp_schedule        = None
        st.session_state.milp_summary         = None
        st.rerun()

with col3:
    if st.button(
        "🗓️ Deze Maand",
        type="primary" if is_month else "secondary",
        use_container_width=True,
    ):
        st.session_state.date_start           = _month_start
        st.session_state.date_end             = max_date
        st.session_state["date_range_picker"] = (_month_start, max_date)
        st.session_state.milp_schedule        = None
        st.session_state.milp_summary         = None
        st.rerun()

# Date-picker widget – the key drives its value from session state
date_range = st.date_input(
    "Of kies een eigen periode:",
    min_value=min_date,
    max_value=max_date,
    key="date_range_picker",   # widget reads/writes st.session_state["date_range_picker"]
)

# Keep date_start / date_end in sync with the picker (handles manual user edits)
if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
    if (date_range[0] != st.session_state.date_start or
            date_range[1] != st.session_state.date_end):
        st.session_state.date_start    = date_range[0]
        st.session_state.date_end      = date_range[1]
        st.session_state.milp_schedule = None   # stale MILP when period changes
        st.session_state.milp_summary  = None

# ─────────────────────────────────────────────────────────────────────────────
# Filter data for selected period
# ─────────────────────────────────────────────────────────────────────────────
mask   = (
    (df["datetime"].dt.date >= st.session_state.date_start) &
    (df["datetime"].dt.date <= st.session_state.date_end)
)
sim_df = df[mask].copy()

st.subheader(
    f"Price Overview  {st.session_state.date_start} → {st.session_state.date_end}  "
    f"({len(sim_df)} kwartier-slots)"
)

fig_price = px.line(
    sim_df, x="datetime", y="price_eur_mwh",
    title="Day-ahead Electricity Prices Belgium (€/MWh)",
    labels={"price_eur_mwh": "Price (€/MWh)", "datetime": "Time"},
)
fig_price.add_hline(y=charge_thresh,    line_dash="dash", line_color="green",
                    annotation_text="Charge threshold")
fig_price.add_hline(y=discharge_thresh, line_dash="dash", line_color="red",
                    annotation_text="Discharge threshold")
st.plotly_chart(fig_price, use_container_width=True)

neg_count = (sim_df["price_eur_mwh"] < 0).sum()
if neg_count > 0:
    st.success(
        f"🎉 {neg_count} kwartieren met **negatieve prijzen** in deze periode "
        "→ ideaal moment voor 'gratis of betaald' laden + grid support!"
    )

# ─────────────────────────────────────────────────────────────────────────────
# Live Fluvius + NODES
# ─────────────────────────────────────────────────────────────────────────────
with st.expander("🌐 Live Fluvius Netcongestie & NODES Flex Market", expanded=False):
    st.markdown("**Real-time grid intelligence** | Fluvius Capaciteitswijzer + NODES Flexibiliteitsmarkt")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("📍 Fluvius Congestie")
        if CONGESTION_AVAILABLE:
            congestion_client = CongestionClient()
            gemeente = st.text_input("Gemeente / Zone", value="Gent", key="fluvius_gemeente")
            if st.button("Haal Fluvius data op", key="btn_fluvius"):
                with st.spinner("Fluvius data ophalen…"):
                    st.json(congestion_client.get_congestion_summary(gemeente))
                    df_cong = congestion_client.get_expected_congestion_hours(gemeente)
                    if not df_cong.empty:
                        st.dataframe(df_cong, use_container_width=True)
        else:
            st.warning("congestion_client.py niet gevonden.")
    with c2:
        st.subheader("🔌 NODES Flex Market")
        if NODES_AVAILABLE:
            nodes_client = NodesClient()
            if st.button("Haal NODES marktstatus op", key="btn_nodes"):
                with st.spinner("NODES data ophalen…"):
                    st.json(nodes_client.get_market_summary())
                    df_flex = nodes_client.get_available_flex_requests()
                    if not df_flex.empty:
                        st.dataframe(df_flex, use_container_width=True)
                    else:
                        st.info("Geen open flex requests (of API key nodig).")
        else:
            st.warning("nodes_client.py niet gevonden.")

# ─────────────────────────────────────────────────────────────────────────────
# Rule-based simulation
# ─────────────────────────────────────────────────────────────────────────────
def quick_simulate(data, cap_kwh, pwr_kw, ch_thresh, dis_thresh, neg_boost, min_soc=0.10):
    soc       = 0.5
    cap_mwh   = cap_kwh / 1000
    max_e     = (pwr_kw * 0.25) / 1000
    results   = []
    cum_rev   = 0.0
    for _, row in data.iterrows():
        p      = row["price_eur_mwh"]
        action = "HOLD"
        e_mwh  = 0.0
        rev    = 0.0
        if p < 0 and neg_boost:
            e = min(max_e, (1 - soc) * cap_mwh / 0.96)
            if e > 0.0001:
                e_mwh   = e
                soc    += e_mwh * 0.96 / cap_mwh
                rev     = -e_mwh * p
                action  = "CHARGE (NEG)"
        elif p < ch_thresh:
            e = min(max_e, (1 - soc) * cap_mwh / 0.96)
            if e > 0.0001:
                e_mwh   = e
                soc    += e_mwh * 0.96 / cap_mwh
                rev     = -e_mwh * p
                action  = "CHARGE"
        elif p > dis_thresh:
            available = max(0.0, (soc - min_soc) * cap_mwh * 0.96)
            discharge_possible = min(max_e, available)
            if discharge_possible > 0.0001:
                e_mwh   = discharge_possible
                soc    -= e_mwh / (cap_mwh * 0.96)
                rev     = e_mwh * p
                action  = "DISCHARGE"
        cum_rev += rev
        results.append({
            "datetime":   row["datetime"],
            "price":      p,
            "action":     action,
            "energy_kwh": e_mwh * 1000,
            "revenue":    rev,
            "soc":        soc * 100,
            "cum_rev":    cum_rev,
        })
    return pd.DataFrame(results)

sim = quick_simulate(
    sim_df, battery_kwh, max_power_kw,
    charge_thresh, discharge_thresh,
    negative_boost, min_soc_pct / 100,
)

# ─────────────────────────────────────────────────────────────────────────────
# KPI metrics
# ─────────────────────────────────────────────────────────────────────────────
st.subheader("🔋 Battery Simulation Results")

milp_ready = st.session_state.get("milp_schedule") is not None

m1, m2, m3 = st.columns(3)
m1.metric(
    "Net Revenue (Rule-based)",
    f"{sim['cum_rev'].iloc[-1]:.2f} €",
    delta=(
        f"MILP: {st.session_state.milp_summary['total_net_revenue_eur']:.2f} €"
        if milp_ready else None
    ),
)
m2.metric("Energy Charged", f"{sim['energy_kwh'].sum():.1f} kWh")
m3.metric("Avg SOC", f"{sim['soc'].mean():.1f} %")

# ─────────────────────────────────────────────────────────────────────────────
# Actions chart
# ─────────────────────────────────────────────────────────────────────────────
fig_actions = go.Figure()
fig_actions.add_trace(go.Scatter(
    x=sim["datetime"], y=sim["price"], mode="lines",
    name="Price", line=dict(color="gray"),
))
charge_pts = sim[sim["action"].str.contains("CHARGE")]
dis_pts    = sim[sim["action"] == "DISCHARGE"]
fig_actions.add_trace(go.Scatter(
    x=charge_pts["datetime"], y=charge_pts["price"], mode="markers",
    name="CHARGE", marker=dict(color="green", size=8),
))
fig_actions.add_trace(go.Scatter(
    x=dis_pts["datetime"], y=dis_pts["price"], mode="markers",
    name="DISCHARGE", marker=dict(color="red", size=8),
))
fig_actions.update_layout(title="Price + Rule-based EMS Actions",
                          xaxis_title="Time", yaxis_title="€/MWh")
st.plotly_chart(fig_actions, use_container_width=True)

# ─────────────────────────────────────────────────────────────────────────────
# SOC chart  –  shows MILP overlay automatically when available
# ─────────────────────────────────────────────────────────────────────────────
fig_soc = go.Figure()
fig_soc.add_trace(go.Scatter(
    x=sim["datetime"], y=sim["soc"],
    mode="lines", name="Rule-based SOC",
    line=dict(color="royalblue", width=2),
))
if milp_ready:
    milp_df = st.session_state.milp_schedule
    fig_soc.add_trace(go.Scatter(
        x=milp_df["datetime"], y=milp_df["soc_pct"],
        mode="lines", name="MILP Optimal SOC",
        line=dict(color="#00AA00", width=2.5, dash="dot"),
    ))
fig_soc.add_hline(
    y=min_soc_pct, line_dash="dash", line_color="orange",
    annotation_text=f"Min {min_soc_pct}% SOC Reserve",
    annotation_position="top right",
)
fig_soc.update_yaxes(range=[0, 100], title="SOC (%)")
title_soc = "Battery State of Charge (%)"
if milp_ready:
    title_soc += " — Rule-based vs MILP"
fig_soc.update_layout(title=title_soc, xaxis_title="Time")
st.plotly_chart(fig_soc, use_container_width=True)

# ─────────────────────────────────────────────────────────────────────────────
# Revenue chart  –  shows MILP overlay automatically when available
# ─────────────────────────────────────────────────────────────────────────────
fig_rev = go.Figure()
fig_rev.add_trace(go.Scatter(
    x=sim["datetime"], y=sim["cum_rev"],
    mode="lines", name="Rule-based",
    fill="tozeroy", line=dict(color="royalblue"),
))
if milp_ready:
    milp_df = st.session_state.milp_schedule
    milp_df = milp_df.copy()
    milp_df["cum_revenue_milp"] = milp_df["net_revenue_eur"].cumsum()
    fig_rev.add_trace(go.Scatter(
        x=milp_df["datetime"], y=milp_df["cum_revenue_milp"],
        mode="lines", name="MILP Optimal",
        line=dict(color="#00AA00", width=2.5, dash="dot"),
    ))
title_rev = "Cumulative Revenue (€) from Smart Charging/Discharging"
if milp_ready:
    title_rev += " — Rule-based vs MILP"
fig_rev.update_layout(title=title_rev, xaxis_title="Time", yaxis_title="€")
st.plotly_chart(fig_rev, use_container_width=True)

# ─────────────────────────────────────────────────────────────────────────────
# MILP Optimization
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("---")

if st.session_state.get("run_milp", False):
    with st.expander("🚀 MILP Optimization Results", expanded=True):
        st.markdown(
            "**MILP** zoekt de optimale planning met perfecte foresight over de geselecteerde "
            "periode. Harde SOC-reserve en end-SOC constraint zijn altijd actief."
        )

        # ── Run button ────────────────────────────────────────────────────────
        n_slots = len(sim_df)
        n_days  = round(n_slots / 96, 1)
        if st.button(
            f"▶️  Optimaliseer {n_slots} slots ({n_days} dagen) via MILP",
            key="run_milp_btn",
            type="primary",
        ):
            progress_bar = st.progress(0, text="MILP solver initialiseren…")
            status_box   = st.empty()

            try:
                status_box.info(
                    f"⚙️  Solving {n_slots} time slots over {n_days} days…  "
                    "Dit kan enkele seconden duren."
                )
                progress_bar.progress(10, text="Variabelen en constraints aanmaken…")

                milp_schedule, milp_summary = optimize_battery_schedule(
                    sim_df,
                    battery_kwh=battery_kwh,
                    max_power_kw=max_power_kw,
                    min_soc=min_soc_pct / 100,
                    min_end_soc=min_end_soc_pct / 100,
                    initial_soc=0.50,
                    time_horizon_hours=None,   # ← use ALL selected slots
                )

                progress_bar.progress(90, text="Resultaten verwerken…")

                # Store for overlay charts above
                st.session_state.milp_schedule = milp_schedule
                st.session_state.milp_summary  = milp_summary

                progress_bar.progress(100, text="✅  Klaar!")
                status_box.empty()

                # ── Solver stats banner ───────────────────────────────────────
                s1, s2, s3, s4 = st.columns(4)
                s1.metric(
                    "Solver status",
                    milp_summary["status"],
                    help="Optimal = globaal optimum gevonden",
                )
                s2.metric(
                    "Solve time",
                    f"{milp_summary['solve_time_sec']} s",
                    help="Wallclock tijd voor CBC solver",
                )
                s3.metric(
                    "Simplex iteraties",
                    f"{milp_summary['solver_iterations']:,}",
                    help="Aantal LP simplex iteraties door CBC",
                )
                s4.metric(
                    "Slots geoptimaliseerd",
                    f"{milp_summary['num_slots']}",
                    help=f"{n_days} dagen × 96 kwartieren",
                )

                # ── Financial breakdown ───────────────────────────────────────
                st.markdown("#### 💰 MILP Financieel Overzicht")

                pos_charge   = milp_schedule[
                    (milp_schedule["charge_kwh"] > 0) & (milp_schedule["price_eur_mwh"] > 0)
                ]
                neg_charge   = milp_schedule[
                    (milp_schedule["charge_kwh"] > 0) & (milp_schedule["price_eur_mwh"] <= 0)
                ]
                discharge_df = milp_schedule[milp_schedule["discharge_kwh"] > 0]

                cost_pos       = abs(pos_charge["net_revenue_eur"].sum())
                income_neg     = abs(neg_charge["net_revenue_eur"].sum())
                income_dis     = discharge_df["net_revenue_eur"].sum()
                rule_based_rev = sim["cum_rev"].iloc[-1]

                f1, f2, f3, f4 = st.columns(4)
                f1.metric(
                    "Net Revenue (MILP)",
                    f"{milp_summary['total_net_revenue_eur']:.2f} €",
                    delta=f"{milp_summary['total_net_revenue_eur'] - rule_based_rev:+.2f} € vs Rule-based",
                )
                f2.metric(
                    "Kosten (prijs > 0)",
                    f"-{cost_pos:.2f} €",
                    help="Betaald voor laden bij positieve prijs",
                    delta_color="inverse",
                )
                f3.metric(
                    "Inkomsten (prijs ≤ 0)",
                    f"+{income_neg:.2f} €",
                    help="Ontvangen voor laden bij negatieve prijs",
                )
                f4.metric(
                    "Ontlaad-inkomsten",
                    f"+{income_dis:.2f} €",
                    help="Verdiend via ontladen bij hoge prijs",
                )

                # ── Comparison table ──────────────────────────────────────────
                st.markdown("#### 📊 Rule-based vs MILP vergelijking")
                comp = pd.DataFrame({
                    "Metric": [
                        "Net Revenue (€)", "Geladen (kWh)", "Ontladen (kWh)", "Eind SOC (%)"
                    ],
                    "Rule-based": [
                        round(rule_based_rev, 2),
                        round(sim["energy_kwh"].sum(), 1),
                        round(sim[sim["action"] == "DISCHARGE"]["energy_kwh"].sum(), 1),
                        round(sim["soc"].iloc[-1], 1),
                    ],
                    "MILP": [
                        milp_summary["total_net_revenue_eur"],
                        milp_summary["total_charged_kwh"],
                        milp_summary["total_discharged_kwh"],
                        milp_summary["final_soc_pct"],
                    ],
                })
                st.dataframe(comp, use_container_width=True, hide_index=True)

                # ── MILP actions chart ────────────────────────────────────────
                st.markdown("#### 📋 MILP acties (actieve kwartieren)")
                action_mask = (
                    (milp_schedule["charge_kwh"] > 0.01) |
                    (milp_schedule["discharge_kwh"] > 0.01)
                )
                detail_df = milp_schedule[action_mask][
                    ["datetime", "price_eur_mwh", "charge_kwh", "discharge_kwh",
                     "net_revenue_eur", "soc_pct"]
                ].copy()
                detail_df["Type"] = detail_df["net_revenue_eur"].apply(
                    lambda x: "🟢 Inkomsten" if x > 0 else ("🔴 Kosten" if x < 0 else "⚪ Nul")
                )
                detail_df = detail_df.rename(columns={
                    "datetime":        "Tijd",
                    "price_eur_mwh":   "Prijs (€/MWh)",
                    "charge_kwh":      "Laden (kWh)",
                    "discharge_kwh":   "Ontladen (kWh)",
                    "net_revenue_eur": "Slot Revenue (€)",
                    "soc_pct":         "SOC (%)",
                })
                st.dataframe(detail_df, use_container_width=True, hide_index=True, height=400)

                fig_milp = go.Figure()
                fig_milp.add_trace(go.Scatter(
                    x=milp_schedule["datetime"], y=milp_schedule["price_eur_mwh"],
                    mode="lines", name="Prijs", line=dict(color="gray"),
                ))
                c_mask = milp_schedule["charge_kwh"] > 0.01
                d_mask = milp_schedule["discharge_kwh"] > 0.01
                fig_milp.add_trace(go.Scatter(
                    x=milp_schedule[c_mask]["datetime"],
                    y=milp_schedule[c_mask]["price_eur_mwh"],
                    mode="markers", name="MILP LADEN",
                    marker=dict(color="green", size=9, symbol="triangle-up"),
                ))
                fig_milp.add_trace(go.Scatter(
                    x=milp_schedule[d_mask]["datetime"],
                    y=milp_schedule[d_mask]["price_eur_mwh"],
                    mode="markers", name="MILP ONTLADEN",
                    marker=dict(color="red", size=9, symbol="triangle-down"),
                ))
                fig_milp.update_layout(
                    title="MILP Optimale Acties", xaxis_title="Tijd", yaxis_title="€/MWh"
                )
                st.plotly_chart(fig_milp, use_container_width=True)

                # ── Solver log (collapsed) ────────────────────────────────────
                with st.expander("🔍 CBC Solver log (technisch)", expanded=False):
                    st.code(milp_summary.get("solver_log", "Geen log beschikbaar."), language="text")

                st.info(
                    "⬆️ Scroll omhoog — de **SOC** en **Cumulative Revenue** grafieken "
                    "tonen nu automatisch de MILP-curve in groen naast de rule-based curve."
                )

            except Exception as e:
                progress_bar.progress(0)
                status_box.error(f"MILP mislukt: {e}")
                st.exception(e)

        # ── Reset ─────────────────────────────────────────────────────────────
        if st.button("🔄 Reset MILP"):
            st.session_state.run_milp     = False
            st.session_state.milp_schedule = None
            st.session_state.milp_summary  = None
            st.rerun()
