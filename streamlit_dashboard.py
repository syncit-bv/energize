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

from milp_optimizer import optimize_battery_schedule

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
min_soc_pct      = st.sidebar.slider("Minimum SOC reserve (%)", 0, 30, 10, 1,
                    help="Batterij nooit verder ontladen dan dit percentage.")
min_end_soc_pct  = st.sidebar.slider("Minimum End-of-Horizon SOC (%)", 10, 50, 20, 5,
                    help="Minimum SOC aan het einde van de optimalisatie horizon.")

st.sidebar.markdown("---")
st.sidebar.subheader("🔋 MILP Startpositie")

# Show previous run's final SOC as suggestion when available
prev_final_soc = None
if st.session_state.get("milp_summary"):
    prev_final_soc = st.session_state.milp_summary.get("final_soc_pct")

initial_soc_help = (
    "Start-SOC voor de MILP optimalisatie. "
    "In productie: lees de werkelijke batterij-SOC uit het BMS. "
    "Bij meerdere aaneengesloten periodes: gebruik het eindpunt van de vorige run."
)
if prev_final_soc is not None:
    st.sidebar.caption(f"💡 Vorige run eindigde op {prev_final_soc:.1f}% SOC")
    use_prev = st.sidebar.checkbox(
        f"Gebruik {prev_final_soc:.1f}% als startpunt (vorige run)", value=True
    )
    default_initial = prev_final_soc / 100 if use_prev else 0.50
else:
    default_initial = 0.50

initial_soc_pct = st.sidebar.slider(
    "Initiële SOC (%)", 10, 100,
    int(default_initial * 100), 5,
    help=initial_soc_help,
)

max_energy_per_slot = max_power_kw * 0.25
st.sidebar.metric("Max energie per slot (15 min)", f"{max_energy_per_slot:.3f} kWh")
st.sidebar.info(f"Rule-based + PuLP MILP actief. {min_soc_pct}% SOC reserve altijd gehandhaafd.")

st.sidebar.markdown("---")

# MILP trigger – single click, auto-runs (no second button needed)
if "milp_pending" not in st.session_state:
    st.session_state.milp_pending = False

if st.sidebar.button("🚀 Run MILP Optimization", type="primary"):
    st.session_state.milp_pending  = True
    st.session_state.milp_schedule = None   # clear previous results
    st.session_state.milp_summary  = None
    st.session_state.milp_initial_soc = initial_soc_pct / 100

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar – ENTSO-E Live Data
# ─────────────────────────────────────────────────────────────────────────────
st.sidebar.markdown("---")
st.sidebar.subheader("🔌 Live Prijsdata")

if ENTSOE_AVAILABLE:
    with st.sidebar.expander("ENTSO-E (gratis, onbeperkt historisch)", expanded=False):
        st.caption("Registreer gratis op transparency.entsoe.eu")
        entsoe_key = st.text_input(
            "ENTSO-E API Key", type="password",
            value=st.session_state.get("entsoe_key") or st.secrets.get("entsoe_key", ""),
        )
        if entsoe_key:
            st.session_state.entsoe_key = entsoe_key
        days_back = st.slider("Dagen terug", 7, 90, 30, 7)
        if st.button("📥 Ophalen via ENTSO-E", type="secondary"):
            if not entsoe_key:
                st.error("Vul eerst je ENTSO-E API key in.")
            else:
                try:
                    with st.spinner("Ophalen van ENTSO-E…"):
                        client = EntsoeClient(entsoe_key)
                        end    = date.today() + timedelta(days=1)
                        start  = end - timedelta(days=days_back)
                        new_df = client.get_day_ahead_prices(start, end)
                        if not new_df.empty:
                            st.session_state.df_prices = new_df
                            st.success(f"✅ {len(new_df)} prijzen geladen ({days_back} dagen)!")
                            st.rerun()
                        else:
                            st.warning("Geen data gevonden.")
                except Exception as e:
                    st.error(f"Fout: {str(e)[:150]}")
else:
    st.sidebar.caption("entsoe_client.py niet gevonden.")

