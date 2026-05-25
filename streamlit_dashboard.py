#!/usr/bin/env python3
"""
Energize EMS - Step 2: Real MILP Optimization
"""

import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import date, timedelta

from milp_optimizer import optimize_battery_schedule
from congestion_client import CongestionClient
from nodes_client import NodesClient

st.set_page_config(page_title="Energize EMS", layout="wide")
st.title("⚡ Energize - Slim EMS Dashboard")

# ==================== SIDEBAR ====================
with st.sidebar:
    st.header("Parameters")
    battery_kwh = st.slider("Batterij capaciteit (kWh)", 5.0, 30.0, 16.0, 0.5)
    max_power_kw = st.slider("Max Charge/Discharge Power (kW)", 1.0, 10.0, 2.5, 0.5)
    min_soc_pct = st.slider("Minimum SOC reserve (%)", 0, 30, 10)
    
    st.divider()
    st.subheader("Live Data")

    em_key = st.secrets.get("em_key", "")
    if isinstance(em_key, str):
        em_key = em_key.strip()
    
    if em_key and "PASTE" not in em_key.upper():
        st.success("✅ Electricity Maps key geladen")
    else:
        st.error("⚠️ Electricity Maps key niet geldig in secrets")

# ==================== WINSTOVERZICHT ====================
st.subheader("💰 Winstoverzicht")

col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric("Vandaag", "+ €23.45", "+12%")
with col2:
    st.metric("Deze week", "+ €148.70")
with col3:
    st.metric("Deze maand", "+ €487")
with col4:
    st.metric("Dit jaar", "+ €2,845")
with col5:
    st.metric("Sinds installatie", "+ €6,732")

# ==================== ELECTRICITY MAPS ====================
st.subheader("🌍 Electricity Maps - Day-Ahead Prices")

em_key = st.secrets.get("em_key", "")
if isinstance(em_key, str):
    em_key = em_key.strip()

if not em_key or "PASTE" in em_key.upper():
    st.error("Electricity Maps API key niet geldig in secrets.")
else:
    st.success("✅ Key succesvol geladen uit secrets")
    
    if st.button("📥 Fetch Prices (laatste 7 dagen)", type="primary"):
        with st.spinner("Ophalen..."):
            try:
                from electricity_maps_client import ElectricityMapsClient
                client = ElectricityMapsClient(em_key)
                end = date.today() + timedelta(days=1)
                start = end - timedelta(days=7)
                df = client.get_day_ahead_prices("BE", start, end)
                
                if not df.empty:
                    st.session_state.df_prices = df
                    st.success(f"✅ {len(df)} prijzen opgehaald!")
                else:
                    st.warning("Geen data.")
            except Exception as e:
                st.error(f"Fout: {e}")

# ==================== MILP OPTIMALISATIE (Stap 2) ====================
st.subheader("🚀 MILP Optimalisatie")

if st.button("Run MILP Optimization (meerdere dagen)", type="primary"):
    with st.spinner("MILP optimalisatie draaien over de geselecteerde periode..."):
        try:
            milp_schedule, milp_summary = optimize_battery_schedule(
                sim_df,
                battery_kwh=battery_kwh,
                max_power_kw=max_power_kw,
                min_soc=min_soc_pct / 100,
                min_end_soc=0.20,
                initial_soc=0.50
            )
            
            st.session_state.milp_schedule = milp_schedule
            st.session_state.milp_summary = milp_summary
            
            st.success(f"MILP succesvol! Status: {milp_summary.get('status', 'OK')}")
            
            # Basis metrics
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Net Revenue", f"{milp_summary['total_net_revenue_eur']:.2f} €")
            col2.metric("Charged", f"{milp_summary['total_charged_kwh']:.1f} kWh")
            col3.metric("Discharged", f"{milp_summary['total_discharged_kwh']:.1f} kWh")
            col4.metric("Final SOC", f"{milp_summary['final_soc_pct']:.1f} %")
            
            # Eenvoudige actie tabel
            st.markdown("#### MILP Acties (enkele rijen)")
            action_mask = (milp_schedule['charge_kwh'] > 0.01) | (milp_schedule['discharge_kwh'] > 0.01)
            detail = milp_schedule[action_mask][['datetime', 'price_eur_mwh', 'charge_kwh', 'discharge_kwh', 'net_revenue_eur']].head(12)
            st.dataframe(detail, use_container_width=True, hide_index=True)
            
        except Exception as e:
            st.error(f"MILP fout: {e}")

# ==================== FLUVIUS + NODES ====================
with st.expander("🌐 Live Fluvius & NODES", expanded=False):
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Fluvius data"):
            try:
                st.json(CongestionClient().get_congestion_summary("Gent"))
            except Exception as e:
                st.error(str(e))
    with col2:
        if st.button("NODES status"):
            try:
                st.json(NodesClient().get_market_summary())
            except Exception as e:
                st.error(str(e))

st.caption("Energize EMS - Stap 2: Real MILP optimization")
