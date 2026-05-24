#!/usr/bin/env python3
"""
EMS MVP Dashboard - Streamlit App
Run with: streamlit run streamlit_dashboard.py
Requires: pip install streamlit pandas matplotlib plotly
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path

# MILP Optimizer
from milp_optimizer import optimize_battery_schedule

st.set_page_config(page_title="EMS Belgium MVP Dashboard", layout="wide")
st.title("⚡ EMS Belgium - Battery & Grid Intelligence Dashboard")
st.markdown("**MVP Prototype** | Belgian day-ahead prices | Smart arbitrage + free electricity charging | Grid balancing")

# Sidebar controls
st.sidebar.header("Battery & Strategy Parameters")
battery_kwh = st.sidebar.slider("Usable Battery Capacity (kWh)", 5.0, 30.0, 10.0, 0.5)
max_power_kw = st.sidebar.slider("Max Charge/Discharge Power (kW)", 2.0, 11.0, 5.0, 0.5)
charge_thresh = st.sidebar.slider("Charge if price below (€/MWh)", 0, 80, 50)
discharge_thresh = st.sidebar.slider("Discharge if price above (€/MWh)", 100, 250, 160)
negative_boost = st.sidebar.checkbox("Aggressive charge on negative prices", value=True)
min_soc_pct = st.sidebar.slider("Minimum SOC reserve (%)", min_value=0, max_value=30, value=10, step=1,
                                help="Batterij nooit verder ontladen dan dit percentage. Beschermt de batterij en laat altijd buffer over.")

st.sidebar.markdown("---")

# MILP button placed in sidebar with other controls
if st.sidebar.button("🚀 Run MILP Optimization", type="primary"):
    st.session_state.run_milp = True

st.sidebar.info("Rule-based simulator + PuLP MILP optimization engine active. 10% SOC reserve is enforced in both.")

# Load data (use pre-generated parquet, or upload XML/parquet directly in the app)
@st.cache_data
def load_data():
    parquet_path = Path("prices_belgium.parquet")
    if parquet_path.exists():
        return pd.read_parquet(parquet_path)
    else:
        return pd.DataFrame()

# Use session_state to persist dataframe across reruns / uploads
if "df_prices" not in st.session_state:
    st.session_state.df_prices = load_data()

df = st.session_state.df_prices

# If no data yet, allow upload of XML or parquet
if df.empty:
    st.warning("Geen prijzen-data gevonden. Upload de originele ENTSO-E XML of een prices_belgium.parquet bestand.")
    
    uploaded_file = st.file_uploader(
        "Upload ENTSO-E XML of prices_belgium.parquet",
        type=["xml", "parquet"],
        help="De XML uit je attachments map, of de parquet die price_parser.py genereert."
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
            
            # Offer download of parquet for GitHub / future use
            parquet_bytes = df.to_parquet(index=False)
            st.download_button(
                label="📥 Download als prices_belgium.parquet (voor toekomstig gebruik / GitHub)",
                data=parquet_bytes,
                file_name="prices_belgium.parquet",
                mime="application/octet-stream"
            )
            st.rerun()

# Safety check
if st.session_state.df_prices.empty:
    st.info("Tip: Run lokaal `python price_parser.py` (pas eventueel het pad naar de XML aan) om de parquet te genereren en commit die.")
    st.stop()

df = st.session_state.df_prices  # ensure we use the session state version

# Date range selector
min_date = df['datetime'].min().date()
max_date = df['datetime'].max().date()
date_range = st.date_input("Select period for analysis", 
                           value=(pd.to_datetime("2026-04-25").date(), pd.to_datetime("2026-05-03").date()),
                           min_value=min_date, max_value=max_date)

# Filter
mask = (df['datetime'].dt.date >= date_range[0]) & (df['datetime'].dt.date <= date_range[1])
sim_df = df[mask].copy()

st.subheader(f"Price Overview ({date_range[0]} → {date_range[1]})")
fig_price = px.line(sim_df, x='datetime', y='price_eur_mwh', 
                    title="Day-ahead Electricity Prices Belgium (€/MWh)",
                    labels={'price_eur_mwh': 'Price (€/MWh)', 'datetime': 'Time'})
fig_price.add_hline(y=charge_thresh, line_dash="dash", line_color="green", annotation_text="Charge threshold")
fig_price.add_hline(y=discharge_thresh, line_dash="dash", line_color="red", annotation_text="Discharge threshold")
st.plotly_chart(fig_price, use_container_width=True)

# Negative price highlight
neg_count = (sim_df['price_eur_mwh'] < 0).sum()
if neg_count > 0:
    st.success(f"🎉 {neg_count} quarters with **negative prices** in this period → perfect moments for 'free or paid charging' + grid support!")

# Simple simulation (re-run with sidebar params for interactivity)
st.subheader("Battery Simulation Results (Rule-based MVP)")

# Re-simulate with current params (simplified version of backtester)
def quick_simulate(data, cap_kwh, pwr_kw, ch_thresh, dis_thresh, neg_boost, min_soc=0.10):
    soc = 0.5
    cap_mwh = cap_kwh / 1000
    max_e_slot = (pwr_kw * 0.25) / 1000
    results = []
    cum_rev = 0.0
    for _, row in data.iterrows():
        p = row['price_eur_mwh']
        action = "HOLD"
        e_mwh = 0.0
        rev = 0.0
        if p < 0 and neg_boost:
            e = min(max_e_slot, (1 - soc) * cap_mwh / 0.96)
            if e > 0.0001:
                e_mwh = e
                soc += e_mwh * 0.96 / cap_mwh
                rev = -e_mwh * p
                action = "CHARGE (NEG)"
        elif p < ch_thresh:
            e = min(max_e_slot, (1 - soc) * cap_mwh / 0.96)
            if e > 0.0001:
                e_mwh = e
                soc += e_mwh * 0.96 / cap_mwh
                rev = -e_mwh * p
                action = "CHARGE"
        elif p > dis_thresh:
            # KEY IMPROVEMENT: never discharge below min_soc (default 10% reserve)
            available = max(0.0, (soc - min_soc) * cap_mwh * 0.96)
            discharge_possible = min(max_e_slot, available)
            if discharge_possible > 0.0001:
                e_mwh = discharge_possible
                soc -= e_mwh / (cap_mwh * 0.96)
                rev = e_mwh * p
                action = "DISCHARGE"
        cum_rev += rev
        results.append({
            'datetime': row['datetime'],
            'price': p,
            'action': action,
            'energy_kwh': e_mwh * 1000,
            'revenue': rev,
            'soc': soc * 100,
            'cum_rev': cum_rev
        })
    return pd.DataFrame(results)

sim = quick_simulate(sim_df, battery_kwh, max_power_kw, charge_thresh, discharge_thresh, negative_boost, min_soc_pct / 100)

col1, col2, col3 = st.columns(3)
col1.metric("Net Revenue", f"{sim['cum_rev'].iloc[-1]:.2f} €")
col2.metric("Energy Charged", f"{sim['energy_kwh'].sum():.1f} kWh")
col3.metric("Avg SOC", f"{sim['soc'].mean():.1f} %")

# Actions plot
fig_actions = go.Figure()
fig_actions.add_trace(go.Scatter(x=sim['datetime'], y=sim['price'], mode='lines', name='Price', line=dict(color='gray')))
charge_pts = sim[sim['action'].str.contains('CHARGE')]
dis_pts = sim[sim['action'] == 'DISCHARGE']
fig_actions.add_trace(go.Scatter(x=charge_pts['datetime'], y=charge_pts['price'], mode='markers', name='CHARGE', marker=dict(color='green', size=8)))
fig_actions.add_trace(go.Scatter(x=dis_pts['datetime'], y=dis_pts['price'], mode='markers', name='DISCHARGE', marker=dict(color='red', size=8)))
fig_actions.update_layout(title="Price + EMS Actions", xaxis_title="Time", yaxis_title="€/MWh")
st.plotly_chart(fig_actions, use_container_width=True)

# SOC and revenue
fig_soc = px.line(sim, x='datetime', y='soc', title="Battery State of Charge (%)", color_discrete_sequence=['blue'])
st.plotly_chart(fig_soc, use_container_width=True)

fig_rev = px.area(sim, x='datetime', y='cum_rev', title="Cumulative Revenue (€) from Smart Charging/Discharging")
st.plotly_chart(fig_rev, use_container_width=True)

st.markdown("---")

# ==================== MILP OPTIMIZATION SECTION ====================
with st.expander("🚀 Run MILP Optimization (true optimal schedule vs rule-based)", expanded=False):
    st.markdown("""
    **MILP** zoekt de écht optimale laad/ontlaad planning over de geselecteerde periode, 
    rekening houdend met je **minimum SOC reserve**, efficiëntie en alle toekomstige prijzen.
    Dit is wat een professioneel EMS platform doet na de day-ahead publicatie om 15:00.
    """)

    if st.button("Run MILP Optimization on current period", type="primary"):
        with st.spinner("Solving MILP with PuLP (CBC solver)..."):
            try:
                milp_schedule, milp_summary = optimize_battery_schedule(
                    sim_df,
                    battery_kwh=battery_kwh,
                    max_power_kw=max_power_kw,
                    min_soc=min_soc_pct / 100,
                    initial_soc=0.50
                )

                st.success(f"MILP solved successfully! Status: {milp_summary['status']}")

                # Comparison metrics
                col1, col2, col3 = st.columns(3)
                col1.metric("MILP Net Revenue", f"{milp_summary['total_net_revenue_eur']:.2f} €", 
                            delta=f"{milp_summary['total_net_revenue_eur'] - sim['cum_rev'].iloc[-1]:.2f} € vs Rule-based")
                col2.metric("MILP Charged", f"{milp_summary['total_charged_kwh']:.1f} kWh")
                col3.metric("MILP Final SOC", f"{milp_summary['final_soc_pct']:.1f} %")

                # Optimal schedule plot
                fig_milp = go.Figure()
                fig_milp.add_trace(go.Scatter(x=milp_schedule['datetime'], y=milp_schedule['price_eur_mwh'], 
                                              mode='lines', name='Price', line=dict(color='gray', width=1)))
                
                # Charge actions
                charge_mask = milp_schedule['charge_kwh'] > 0.01
                fig_milp.add_trace(go.Scatter(x=milp_schedule[charge_mask]['datetime'], 
                                              y=milp_schedule[charge_mask]['price_eur_mwh'],
                                              mode='markers', name='MILP CHARGE', 
                                              marker=dict(color='green', size=10, symbol='triangle-up')))
                
                # Discharge actions
                discharge_mask = milp_schedule['discharge_kwh'] > 0.01
                fig_milp.add_trace(go.Scatter(x=milp_schedule[discharge_mask]['datetime'], 
                                              y=milp_schedule[discharge_mask]['price_eur_mwh'],
                                              mode='markers', name='MILP DISCHARGE', 
                                              marker=dict(color='red', size=10, symbol='triangle-down')))
                
                fig_milp.update_layout(title="MILP Optimal Schedule (green = charge, red = discharge)",
                                       xaxis_title="Time", yaxis_title="€/MWh")
                st.plotly_chart(fig_milp, use_container_width=True)

                # Comparison table
                st.subheader("Comparison: Rule-based vs MILP")
                comparison_df = pd.DataFrame({
                    "Metric": ["Net Revenue (€)", "Energy Charged (kWh)", "Energy Discharged (kWh)", "Final SOC (%)"],
                    "Rule-based": [round(sim['cum_rev'].iloc[-1], 2), 
                                   round(sim['energy_kwh'].sum(), 1),
                                   round(sim[sim['action'] == 'DISCHARGE']['energy_kwh'].sum(), 1),
                                   round(sim['soc'].iloc[-1], 1)],
                    "MILP Optimal": [milp_summary['total_net_revenue_eur'],
                                     milp_summary['total_charged_kwh'],
                                     milp_summary['total_discharged_kwh'],
                                     milp_summary['final_soc_pct']]
                })
                st.dataframe(comparison_df, use_container_width=True, hide_index=True)

            except Exception as e:
                st.error(f"MILP optimization failed: {str(e)}")
                st.info("Tip: Try a shorter period (e.g. one day like 1 May 2026) for faster solving.")