if ELECTRICITY_MAPS_AVAILABLE:
    with st.sidebar.expander("🌍 Electricity Maps (sandbox = 7 dagen)", expanded=False):
        st.caption("Sandbox API = beperkt tot recente 7 dagen. Gebruik ENTSO-E voor meer historische data.")
        em_key = st.text_input(
            "Electricity Maps API Key", type="password",
            value=st.session_state.get("em_key") or st.secrets.get("em_key", ""),
        )
        if em_key:
            st.session_state.em_key = em_key
        if st.button("📥 Fetch via Electricity Maps", type="secondary"):
            if not em_key:
                st.error("Vul eerst je Electricity Maps API key in.")
            else:
                try:
                    with st.spinner("Fetching…"):
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
    st.warning("Geen prijsdata. Upload XML/parquet of gebruik een live API hierboven.")
    uploaded_file = st.file_uploader("Upload ENTSO-E XML of prices_belgium.parquet",
                                     type=["xml", "parquet"])
    if uploaded_file is not None:
        if uploaded_file.name.endswith(".parquet"):
            df = pd.read_parquet(uploaded_file)
            st.session_state.df_prices = df
            st.rerun()
        elif uploaded_file.name.endswith(".xml"):
            Path("temp_upload.xml").write_bytes(uploaded_file.getvalue())
            from price_parser import parse_entsoe_prices
            df = parse_entsoe_prices("temp_upload.xml")
            st.session_state.df_prices = df
            parquet_bytes = df.to_parquet(index=False)
            st.download_button("📥 Download als prices_belgium.parquet",
                               data=parquet_bytes, file_name="prices_belgium.parquet")
            st.rerun()

if st.session_state.df_prices.empty:
    st.info("Tip: `python price_parser.py` of gebruik de ENTSO-E fetch in de sidebar.")
    st.stop()

df       = st.session_state.df_prices
min_date = df["datetime"].min().date()
max_date = df["datetime"].max().date()

# ─────────────────────────────────────────────────────────────────────────────
# Date range – session-state driven
# ─────────────────────────────────────────────────────────────────────────────
_week_start  = max(min_date, max_date - timedelta(days=6))
_month_start = max(min_date, max_date.replace(day=1))

if "date_start" not in st.session_state:
    st.session_state.date_start = _week_start
    st.session_state.date_end   = max_date
    st.session_state["date_range_picker"] = (_week_start, max_date)

is_today = (st.session_state.date_start == max_date and
            st.session_state.date_end   == max_date)
is_week  = (st.session_state.date_start == _week_start and
            st.session_state.date_end   == max_date)
is_month = (st.session_state.date_start == _month_start and
            st.session_state.date_end   == max_date)

st.subheader("📅 Analyse Periode")
col1, col2, col3, _ = st.columns([1, 1, 1, 2])

def _set_period(start, end):
    st.session_state.date_start           = start
    st.session_state.date_end             = end
    st.session_state["date_range_picker"] = (start, end)
    st.session_state.milp_schedule        = None  # stale when period changes
    st.session_state.milp_summary         = None
    st.session_state.milp_pending         = False

with col1:
    if st.button("📅 Vandaag", type="primary" if is_today else "secondary",
                 use_container_width=True):
        _set_period(max_date, max_date); st.rerun()
with col2:
    if st.button("📆 Deze Week", type="primary" if is_week else "secondary",
                 use_container_width=True):
        _set_period(_week_start, max_date); st.rerun()
with col3:
    if st.button("🗓️ Deze Maand", type="primary" if is_month else "secondary",
                 use_container_width=True):
        _set_period(_month_start, max_date); st.rerun()

date_range = st.date_input("Of kies een eigen periode:",
                            min_value=min_date, max_value=max_date,
                            key="date_range_picker")

if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
    if (date_range[0] != st.session_state.date_start or
            date_range[1] != st.session_state.date_end):
        st.session_state.date_start    = date_range[0]
        st.session_state.date_end      = date_range[1]
        st.session_state.milp_schedule = None
        st.session_state.milp_summary  = None
        st.session_state.milp_pending  = False

