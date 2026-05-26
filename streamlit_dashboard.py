#!/usr/bin/env python3
"""
Energize EMS - Herstelde versie (clean)
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import date, timedelta

from milp_optimizer import optimize_battery_schedule

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

st.set_page_config(page_title="Energize EMS", layout="wide")
st.title("⚡ Energize - Slim EMS Dashboard")

# ====================== SECRETS ======================
em_key = st.secrets.get("em_key", "")
entsoe_key = st.secrets.get("entsoe_key", "")

# ====================== SIDEBAR ======================
with st.sidebar:
    st.header("Battery Parameters")
    battery_kwh = st.slider("Batterij capaciteit (kWh)", 5.0, 30.0, 16.0, 0.5)
    max_power_kw = st.slider("Max Power (kW)", 1.0, 10.0, 2.5, 0.5)
    min_soc_pct = st.slider("Min SOC reserve (%)", 0, 30, 10)
    min_end_soc_pct = st.slider("Min End-of-Day SOC (%)", 10, 50, 20, 5)

    st.divider()
    if st.button("Reboot App"):
        st.rerun()

# ====================== DATE BUTTONS ======================
st.subheader("📅 Periode")

today = date.today()
tomorrow = today + timedelta(days=1)

if "date_start" not in st.session_state:
    st.session_state.date_start = today - timedelta(days=6)
    st.session_state.date_end = today

col1, col2, col3, _ = st.columns([1.2, 1.4, 1.2, 2])

with col1:
    if st.button(f"📅 Vandaag ({today.strftime('%d %b')})", 
                 type="primary" if st.session_state.date_start == today else "secondary"):
        st.session_state.date_start = today
        st.session_state.date_end = today
        st.rerun()

with col2:
    if st.button(f"📆 Day-ahead ({tomorrow.strftime('%d %b')})", 
                 type="primary" if st.session_state.date_start == tomorrow else "secondary"):
        st.session_state.date_start = tomorrow
        st.session_state.date_end = tomorrow
        st.rerun()

with col3:
    if st.button("🗓️ Deze Week", type="secondary"):
        st.session_state.date_start = today - timedelta(days=6)
        st.session_state.date_end = today
        st.rerun()

date_range = st.date_input("Of kies eigen periode:", 
                           value=(st.session_state.date_start, st.session_state.date_end))

if date_range and len(date_range) == 2:
    st.session_state.date_start, st.session_state.date_end = date_range

# ====================== DATA ======================
if "df_prices" not in st.session_state or st.session_state.df_prices.empty:
    st.warning("Geen prijsdata. Haal data op.")
    if st.button("Haal data op (laatste 10 dagen)", type="primary"):
        try:
            end = date.today() + timedelta(days=1)
            start = date.today() - timedelta(days=10)
            
            if entsoe_key:
                from entsoe_client import EntsoeClient
                client = EntsoeClient(entsoe_key)
                df = client.get_day_ahead_prices(start, end)
            else:
                client = ElectricityMapsClient(em_key)
                df = client.get_day_ahead_prices("BE", start, end)
            
            st.session_state.df_prices = df
            st.success(f"✅ {len(df)} prijzen geladen")
            st.rerun()
        except Exception as e:
            st.error(f"Fout: {e}")
    st.stop()

df = st.session_state.df_prices
mask = (df['datetime'].dt.date >= st.session_state.date_start) & (df['datetime'].dt.date <= st.session_state.date_end)
sim_df = df[mask].copy()

# ====================== MILP ======================
st.subheader("🚀 MILP Optimalisatie")

has_tomorrow = st.session_state.date_end >= tomorrow
st.info("✅ Day-ahead prijzen bekend" if has_tomorrow else "⚠️ Day-ahead prijzen voor morgen nog niet bekend")

col1, col2 = st.columns(2)

with col1:
    if st.button("Run MILP mét day-ahead", type="primary"):
        with st.spinner("Bezig..."):
            schedule, summary = optimize_battery_schedule(
                sim_df, battery_kwh=battery_kwh, max_power_kw=max_power_kw,
                min_soc=min_soc_pct/100, min_end_soc=min_end_soc_pct/100,
                initial_soc=0.5, time_horizon_hours=None
            )
            st.session_state.milp_full = {"schedule": schedule, "summary": summary}
            st.success("MILP mét day-ahead klaar")

with col2:
    if st.button("Run MILP zónder day-ahead"):
        with st.spinner("Bezig..."):
            schedule, summary = optimize_battery_schedule(
                sim_df, battery_kwh=battery_kwh, max_power_kw=max_power_kw,
                min_soc=min_soc_pct/100, min_end_soc=min_end_soc_pct/100,
                initial_soc=0.5, time_horizon_hours=None
            )
            st.session_state.milp_conserv = {"schedule": schedule, "summary": summary}
            st.success("MILP zónder day-ahead klaar")

# Vergelijking
if "milp_full" in st.session_state:
    s = st.session_state.milp_full["summary"]
    st.success(f"MILP mét day-ahead → {s['total_net_revenue_eur']:.2f} €")

if "milp_conserv" in st.session_state:
    s = st.session_state.milp_conserv["summary"]
    st.info(f"MILP zónder day-ahead → {s['total_net_revenue_eur']:.2f} €")

# ====================== ELIA (simpel gehouden) ======================
with st.expander("🌐 Elia Grid Intelligence", expanded=False):
    if ELIA_AVAILABLE:
        elia = EliaClient()
        if st.button("Haal Elia status"):
            st.json(elia.get_market_summary())
    else:
        st.info("elia_client.py nog niet volledig")

st.caption("Energize EMS - Herstelde versie")
