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

# ENTSO-E Live Integration (optional but powerful for production EMS)
try:
    from entsoe_client import EntsoeClient
    ENTSOE_AVAILABLE = True
except ImportError:
    ENTSOE_AVAILABLE = False

# Electricity Maps Integration (carbon intensity + forecasts)
try:
    from electricity_maps_client import ElectricityMapsClient
    ELECTRICITY_MAPS_AVAILABLE = True
except ImportError:
    ELECTRICITY_MAPS_AVAILABLE = False

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

min_end_soc_pct = st.sidebar.slider("Minimum End-of-Horizon SOC (%)", min_value=10, max_value=50, value=20, step=5,
                                    help="Minimum SOC aan het einde van de optimalisatie horizon. Voorkomt dat je 's avonds op 10% eindigt en de volgende dag niet kunt laden.")

st.sidebar.markdown("---")

# MILP button placed in sidebar with other controls
if "run_milp" not in st.session_state:
    st.session_state.run_milp = False

if st.sidebar.button("🚀 Run MILP Optimization", type="primary"):
    st.session_state.run_milp = True

# ENTSO-E Live Data Integration (NEW)
st.sidebar.markdown("---")
st.sidebar.subheader("🔌 ENTSO-E Live Data")

if ENTSOE_AVAILABLE:
    with st.sidebar.expander("Fetch fresh prices from ENTSO-E Transparency Platform", expanded=False):
        st.caption("Get the latest day-ahead prices automatically (requires free API key)")
        
        entsoe_key = st.text_input(
            "ENTSO-E API Key", 
            type="password",
            value=st.session_state.get("entsoe_key", ""),
            help="Get your free key at https://transparency.entsoe.eu/ → My Account → API Key"
        )
        
        if entsoe_key:
            st.session_state.entsoe_key = entsoe_key
        
        col1, col2 = st.columns(2)
        with col1:
            fetch_start = st.date_input("From date", value=pd.to_datetime("2026-05-20").date(), key="fetch_start")
        with col2:
            fetch_end = st.date_input("To date", value=pd.to_datetime("2026-05-25").date(), key="fetch_end")
        
        if st.button("📥 Fetch & Load from ENTSO-E", type="secondary"):
            if not entsoe_key:
                st.error("Please enter your ENTSO-E API key first.")
            else:
                try:
                    with st.spinner("Contacting ENTSO-E Transparency Platform..."):
                        client = EntsoeClient(entsoe_key)
                        new_df = client.get_day_ahead_prices(fetch_start, fetch_end)
                        
                        if not new_df.empty:
                            st.session_state.df_prices = new_df
                            st.success(f"✅ Loaded {len(new_df)} price points from ENTSO-E!")
                            st.balloons()
                            st.rerun()
                        else:
                            st.warning("No data returned. Check dates or try again later.")
                except Exception as e:
                    st.error(f"ENTSO-E fetch failed: {str(e)[:200]}")
                    st.info("Tip: Make sure your API key is valid and you have internet access.")
else:
    st.sidebar.info("Install `requests` and ensure entsoe_client.py is present for live ENTSO-E integration.")

# Electricity Maps Integration (NEW - Carbon + Forecasts)
if ELECTRICITY_MAPS_AVAILABLE:
    with st.sidebar.expander("🌍 Electricity Maps (Carbon + Forecasts)", expanded=False):
        st.caption("Get carbon intensity + forecasts. Great for smart 'green charging' decisions.")
        
        em_key = st.text_input(
            "Electricity Maps API Key",
            type="password",
            value=st.session_state.get("em_key", ""),
            help="Sandbox or Production key from Electricity Maps"
        )
        
        if em_key:
            st.session_state.em_key = em_key
        
        em_zone = st.selectbox("Zone", ["BE", "DE", "FR", "NL"], index=0, key="em_zone")
        
        if st.button("📊 Fetch Carbon Data", type="secondary"):
            if not em_key:
                st.error("Please enter your Electricity Maps API key.")
            else:
                try:
                    with st.spinner("Fetching from Electricity Maps..."):
                        em_client = ElectricityMapsClient(em_key, use_sandbox=True)
                        
                        carbon_latest = em_client.get_carbon_intensity_latest(em_zone)
                        carbon_forecast = em_client.get_carbon_intensity_forecast(em_zone)
                        
                        st.session_state.carbon_latest = carbon_latest
                        st.session_state.carbon_forecast = carbon_forecast
                        
                        st.success(f"✅ Carbon data loaded for {em_zone}!")
                        st.rerun()
                except Exception as e:
                    st.error(f"Electricity Maps error: {str(e)[:180]}")