mask   = ((df["datetime"].dt.date >= st.session_state.date_start) &
          (df["datetime"].dt.date <= st.session_state.date_end))
sim_df = df[mask].copy()

# ─────────────────────────────────────────────────────────────────────────────
# *** MILP AUTO-RUN ***
# Runs immediately after sidebar button → stores results → st.rerun()
# On the next pass the charts below pick up milp_schedule from session_state.
# ─────────────────────────────────────────────────────────────────────────────
if st.session_state.get("milp_pending"):
    n_slots = len(sim_df)
    n_days  = round(n_slots / 96, 1)
    with st.status(
        f"⚙️  MILP optimalisatie voor {n_slots} slots ({n_days} dagen)…",
        expanded=True,
    ) as solve_status:
        st.write("Variabelen en constraints aanmaken…")
        try:
            milp_schedule, milp_summary = optimize_battery_schedule(
                sim_df,
                battery_kwh=battery_kwh,
                max_power_kw=max_power_kw,
                min_soc=min_soc_pct / 100,
                min_end_soc=min_end_soc_pct / 100,
                initial_soc=st.session_state.get("milp_initial_soc", 0.50),
                time_horizon_hours=None,
            )
            st.write(
                f"✅  Opgelost in **{milp_summary['solve_time_sec']} s** | "
                f"**{milp_summary['solver_iterations']:,}** iteraties | "
                f"Status: **{milp_summary['status']}**"
            )
            st.session_state.milp_schedule = milp_schedule
            st.session_state.milp_summary  = milp_summary
            st.session_state.milp_pending  = False
            solve_status.update(label="✅  MILP klaar!", state="complete")
        except Exception as e:
            solve_status.update(label=f"❌  MILP mislukt: {e}", state="error")
            st.session_state.milp_pending = False
            st.exception(e)

    # Rerun so all charts above/below now pick up milp_schedule from session_state
    st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# Price chart
# ─────────────────────────────────────────────────────────────────────────────
st.subheader(
    f"Price Overview  {st.session_state.date_start} → {st.session_state.date_end}  "
    f"({len(sim_df)} slots)"
)
fig_price = px.line(sim_df, x="datetime", y="price_eur_mwh",
                    title="Day-ahead Electricity Prices Belgium (€/MWh)",
                    labels={"price_eur_mwh": "Price (€/MWh)", "datetime": "Time"})
fig_price.add_hline(y=charge_thresh,    line_dash="dash", line_color="green",
                    annotation_text="Charge threshold")
fig_price.add_hline(y=discharge_thresh, line_dash="dash", line_color="red",
                    annotation_text="Discharge threshold")
st.plotly_chart(fig_price, use_container_width=True)

neg_count = (sim_df["price_eur_mwh"] < 0).sum()
if neg_count > 0:
    st.success(f"🎉 {neg_count} kwartieren met negatieve prijzen → gratis / betaald laden + grid support!")

# ─────────────────────────────────────────────────────────────────────────────
# Fluvius + NODES
# ─────────────────────────────────────────────────────────────────────────────
with st.expander("🌐 Live Fluvius Netcongestie & NODES Flex Market", expanded=False):
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("📍 Fluvius Congestie")
        if CONGESTION_AVAILABLE:
            gemeente = st.text_input("Gemeente / Zone", value="Gent", key="fluvius_gemeente")
            if st.button("Haal Fluvius data op", key="btn_fluvius"):
                cc = CongestionClient()
                st.json(cc.get_congestion_summary(gemeente))
                df_cong = cc.get_expected_congestion_hours(gemeente)
                if not df_cong.empty:
                    st.dataframe(df_cong, use_container_width=True)
        else:
            st.warning("congestion_client.py niet gevonden.")
    with c2:
        st.subheader("🔌 NODES Flex Market")
        if NODES_AVAILABLE:
            if st.button("Haal NODES marktstatus op", key="btn_nodes"):
                nc = NodesClient()
                st.json(nc.get_market_summary())
                df_flex = nc.get_available_flex_requests()
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
    soc = 0.5; cap_mwh = cap_kwh / 1000; max_e = (pwr_kw * 0.25) / 1000
    results = []; cum_rev = 0.0
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
            available = max(0.0, (soc - min_soc) * cap_mwh * 0.96)
            dp = min(max_e, available)
            if dp > 0.0001:
                e_mwh = dp; soc -= e_mwh / (cap_mwh * 0.96)
                rev = e_mwh * p; action = "DISCHARGE"
        cum_rev += rev
        results.append({"datetime": row["datetime"], "price": p, "action": action,
                         "energy_kwh": e_mwh * 1000, "revenue": rev,
                         "soc": soc * 100, "cum_rev": cum_rev})
    return pd.DataFrame(results)

