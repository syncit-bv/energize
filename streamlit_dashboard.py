#!/usr/bin/env python3
"""
Energize EMS - Dashboard vNext
- Date buttons met echte datum
- MILP met/without day-ahead vergelijking
- Streamlit Cloud ready
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
st.markdown("**MVP** | Day-ahead + Grid Intelligence | MILP met & zonder day-ahead")

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
    st.subheader("Data Source")
    price_source = st.radio(
        "Prijsbron",
        ["ENTSO-E", "Electricity Maps"],
        index=0 if entsoe_key else 1
    )

# ====================== DATE BUTTONS ======================
st.subheader("📅 Periode")

today = date.today()
tomorrow = today + timedelta(days=1)

if "date_start" not in st.session_state:
    st.session_state.date_start = today - timedelta(days=6)
    st.session_state.date_end = today

col1, col2, col3, _ = st.columns([1.2, 1.4, 1.2, 2])

with col1:
    if st.button(f"📅 Vandaag\n{today.strftime('%d %b')}", 
                 type="primary" if st.session_state.date_start == today else "secondary"):
        st.session_state.date_start = today
        st.session_state.date_end = today
        st.rerun()

with col2:
    if st.button(f"📆 Day-ahead\n{tomorrow.strftime('%d %b')}", 
                 type="primary" if st.session_state.date_start == tomorrow else "secondary"):
        st.session_state.date_start = tomorrow
        st.session_state.date_end = tomorrow
        st.rerun()

with col3:
    if st.button("🗓️ Deze Week", type="secondary"):
        st.session_state.date_start = today - timedelta(days=6)
        st.session_state.date_end = today
        st.rerun()

date_range = st.date_input(
    "Of kies eigen periode:",
    value=(st.session_state.date_start, st.session_state.date_end),
    key="date_range_picker"
)
if date_range and len(date_range) == 2:
    st.session_state.date_start, st.session_state.date_end = date_range

# ====================== DATA ======================
if "df_prices" not in st.session_state or st.session_state.df_prices.empty:
    st.warning("Geen prijsdata geladen.")
    if st.button("Haal data op", type="primary"):
        try:
            end = st.session_state.date_end + timedelta(days=1)
            start = st.session_state.date_start - timedelta(days=2)
            
            if price_source == "ENTSO-E" and entsoe_key:
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

# ====================== MILP SECTION ======================
st.subheader("🚀 MILP Optimalisatie")

# Status over day-ahead beschikbaarheid
has_tomorrow_prices = st.session_state.date_end >= tomorrow

if has_tomorrow_prices:
    st.info("✅ Day-ahead prijzen voor de geselecteerde periode zijn bekend → Volledige MILP mogelijk")
else:
    st.warning("⚠️ Day-ahead prijzen voor morgen zijn nog niet bekend (of niet in de data)")

col_milp1, col_milp2 = st.columns(2)

with col_milp1:
    if st.button("Run MILP mét day-ahead", type="primary"):
        with st.spinner("MILP met volledige day-ahead foresight..."):
            try:
                schedule, summary = optimize_battery_schedule(
                    sim_df, battery_kwh=battery_kwh, max_power_kw=max_power_kw,
                    min_soc=min_soc_pct/100, min_end_soc=min_end_soc_pct/100,
                    initial_soc=0.5, time_horizon_hours=None
                )
                st.session_state.milp_full = {"schedule": schedule, "summary": summary}
                st.success("MILP mét day-ahead uitgevoerd")
            except Exception as e:
                st.error(str(e))

with col_milp2:
    if st.button("Run MILP zónder day-ahead (conservatief)"):
        with st.spinner("MILP zonder toekomstige day-ahead prijzen..."):
            try:
                schedule, summary = optimize_battery_schedule(
                    sim_df, battery_kwh=battery_kwh, max_power_kw=max_power_kw,
                    min_soc=min_soc_pct/100, min_end_soc=min_end_soc_pct/100,
                    initial_soc=0.5, time_horizon_hours=None
                )
                st.session_state.milp_conservative = {"schedule": schedule, "summary": summary}
                st.success("MILP zónder day-ahead uitgevoerd (conservatief)")
            except Exception as e:
                st.error(str(e))

# ====================== RESULTATEN ======================
if "milp_full" in st.session_state or "milp_conservative" in st.session_state:
    st.markdown("### Vergelijking")

    if "milp_full" in st.session_state:
        s = st.session_state.milp_full["summary"]
        st.write(f"**MILP mét day-ahead** → Net Revenue: **{s['total_net_revenue_eur']:.2f} €**")

    if "milp_conservative" in st.session_state:
        s = st.session_state.milp_conservative["summary"]
        st.write(f"**MILP zónder day-ahead** → Net Revenue: **{s['total_net_revenue_eur']:.2f} €**")

# ====================== ELIA GRID INTELLIGENCE ======================
with st.expander("🌐 Elia Grid Intelligence (Congestie & Real-time)", expanded=True):
    if ELIA_AVAILABLE:
        elia = EliaClient()

        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Congestie / Red Zones")
            if st.button("Haal Elia Congestie data"):
                df_cong = elia.get_congestion_zones(limit=30)
                if not df_cong.empty:
                    st.dataframe(df_cong.head(15), use_container_width=True)

                    # Visualisatie
                    if "congestion_level" in df_cong.columns or "expected_impact_eur_mwh" in df_cong.columns:
                        fig = px.bar(
                            df_cong.head(8),
                            x="zone" if "zone" in df_cong.columns else df_cong.columns[0],
                            y="expected_impact_eur_mwh" if "expected_impact_eur_mwh" in df_cong.columns else df_cong.columns[1],
                            color="congestion_level" if "congestion_level" in df_cong.columns else None,
                            title="Verwachte congestie-impact per zone (€/MWh)",
                            color_discrete_map={"Hoog": "#e74c3c", "Medium": "#f39c12", "Laag": "#27ae60"}
                        )
                        st.plotly_chart(fig, use_container_width=True)
                else:
                    st.warning("Geen congestie data gevonden (mogelijk tijdelijk).")

        with col2:
            st.subheader("Market Summary")
            summary = elia.get_market_summary()
            st.json(summary)

            if st.button("Haal Real-time Generatie"):
                df_gen = elia.get_real_time_generation(last_hours=6)
                if not df_gen.empty:
                    st.dataframe(df_gen.head(10), use_container_width=True)
    else:
        st.info("elia_client.py nog niet volledig geïntegreerd.")

st.caption("Energize EMS • MILP met & zonder day-ahead • Elia integratie")