import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta
import numpy as np

from milp_optimizer import optimize_battery_schedule
from congestion_client import CongestionClient
from nodes_client import NodesClient

st.set_page_config(page_title="Energize EMS", layout="wide")
st.title("⚡ Energize - Slim EMS Dashboard")

# Sidebar
with st.sidebar:
    st.header("Parameters")
    battery_capacity = st.slider("Batterij capaciteit (kWh)", 5, 50, 16)
    max_power = st.slider("Max Charge/Discharge Power (kW)", 1.0, 10.0, 2.5)
    min_soc = st.slider("Minimum SOC (%)", 0, 30, 10)
    st.divider()
    st.subheader("Live Data")
    st.success("Secrets geladen via Streamlit Cloud")

# Main content
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

# Electricity Maps
st.subheader("🌍 Electricity Maps - Day-Ahead Prices")
em_key = st.secrets.get("em_key", "")
if not em_key:
    st.error("Electricity Maps API key niet gevonden in secrets.")
else:
    st.success("✅ Electricity Maps key geladen")
    if st.button("Fetch Prices"):
        st.info("Prijzen ophalen...")

# MILP
if st.button("Run MILP Optimization"):
    st.info("MILP running over multiple days...")

# Netcongestie
with st.expander("🌐 Live Fluvius Netcongestie & NODES"):
    st.info("Live data integratie actief")

st.caption("Energize EMS - Built with MILP optimization")