sim = quick_simulate(sim_df, battery_kwh, max_power_kw, charge_thresh,
                     discharge_thresh, negative_boost, min_soc_pct / 100)

milp_df   = st.session_state.get("milp_schedule")
milp_summ = st.session_state.get("milp_summary")
milp_ready = milp_df is not None

# ─────────────────────────────────────────────────────────────────────────────
# KPI metrics
# ─────────────────────────────────────────────────────────────────────────────
st.subheader("🔋 Battery Simulation Results")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Net Revenue (Rule-based)", f"{sim['cum_rev'].iloc[-1]:.2f} €")
m2.metric("Energy Charged", f"{sim['energy_kwh'].sum():.1f} kWh")
m3.metric("Avg SOC", f"{sim['soc'].mean():.1f} %")
if milp_ready:
    m4.metric(
        "Net Revenue (MILP)",
        f"{milp_summ['total_net_revenue_eur']:.2f} €",
        delta=f"{milp_summ['total_net_revenue_eur'] - sim['cum_rev'].iloc[-1]:+.2f} € vs Rule-based",
    )
else:
    m4.metric("MILP Revenue", "— (niet uitgevoerd)", help="Druk op '🚀 Run MILP' in de sidebar")

# ─────────────────────────────────────────────────────────────────────────────
# Actions chart
# ─────────────────────────────────────────────────────────────────────────────
fig_actions = go.Figure()
fig_actions.add_trace(go.Scatter(x=sim["datetime"], y=sim["price"],
    mode="lines", name="Prijs", line=dict(color="gray")))
fig_actions.add_trace(go.Scatter(
    x=sim[sim["action"].str.contains("CHARGE")]["datetime"],
    y=sim[sim["action"].str.contains("CHARGE")]["price"],
    mode="markers", name="LADEN", marker=dict(color="green", size=8)))
fig_actions.add_trace(go.Scatter(
    x=sim[sim["action"] == "DISCHARGE"]["datetime"],
    y=sim[sim["action"] == "DISCHARGE"]["price"],
    mode="markers", name="ONTLADEN", marker=dict(color="red", size=8)))
fig_actions.update_layout(title="Prijs + Rule-based EMS Acties",
                           xaxis_title="Tijd", yaxis_title="€/MWh")
st.plotly_chart(fig_actions, use_container_width=True)

# ─────────────────────────────────────────────────────────────────────────────
# SOC chart  (rule-based, always shown)
# ─────────────────────────────────────────────────────────────────────────────
fig_soc_rb = go.Figure()
fig_soc_rb.add_trace(go.Scatter(x=sim["datetime"], y=sim["soc"],
    mode="lines", name="Rule-based SOC", line=dict(color="royalblue", width=2)))
fig_soc_rb.add_hline(y=min_soc_pct, line_dash="dash", line_color="orange",
    annotation_text=f"Min {min_soc_pct}% reserve", annotation_position="top right")
fig_soc_rb.update_yaxes(range=[0, 100], title="SOC (%)")
fig_soc_rb.update_layout(title="Battery State of Charge (%) — Rule-based", xaxis_title="Tijd")
st.plotly_chart(fig_soc_rb, use_container_width=True)

