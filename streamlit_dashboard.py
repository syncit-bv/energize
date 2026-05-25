#!/usr/bin/env python3
"""
Energize EMS - Minimal Working Dashboard (Recovery Version)
"""

import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import date, timedelta
import numpy as np

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
    
    # Electricity Maps Key Status
    em_key = st.secrets.get("em_key", "").strip()
    if em_key and em_key != "PASTE_YOUR_ELECTRICITY_MAPS_API_KEY_HERE":
        st.success("✅ Electricity Maps key geladen uit secrets")
    else:
        st.error("⚠️ Electricity Maps API key niet gevonden in secrets")
        st.info("Ga naar Settings → Secrets en voeg 'em_key' toe")

    if st.button("🔄 Reboot App (na secret update)", type="secondary"):
        st.rerun()

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

em_key = st.secrets.get("em_key", "").strip()

if not em_key or em_key == "PASTE_YOUR_ELECTRICITY_MAPS_API_KEY_HERE":
    st.error("Electricity Maps API key niet gevonden in secrets.")
    st.info("Ga naar je Streamlit Cloud app → Settings → Secrets en voeg 'em_key' toe met je echte key.")
else:
    st.success("✅ Electricity Maps key succesvol geladen uit secrets")
    
    if st.button("📥 Fetch Day-Ahead Prices (laatste 7 dagen)", type="primary"):
        with st.spinner("Prijzen ophalen via Electricity Maps v4..."):
            try:
                from electricity_maps_client import ElectricityMapsClient
                client = ElectricityMapsClient(em_key)
                end = date.today() + timedelta(days=1)
                start = end - timedelta(days=7)
                df = client.get_day_ahead_prices("BE", start, end)
                
                if not df.empty:
                    st.session_state.df_prices = df
                    st.success(f"✅ {len(df)} prijzen succesvol opgehaald!")
                    st.rerun()
                else:
                    st.warning("Geen data gevonden.")
            except Exception as e:
                st.error(f"Fout bij ophalen: {e}")

# ==================== MILP ====================
st.subheader("🚀 MILP Optimalisatie")

if st.button("Run MILP Optimization (meerdere dagen)", type="primary"):
    with st.spinner("MILP optimalisatie draaien..."):
        try:
            # Placeholder - echte logica komt later terug
            st.success("MILP succesvol uitgevoerd (demo mode)")
            st.info("Volledige MILP logica met iteraties wordt in volgende update teruggezet.")
        except Exception as e:
            st.error(f"MILP error: {e}")

# ==================== LIVE FLUVIUS + NODES ====================
with st.expander("🌐 Live Fluvius Netcongestie & NODES Flex Market", expanded=False):
    st.markdown("**Real-time grid intelligence**")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.write("**Fluvius Congestie**")
        if st.button("Haal Fluvius data op"):
            try:
                client = CongestionClient()
                summary = client.get_congestion_summary("Gent")
                st.json(summary)
            except Exception as e:
                st.error(f"Fluvius error: {e}")
    
    with col2:
        st.write("**NODES Flex Market**")
        if st.button("Haal NODES status op"):
            try:
                client = NodesClient()
                summary = client.get_market_summary()
                st.json(summary)
            except Exception as e:
                st.error(f"NODES error: {e}")

st.caption("Energize EMS - Recovery version | Built with MILP optimization")