else:
    st.sidebar.caption("electricity_maps_client.py not found.")

# Extra metric the user requested
max_energy_per_slot = max_power_kw * 0.25
st.sidebar.metric("Max power per slot", f"{max_energy_per_slot:.3f} kWh", 
                  help="Bij 2,5 kW max power mag je per 15 min maximaal 0,625 kWh laden of ontladen.")

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
    st.warning("Geen prijzen-data gevonden. Upload ENTSO-E XML, gebruik de live ENTSO-E fetch hierboven, of laad een prices_belgium.parquet.")
    
    uploaded_file = st.file_uploader(
        "Upload ENTSO-E XML of prices_belgium.parquet",
        type=["xml", "parquet"],
        help="De XML uit je attachments map, of de parquet die price_parser.py genereert. Of gebruik de ENTSO-E Live fetch in de sidebar!"
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

# Electricity Maps Carbon Insight (if fetched)
if st.session_state.get("carbon_latest"):
    carbon = st.session_state.carbon_latest
    zone = st.session_state.get("em_zone", "BE")
    ci = carbon.get("carbonIntensity", "N/A")
    updated = str(carbon.get("updatedAt", ""))[:16]
    st.info(f"🌍 **Carbon Intensity ({zone}):** {ci} gCO₂eq/kWh  |  Updated: {updated}")

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

# SOC and revenue (improved with fixed 0-100% scale + min reserve line)
fig_soc = px.line(sim, x='datetime', y='soc', title="Battery State of Charge (%) - Rule-based", color_discrete_sequence=['blue'])
fig_soc.update_yaxes(range=[0, 100])
fig_soc.add_hline(y=min_soc_pct, line_dash="dash", line_color="orange", 
                  annotation_text=f"Min {min_soc_pct}% SOC Reserve", annotation_position="top right")
st.plotly_chart(fig_soc, use_container_width=True)

fig_rev = px.area(sim, x='datetime', y='cum_rev', title="Cumulative Revenue (€) from Smart Charging/Discharging")
st.plotly_chart(fig_rev, use_container_width=True)

# ==================== COMBINED OVERLAY PLOTS (when MILP has been run) ====================
if st.session_state.get("milp_schedule") is not None:
    st.markdown("---")
    st.subheader("📊 Combined View: Rule-based vs MILP (Overlay)")

    milp_df = st.session_state.milp_schedule

    # Combined SOC plot
    fig_combined_soc = go.Figure()
    fig_combined_soc.add_trace(go.Scatter(x=sim['datetime'], y=sim['soc'], mode='lines', name='Rule-based', line=dict(color='blue')))
    fig_combined_soc.add_trace(go.Scatter(x=milp_df['datetime'], y=milp_df['soc_pct'], mode='lines', name='MILP Optimal', line=dict(color='#00AA00', width=2.5)))
    fig_combined_soc.update_yaxes(range=[0, 100], title="SOC (%)")
    fig_combined_soc.add_hline(y=min_soc_pct, line_dash="dash", line_color="orange", 
                               annotation_text=f"Min {min_soc_pct}% Reserve")
    fig_combined_soc.update_layout(title="Battery State of Charge (%) - Rule-based vs MILP")
    st.plotly_chart(fig_combined_soc, use_container_width=True)

    # Combined Cumulative Revenue plot
    fig_combined_rev = go.Figure()
    fig_combined_rev.add_trace(go.Scatter(x=sim['datetime'], y=sim['cum_rev'], mode='lines', name='Rule-based', line=dict(color='blue')))
    milp_df['cum_revenue_milp'] = milp_df['net_revenue_eur'].cumsum()
    fig_combined_rev.add_trace(go.Scatter(x=milp_df['datetime'], y=milp_df['cum_revenue_milp'], mode='lines', name='MILP Optimal', line=dict(color='#00AA00', width=2.5)))
    fig_combined_rev.update_layout(title="Cumulative Net Revenue (€) - Rule-based vs MILP", yaxis_title="€")
    st.plotly_chart(fig_combined_rev, use_container_width=True)

st.markdown("---")

# ==================== MILP RESULTS (in expander, triggered from sidebar) ====================
if st.session_state.get("run_milp", False):
    with st.expander("🚀 MILP Optimization Results", expanded=True):
        st.markdown("**MILP** zoekt de optimale planning met perfecte foresight over de geselecteerde periode, met harde 10% SOC reserve.")

        if st.button("Run MILP now (on current period)", key="run_milp_btn"):
            with st.spinner("Solving MILP..."):
                try:
                    milp_schedule, milp_summary = optimize_battery_schedule(
                        sim_df,
                        battery_kwh=battery_kwh,
                        max_power_kw=max_power_kw,
                        min_soc=min_soc_pct / 100,
                        min_end_soc=min_end_soc_pct / 100,
                        initial_soc=0.50
                    )

                    # Calculate transparent metrics with separation of positive vs negative prices
                    pos_price_charge = milp_schedule[(milp_schedule['charge_kwh'] > 0) & (milp_schedule['price_eur_mwh'] > 0)]
                    neg_price_charge = milp_schedule[(milp_schedule['charge_kwh'] > 0) & (milp_schedule['price_eur_mwh'] <= 0)]
                    discharge = milp_schedule[milp_schedule['discharge_kwh'] > 0]

                    cost_positive = abs(pos_price_charge['net_revenue_eur'].sum())      # Red - we pay
                    income_negative = abs(neg_price_charge['net_revenue_eur'].sum())    # Green - we receive
                    income_discharge = discharge['net_revenue_eur'].sum()               # Green - we receive

                    st.success(f"MILP solved! Status: {milp_summary['status']}")

                    # Store MILP results for overlay on main graphs
                    st.session_state.milp_schedule = milp_schedule
                    st.session_state.milp_summary = milp_summary

                    # Transparent colored metrics
                    st.markdown("#### 💰 MILP Financial Breakdown")
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("Net Revenue", f"{milp_summary['total_net_revenue_eur']:.2f} €",
                                delta=f"{milp_summary['total_net_revenue_eur'] - sim['cum_rev'].iloc[-1]:.2f} vs Rule-based")
                    col2.metric("Cost (price > 0)", f"-{cost_positive:.2f} €", 
                                help="What we pay when charging at positive prices", delta_color="inverse")
                    col3.metric("Income (price ≤ 0)", f"+{income_negative:.2f} €",
                                help="Money received for charging during negative / zero prices")
                    col4.metric("Discharge Income", f"+{income_discharge:.2f} €",
                                help="Money earned by discharging at high prices")

                    # Detailed table - only slots with actual Charge or Discharge action
                    st.markdown("#### 📋 MILP Actions (only quarters with activity)")
                    action_mask = (milp_schedule['charge_kwh'] > 0.01) | (milp_schedule['discharge_kwh'] > 0.01)
                    detail_df = milp_schedule[action_mask][['datetime', 'price_eur_mwh', 'charge_kwh', 'discharge_kwh', 'net_revenue_eur', 'soc_pct']].copy()
                    detail_df['Revenue Type'] = detail_df['net_revenue_eur'].apply(
                        lambda x: "🟢 Income" if x > 0 else ("🔴 Cost" if x < 0 else "⚪ Zero")
                    )
                    detail_df = detail_df.rename(columns={
                        'datetime': 'Time',
                        'price_eur_mwh': 'Price (€/MWh)',
                        'charge_kwh': 'Charge (kWh)',
                        'discharge_kwh': 'Discharge (kWh)',
                        'net_revenue_eur': 'Slot Revenue (€)',
                        'soc_pct': 'SOC (%)'
                    })
                    st.dataframe(detail_df, use_container_width=True, hide_index=True, height=400)

                    # Optimal schedule plot
                    fig_milp = go.Figure()
                    fig_milp.add_trace(go.Scatter(x=milp_schedule['datetime'], y=milp_schedule['price_eur_mwh'],
                                                  mode='lines', name='Price', line=dict(color='gray')))
                    charge_mask = milp_schedule['charge_kwh'] > 0.01
                    fig_milp.add_trace(go.Scatter(x=milp_schedule[charge_mask]['datetime'],
                                                  y=milp_schedule[charge_mask]['price_eur_mwh'],
                                                  mode='markers', name='MILP CHARGE',
                                                  marker=dict(color='green', size=9, symbol='triangle-up')))
                    discharge_mask = milp_schedule['discharge_kwh'] > 0.01
                    fig_milp.add_trace(go.Scatter(x=milp_schedule[discharge_mask]['datetime'],
                                                  y=milp_schedule[discharge_mask]['price_eur_mwh'],
                                                  mode='markers', name='MILP DISCHARGE',
                                                  marker=dict(color='red', size=9, symbol='triangle-down')))
                    fig_milp.update_layout(title="MILP Optimal Actions", xaxis_title="Time", yaxis_title="€/MWh")
                    st.plotly_chart(fig_milp, use_container_width=True)

                    # MILP-specific SOC and Cumulative Revenue plots (as requested)
                    st.markdown("#### 📈 MILP Battery State of Charge")
                    fig_milp_soc = px.line(milp_schedule, x='datetime', y='soc_pct', 
                                           title="MILP - State of Charge (%)", color_discrete_sequence=['#00AA00'])
                    fig_milp_soc.add_hline(y=min_soc_pct, line_dash="dash", line_color="orange", 
                                           annotation_text=f"Min {min_soc_pct}% reserve")
                    st.plotly_chart(fig_milp_soc, use_container_width=True)

                    st.markdown("#### 📈 MILP Cumulative Revenue")
                    milp_schedule['cum_revenue'] = milp_schedule['net_revenue_eur'].cumsum()
                    fig_milp_rev = px.area(milp_schedule, x='datetime', y='cum_revenue',
                                           title="MILP - Cumulative Net Revenue (€)", color_discrete_sequence=['#00AA00'])
                    st.plotly_chart(fig_milp_rev, use_container_width=True)

                    # Comparison
                    st.subheader("Rule-based vs MILP")
                    comp = pd.DataFrame({
                        "Metric": ["Net Revenue (€)", "Charged (kWh)", "Discharged (kWh)", "Final SOC (%)"],
                        "Rule-based": [round(sim['cum_rev'].iloc[-1], 2),
                                       round(sim['energy_kwh'].sum(), 1),
                                       round(sim[sim['action']=='DISCHARGE']['energy_kwh'].sum(), 1),
                                       round(sim['soc'].iloc[-1], 1)],
                        "MILP": [milp_summary['total_net_revenue_eur'],
                                 milp_summary['total_charged_kwh'],
                                 milp_summary['total_discharged_kwh'],
                                 milp_summary['final_soc_pct']]
                    })
                    st.dataframe(comp, use_container_width=True, hide_index=True)

                except Exception as e:
                    st.error(f"MILP failed: {e}")

        if st.button("Reset MILP view"):
            st.session_state.run_milp = False
            st.rerun()