# ─────────────────────────────────────────────────────────────────────────────
# Revenue chart  (rule-based, always shown)
# ─────────────────────────────────────────────────────────────────────────────
fig_rev_rb = go.Figure()
fig_rev_rb.add_trace(go.Scatter(x=sim["datetime"], y=sim["cum_rev"],
    mode="lines", name="Rule-based", fill="tozeroy", line=dict(color="royalblue")))
fig_rev_rb.update_layout(title="Cumulatieve Revenue (€) — Rule-based",
                          xaxis_title="Tijd", yaxis_title="€")
st.plotly_chart(fig_rev_rb, use_container_width=True)

# ─────────────────────────────────────────────────────────────────────────────
# MILP charts  (shown directly below when MILP has been run)
# ─────────────────────────────────────────────────────────────────────────────
if milp_ready:
    st.markdown("---")
    st.subheader("🟢 MILP Optimale Planning — Resultaten")

    # Solver stats
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Solver status",     milp_summ["status"])
    s2.metric("Solve time",        f"{milp_summ['solve_time_sec']} s")
    s3.metric("Simplex iteraties", f"{milp_summ['solver_iterations']:,}")
    s4.metric("Slots",             f"{milp_summ['num_slots']}")

    # SOC – MILP
    milp_df_copy = milp_df.copy()
    fig_soc_milp = go.Figure()
    fig_soc_milp.add_trace(go.Scatter(x=sim["datetime"], y=sim["soc"],
        mode="lines", name="Rule-based", line=dict(color="royalblue", width=1.5, dash="dot")))
    fig_soc_milp.add_trace(go.Scatter(x=milp_df_copy["datetime"], y=milp_df_copy["soc_pct"],
        mode="lines", name="MILP Optimaal", line=dict(color="#00AA00", width=2.5)))
    fig_soc_milp.add_hline(y=min_soc_pct, line_dash="dash", line_color="orange",
        annotation_text=f"Min {min_soc_pct}% reserve", annotation_position="top right")
    fig_soc_milp.update_yaxes(range=[0, 100], title="SOC (%)")
    fig_soc_milp.update_layout(title="Battery State of Charge (%) — Rule-based vs MILP",
                                xaxis_title="Tijd")
    st.plotly_chart(fig_soc_milp, use_container_width=True)

    # Revenue – MILP
    milp_df_copy["cum_rev_milp"] = milp_df_copy["net_revenue_eur"].cumsum()
    fig_rev_milp = go.Figure()
    fig_rev_milp.add_trace(go.Scatter(x=sim["datetime"], y=sim["cum_rev"],
        mode="lines", name="Rule-based", line=dict(color="royalblue", width=1.5, dash="dot")))
    fig_rev_milp.add_trace(go.Scatter(x=milp_df_copy["datetime"], y=milp_df_copy["cum_rev_milp"],
        mode="lines", name="MILP Optimaal", fill="tozeroy",
        line=dict(color="#00AA00", width=2.5)))
    fig_rev_milp.update_layout(
        title="Cumulatieve Revenue (€) — Rule-based vs MILP",
        xaxis_title="Tijd", yaxis_title="€")
    st.plotly_chart(fig_rev_milp, use_container_width=True)

    # Financial breakdown
    st.markdown("#### 💰 Financieel Overzicht MILP")
    pos_ch  = milp_df[(milp_df["charge_kwh"] > 0) & (milp_df["price_eur_mwh"] > 0)]
    neg_ch  = milp_df[(milp_df["charge_kwh"] > 0) & (milp_df["price_eur_mwh"] <= 0)]
    dis_df  = milp_df[milp_df["discharge_kwh"] > 0]
    cost_p  = abs(pos_ch["net_revenue_eur"].sum())
    inc_neg = abs(neg_ch["net_revenue_eur"].sum())
    inc_dis = dis_df["net_revenue_eur"].sum()

    f1, f2, f3, f4 = st.columns(4)
    f1.metric("Net Revenue (MILP)", f"{milp_summ['total_net_revenue_eur']:.2f} €",
              delta=f"{milp_summ['total_net_revenue_eur'] - sim['cum_rev'].iloc[-1]:+.2f} € vs Rule-based")
    f2.metric("Kosten (prijs > 0)",   f"-{cost_p:.2f} €",  delta_color="inverse")
    f3.metric("Inkomsten (prijs ≤ 0)", f"+{inc_neg:.2f} €")
    f4.metric("Ontlaad-inkomsten",    f"+{inc_dis:.2f} €")

    # Comparison table
    st.markdown("#### 📊 Vergelijking")
    comp = pd.DataFrame({
        "Metric":      ["Net Revenue (€)", "Geladen (kWh)", "Ontladen (kWh)", "Eind SOC (%)"],
        "Rule-based":  [round(sim["cum_rev"].iloc[-1], 2),
                        round(sim["energy_kwh"].sum(), 1),
                        round(sim[sim["action"] == "DISCHARGE"]["energy_kwh"].sum(), 1),
                        round(sim["soc"].iloc[-1], 1)],
        "MILP":        [milp_summ["total_net_revenue_eur"],
                        milp_summ["total_charged_kwh"],
                        milp_summ["total_discharged_kwh"],
                        milp_summ["final_soc_pct"]],
    })
    st.dataframe(comp, use_container_width=True, hide_index=True)

    # MILP actions chart
    st.markdown("#### 📋 MILP Acties")
    fig_milp_actions = go.Figure()
    fig_milp_actions.add_trace(go.Scatter(
        x=milp_df["datetime"], y=milp_df["price_eur_mwh"],
        mode="lines", name="Prijs", line=dict(color="gray")))
    c_mask = milp_df["charge_kwh"]    > 0.01
    d_mask = milp_df["discharge_kwh"] > 0.01
    fig_milp_actions.add_trace(go.Scatter(
        x=milp_df[c_mask]["datetime"], y=milp_df[c_mask]["price_eur_mwh"],
        mode="markers", name="LADEN", marker=dict(color="green", size=9, symbol="triangle-up")))
    fig_milp_actions.add_trace(go.Scatter(
        x=milp_df[d_mask]["datetime"], y=milp_df[d_mask]["price_eur_mwh"],
        mode="markers", name="ONTLADEN", marker=dict(color="red", size=9, symbol="triangle-down")))
    fig_milp_actions.update_layout(title="MILP Optimale Acties",
                                    xaxis_title="Tijd", yaxis_title="€/MWh")
    st.plotly_chart(fig_milp_actions, use_container_width=True)

    # Detailed action table
    action_mask = (milp_df["charge_kwh"] > 0.01) | (milp_df["discharge_kwh"] > 0.01)
    detail_df   = milp_df[action_mask][
        ["datetime", "price_eur_mwh", "charge_kwh", "discharge_kwh",
         "net_revenue_eur", "soc_pct"]].copy()
    detail_df["Type"] = detail_df["net_revenue_eur"].apply(
        lambda x: "🟢 Inkomsten" if x > 0 else ("🔴 Kosten" if x < 0 else "⚪ Nul"))
    detail_df = detail_df.rename(columns={
        "datetime": "Tijd", "price_eur_mwh": "Prijs (€/MWh)",
        "charge_kwh": "Laden (kWh)", "discharge_kwh": "Ontladen (kWh)",
        "net_revenue_eur": "Slot Revenue (€)", "soc_pct": "SOC (%)"})
    st.dataframe(detail_df, use_container_width=True, hide_index=True, height=350)

    # Solver log
    with st.expander("🔍 CBC Solver log (technisch)", expanded=False):
        st.code(milp_summ.get("solver_log", "Geen log beschikbaar."), language="text")

    # Next run hint
    st.info(
        f"💡 **Volgende periode?** De batterij eindigt op **{milp_summ['final_soc_pct']:.1f}% SOC**. "
        "Selecteer de volgende periode en gebruik bovenstaande checkbox in de sidebar om "
        "dit als startpunt te nemen — dan klopt de SOC-continuïteit tussen periodes."
    )

    if st.button("🔄 Reset MILP resultaten"):
        st.session_state.milp_schedule = None
        st.session_state.milp_summary  = None
        st.session_state.milp_pending  = False
        st.rerun()
