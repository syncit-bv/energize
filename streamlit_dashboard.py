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
    optimize_battery_schedule_wind_solar,
    estimate_own_solar_kwh,
    battery_sizing_analysis as _battery_sizing_raw,
)

@st.cache_data(show_spinner=False, ttl=3600)
def _battery_sizing_cached(data_hash: str, prices_df, battery_sizes_kwh,
                             max_power_kw, charge_power_kw,
                             min_soc, initial_soc, capex_per_kwh, lifespan_years):
    """Interne gecachede kern — niet rechtstreeks aanroepen."""
    return _battery_sizing_raw(
        prices_df,
        battery_sizes_kwh=list(battery_sizes_kwh),
        max_power_kw=max_power_kw, charge_power_kw=charge_power_kw,
        min_soc=min_soc, initial_soc=initial_soc,
        capex_per_kwh=capex_per_kwh, lifespan_years=lifespan_years,
    )

def battery_sizing_analysis(prices_df, battery_sizes_kwh=None,
                              max_power_kw=5.0, charge_power_kw=None,
                              min_soc=0.10, min_end_soc=0.20, initial_soc=0.50,
                              capex_per_kwh=500.0, lifespan_years=12.0, **_):
    """
    Gecachede wrapper — identieke signature als de raw functie.
    Cache-key = hash van data + parameters. Geen aanpassing nodig aan call sites.
    Tweede aanroep met zelfde data en params: onmiddellijk resultaat.
    """
    data_hash = (f"{len(prices_df)}_"
                 f"{prices_df['price_eur_mwh'].sum():.2f}_"
                 f"{max_power_kw}_{charge_power_kw}_{capex_per_kwh}_{lifespan_years}_"
                 f"{sorted(battery_sizes_kwh or [])}")
    return _battery_sizing_cached(
        data_hash, prices_df,
        tuple(sorted(battery_sizes_kwh or [])),
        max_power_kw, charge_power_kw,
        min_soc, initial_soc, capex_per_kwh, lifespan_years,
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
    """
    Huidige tijd in Europe/Brussels (CET=UTC+1 winter, CEST=UTC+2 zomer).
    Streamlit Cloud en veel servers draaien op UTC — altijd UTC+offset berekenen.
    """
    try:
        # Beste aanpak: gebruik zoneinfo (Python 3.9+) of pytz
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo("Europe/Brussels")).replace(tzinfo=None)
        except ImportError:
            import pytz
            return datetime.now(pytz.timezone("Europe/Brussels")).replace(tzinfo=None)
    except Exception:
        # Noodoplossing: UTC + 2u (CEST zomer), werkt voor België april–oktober
        from datetime import timezone, timedelta
        utc_now = datetime.now(timezone.utc)
        # Eenvoudige DST-schatting: CEST (UTC+2) van laatste zondag maart t/m laatste zondag okt
        month = utc_now.month
        offset = 2 if 3 < month < 11 else (
                 2 if month == 3 and utc_now.day >= 25 else (
                 1 if month == 10 and utc_now.day < 25 else 1))
        return (utc_now + timedelta(hours=offset)).replace(tzinfo=None)

def day_ahead_published() -> bool:
    """Day-ahead prices for D+1 are published around 12:30-13:00 CET."""
    return now_cet().hour >= 13

def _set_period(start: date, end: date):
    """Central helper to update period session state and clear stale MILP."""
    st.session_state.date_start    = start
    st.session_state.date_end      = end
    # Niet st.session_state["date_range_picker"] instellen —
    # nieuwere Streamlit versies laten dit niet toe na widget-render.
    # De date_input gebruikt value= ipv key= voor initialisatie.
    st.session_state.milp_schedule = None
    st.session_state.milp_summary  = None
    st.session_state.milp_pending  = False


def _safe_float(val) -> float:
    """Haal numeriek deel op uit strings zoals '13.32 (+5.20€ gepland morgen...)'."""
    try:
        return float(str(val).split(" ")[0])
    except (ValueError, TypeError):
        return 0.0


def _to_belgian_csv(df: pd.DataFrame) -> bytes:
    """
    Exporteer DataFrame als Belgisch/Nederlands CSV:
      - Decimaalteken    : komma  (3,14 i.p.v. 3.14)
      - Lijstscheidingsteken: puntkomma (kolom1;kolom2)
      - Datum formaat    : DD/MM/YYYY HH:MM
      - Emoji in Type-kolom vervangen door leesbare tekst
    Direct te openen in Excel (BE/NL regio-instellingen).
    """
    export = df.copy()

    # Emoji → leesbare tekst
    if "Type" in export.columns:
        export["Type"] = export["Type"].str.replace("🟢 ", "", regex=False)\
                                       .str.replace("🔴 ", "", regex=False)\
                                       .str.replace("⚪", "Nul", regex=False)

    # Datum naar Belgisch formaat
    if "Tijd" in export.columns:
        export["Tijd"] = pd.to_datetime(export["Tijd"], utc=True, errors="coerce")\
                           .dt.tz_convert("Europe/Brussels")\
                           .dt.strftime("%d/%m/%Y %H:%M")

    # Floats: punt → komma
    for col in export.select_dtypes(include="number").columns:
        export[col] = export[col].apply(
            lambda v: f"{v:.4f}".replace(".", ",") if pd.notna(v) else ""
        )

    return export.to_csv(
        index=False,
        sep=";",
        encoding="utf-8-sig",   # BOM zodat Excel direct UTF-8 herkent
    ).encode("utf-8-sig")


# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="EMS Belgium MVP", layout="wide", page_icon="⚡")

# ─────────────────────────────────────────────────────────────────────────────
# Secrets: eenmalig laden bij startup → daarna enkel via session_state
# Correcte aanpak voor Streamlit Cloud: nooit st.secrets inline in widgets
# ─────────────────────────────────────────────────────────────────────────────
def _load_secret(key: str) -> str:
    """
    Laad een secret veilig — werkt zowel lokaal (.streamlit/secrets.toml)
    als op Streamlit Cloud. Geeft lege string terug als key ontbreekt
    of als de waarde nog een placeholder is.
    """
    # Bekende placeholders die we moeten negeren
    _PLACEHOLDERS = {
        "your-entsoe-api-key-here",
        "your-em-api-key-here",
        "paste-your-key-here",
        "YOUR_API_KEY_HERE",
        "PASTE_YOUR_ELECTRICITY_MAPS_API_KEY_HERE",
        "xxx",
        "your_key_here",
    }
    try:
        val = st.secrets.get(key, "")
        val = val if isinstance(val, str) else ""
    except Exception:
        try:
            val = str(st.secrets[key])
        except Exception:
            return ""

    # Negeer placeholders en te korte waarden (echte keys zijn altijd ≥ 10 tekens)
    if not val or val.strip().lower() in {p.lower() for p in _PLACEHOLDERS}:
        return ""
    if len(val.strip()) < 10:
        return ""
    return val.strip()

# Laad alle API keys eenmalig in session_state (nooit overschrijven als al ingevuld)
for _key in ("entsoe_key", "em_key"):
    if _key not in st.session_state or not st.session_state[_key]:
        _secret_val = _load_secret(_key)
        if _secret_val:
            st.session_state[_key] = _secret_val

st.title("⚡ EMS Belgium — Battery & Grid Intelligence Dashboard")
st.markdown(
    "**MVP Prototype** | Belgische day-ahead prijzen | "
    "Smart arbitrage + gratis laden bij negatieve prijzen | Grid balancing"
)

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar — Battery & Strategy Parameters
# ─────────────────────────────────────────────────────────────────────────────
st.sidebar.header("🔋 Batterij & Strategie")
battery_kwh      = st.sidebar.slider("Capaciteit (kWh)", 5.0, 100.0, 10.0, 0.5)

# ── Aansluitingstype ──────────────────────────────────────────────────────────
aansluiting = st.sidebar.radio(
    "Aansluitingstype",
    ["⚡ Monofase (1×230V)", "⚡⚡⚡ Driefasig (3×230V)"],
    index=0,
    help=(
        "Bepaalt de wettelijke vermogensgrenzen van de netaansluiting:\n\n"
        "**Monofase**: max 9,2 kW afname van het net, max 5 kW injectie op het net.\n\n"
        "**Driefasig**: max 15,9 kW afname van het net, max 10 kW injectie op het net.\n\n"
        "Let op: afname en injectie hebben verschillende limieten!"
    ),
)
is_driefasig = "Driefasig" in aansluiting
# Correcte wettelijke limieten (Fluvius/Synergrid)
max_afname_kw    = 15.9 if is_driefasig else 9.2    # max laden VAN net
max_inj_kw       = 10.0 if is_driefasig else 5.0    # max injecteren OP net
max_cap_kw       = max_afname_kw                     # laadlimiet = afname-limiet

_opt_kwh = round(min(max_afname_kw, max_inj_kw / (0.92**0.5)) * 4 * (0.92**0.5), 0)
if is_driefasig:
    st.sidebar.success(
        f"⚡⚡⚡ **Driefasig** — afname max {max_afname_kw} kW | injectie max {max_inj_kw:.0f} kW. "
        f"Technisch optimale batterij: ~{_opt_kwh:.0f} kWh."
    )
else:
    st.sidebar.info(
        f"⚡ **Monofase** — afname max {max_afname_kw} kW | injectie max {max_inj_kw:.0f} kW. "
        f"Technisch optimale batterij: ~{_opt_kwh:.0f} kWh."
    )

# Asymmetrisch vermogen — standaard ingesteld op aansluitingslimiet
discharge_power_kw = st.sidebar.slider(
    "Max injectievermogen (kW)",
    0.5, max_inj_kw, min(max_inj_kw, 5.0), 0.5,
    help=(
        f"Maximaal vermogen bij injectie OP het net. "
        f"Wettelijke limiet: {max_inj_kw:.0f} kW ({'driefasig' if is_driefasig else 'monofase'}). "
        "Geen capaciteitstarief op injectie."
    ),
)
charge_power_kw = st.sidebar.slider(
    "Max laadvermogen — rule-based (kW)", 0.5, max_afname_kw, 2.5, 0.5,
    help=(
        "Maximaal afnamevermogen voor de RULE-BASED simulatie.\n\n"
        f"Wettelijke afname-limiet: {max_afname_kw} kW. "
        "De MILP optimaliseert het laadvermogen zelf tussen 2.5 kW en "
        f"{max_afname_kw} kW op basis van capaciteitstarief vs. arbitrage-opbrengst.\n\n"
        "Deze slider heeft géén invloed op de MILP berekeningen."
    ),
)
# MILP: laden tot max afname-limiet, ontladen tot injectie-limiet
milp_charge_upper_kw = max_afname_kw   # afname-limiet (HOGER dan injectie!)
# Backwards-compat
max_power_kw = discharge_power_kw

# Capaciteitstarief berekening — gebaseerd op AFNAME-limiet (niet injectie)
cap_peak_kw    = max(2.5, charge_power_kw)  # rule-based piek
cap_monthly    = cap_peak_kw * 60 / 12       # €/maand bij deze piek
cap_forfait    = 2.5 * 60 / 12               # €12.50/maand minimumforfait
cap_extra      = cap_monthly - cap_forfait   # extra boven forfait

st.sidebar.info(
    f"⚡ **Rule-based cap.tarief**: {cap_peak_kw:.1f} kW piek "
    f"→ **{cap_monthly:.2f} €/mnd**"
    + (f" (+{cap_extra:.2f} € vs forfait)" if cap_extra > 0.01 else " (= forfait minimum)")
    + "\n\n_MILP kiest zijn eigen optimale piek (2.5–5 kW)._"
)

charge_thresh    = st.sidebar.slider("Laden onder (€/MWh)", 0, 80, 50)
discharge_thresh = st.sidebar.slider("Ontladen boven (€/MWh)", 100, 250, 160)
negative_boost   = st.sidebar.checkbox("Agressief laden bij negatieve prijs", value=True)
min_soc_pct      = st.sidebar.slider("Min SOC reserve (%)", 0, 30, 10, 1)
min_end_soc_pct  = st.sidebar.slider("Min End-SOC (%)", 10, 50, 20, 5,
    help="Min SOC op het einde van de horizon. Bij multi-dag MILP is dit het einde van de laatste dag.")

# ── Batterij specs & validatie — direct onder de SOC sliders ─────────────────
ETA              = 0.92 ** 0.5
max_e_slot_ch    = charge_power_kw    * 0.25   # max kWh laden per slot
max_e_slot_dis   = discharge_power_kw * 0.25   # max kWh ontladen per slot
c_rate_ch        = (ETA * max_e_slot_ch  * 4) / battery_kwh
c_rate_dis       = (max_e_slot_dis / ETA * 4) / battery_kwh
usable_kwh       = battery_kwh * (1 - min_soc_pct / 100)
t_charge_min     = (usable_kwh / (ETA * max_e_slot_ch))  * 15
t_discharge_min  = (usable_kwh / (max_e_slot_dis / ETA)) * 15

with st.sidebar.expander("🔬 Batterij specs & validatie", expanded=True):
    s1, s2 = st.columns(2)
    s1.metric("Laden/slot (rule-based)", f"{max_e_slot_ch:.2f} kWh",
              help=f"Max kWh per 15 min bij {charge_power_kw} kW (rule-based slider). MILP kiest zelf optimaal tussen 2.5–{discharge_power_kw} kW.")
    s2.metric("Ontladen/slot", f"{max_e_slot_dis:.2f} kWh",
              help="Max kWh per 15 min naar het net (geen capaciteitstarief). Geldt voor rule-based én MILP.")
    s3, s4 = st.columns(2)
    s3.metric("Vol laden (rule-based)", f"{t_charge_min:.0f} min",
              help=f"Van {min_soc_pct}% naar 100% bij {charge_power_kw} kW (rule-based). MILP kan sneller laden als dat loont.")
    s4.metric("Vol ontladen", f"{t_discharge_min:.0f} min",
              help=f"Van 100% naar {min_soc_pct}% bij {discharge_power_kw} kW (geldt voor alle scenario's)")
    asym_milp = discharge_power_kw / 2.5  # MILP min piek
    asym_rb   = discharge_power_kw / charge_power_kw
    st.success(
        f"⚡ MILP: ontladen is tot **{asym_milp:.1f}× sneller** dan laden (bij forfait 2.5 kW) "
        f"| Rule-based: {asym_rb:.1f}×"
    )
    max_c = max(c_rate_ch, c_rate_dis)
    if max_c > 2.0:
        st.error(f"⚠️ C-rate = {max_c:.1f}C — ZEER HOOG.")
    elif max_c > 1.0:
        st.warning(f"⚠️ C-rate = {max_c:.1f}C — boven 1C.")
    else:
        st.success(f"✅ C-rate laden = {c_rate_ch:.2f}C | ontladen = {c_rate_dis:.2f}C")

st.sidebar.markdown("---")
st.sidebar.subheader("🚀 MILP Optimalisatie")

# ── Initiële SOC: 3 bronnen in volgorde van prioriteit ────────────────────────
# 1. Gisteren's MILP eindSOC (meest nauwkeurig — perfecte foresight op historische prijzen)
# 2. Vorige run's eindSOC (handig bij aaneengesloten periodes)
# 3. Manuele slider

@st.cache_data(ttl=3600, show_spinner=False)
def compute_yesterday_optimal_soc(
    prices_parquet_hash: str,  # cache-key: verandert als data wijzigt
    battery_kwh: float,
    max_power_kw: float,
    min_soc: float,
    min_end_soc: float,
) -> float | None:
    """
    Bereken de optimale eindSOC van gisteren via MILP (perfecte foresight).
    Wordt gecached zodat het slechts 1x per uur herberekend wordt.
    Geeft None terug als er geen gisterse data beschikbaar is.
    """
    try:
        from milp_optimizer import optimize_battery_schedule
        yesterday = date.today() - timedelta(days=1)
        df_all = st.session_state.get("df_prices", pd.DataFrame())
        if df_all.empty:
            return None
        df_yest = df_all[df_all["datetime"].dt.date == yesterday].copy()
        if len(df_yest) < 4:
            return None
        _, summ = optimize_battery_schedule(
            df_yest,
            battery_kwh=battery_kwh,
            max_power_kw=max_power_kw,
            min_soc=min_soc,
            min_end_soc=min_end_soc,
            initial_soc=0.50,          # neutraal startpunt voor gisteren
            time_horizon_hours=None,
        )
        return summ["final_soc_pct"] if summ["status"] == "Optimal" else None
    except Exception:
        return None

# Bepaal de beste start-SOC suggestie
yesterday_soc   = None
prev_final_soc  = (st.session_state.get("milp_summary") or {}).get("final_soc_pct")

# Bereken gisteren's optimale SOC (gecached, snel)
if not st.session_state.get("df_prices", pd.DataFrame()).empty:
    try:
        _hash = str(len(st.session_state.df_prices))  # simpele cache-key
        yesterday_soc = compute_yesterday_optimal_soc(
            _hash,
            battery_kwh, max_power_kw,
            min_soc_pct / 100, min_end_soc_pct / 100,
        )
    except Exception:
        yesterday_soc = None

# Toon de beschikbare SOC-bronnen
if yesterday_soc is not None:
    st.sidebar.success(
        f"📊 **Gisteren's optimale eindSOC: {yesterday_soc:.1f}%**\n\n"
        f"MILP berekende dit op basis van alle prijzen van gisteren "
        f"(perfecte foresight). Gebruik dit als startpunt voor vandaag."
    )
    default_initial = yesterday_soc / 100
    soc_source = f"Gisteren optimaal ({yesterday_soc:.1f}%)"
elif prev_final_soc is not None:
    st.sidebar.caption(f"💡 Vorige run eindigde op **{prev_final_soc:.1f}%** SOC")
    default_initial = prev_final_soc / 100
    soc_source = f"Vorige run ({prev_final_soc:.1f}%)"
else:
    default_initial = 0.50
    soc_source = "Standaard (50%)"

initial_soc_pct = st.sidebar.slider(
    "Start SOC (%)", 10, 100, int(default_initial * 100), 5,
    help=(
        f"Huidige bron: {soc_source}\n\n"
        "Volgorde van prioriteit:\n"
        "1. Gisteren's optimale eindSOC (MILP op historische prijzen)\n"
        "2. Vorige run's eindSOC\n"
        "3. Manuele waarde\n\n"
        "In productie: vervang door live BMS-uitlezing."
    )
)

own_kwp = st.sidebar.slider(
    "Eigen PV-vermogen (kWp)", 0.0, 20.0, 6.3, 0.1,
    help="Jouw zonnepanelen vermogen. 0 = geen eigen PV. Wordt gebruikt voor MILP+Solar scenario."
)

if st.sidebar.button("🔬 Vergelijk alle scenario's", type="primary", use_container_width=True,
                      help="Berekent alle 4 scenario's: Rule-based | MILP Basis | MILP+Day-ahead | MILP+Solar"):
    st.session_state.scenarios_pending = True
    st.session_state.milp_pending      = False
    st.session_state.scenarios         = {}
    st.session_state.milp_initial_soc  = initial_soc_pct / 100

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
            value=st.session_state.get("entsoe_key", ""),
            placeholder="Plak hier je ENTSO-E API key…",
            key="entsoe_key_input",
        )
        if entsoe_key and len(entsoe_key.strip()) >= 10:
            st.session_state.entsoe_key = entsoe_key.strip()
            st.caption("✅ API key ingevuld")
        elif entsoe_key:
            st.warning("Key lijkt te kort — controleer of je de volledige key geplakt hebt.")
        elif not st.session_state.get("entsoe_key"):
            st.caption("⚠️ Geen key — ophaalknoppen werken niet tot je een key invult.")

        # Preset fetch buttons
        c1, c2, c3, c4, c5 = st.columns(5)
        fetch_days = None
        if c1.button("7 d",   use_container_width=True): fetch_days = 7
        if c2.button("30 d",  use_container_width=True): fetch_days = 30
        if c3.button("90 d",  use_container_width=True): fetch_days = 90
        if c4.button("180 d", use_container_width=True): fetch_days = 180
        if c5.button("365 d", use_container_width=True): fetch_days = 365

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
            value=st.session_state.get("em_key", ""),
            placeholder="Plak hier je Electricity Maps API key…",
            key="em_key_input",
        )
        if em_key and len(em_key.strip()) >= 10:
            st.session_state.em_key = em_key.strip()
            st.caption("✅ API key ingevuld")
        elif em_key:
            st.warning("Key lijkt te kort — controleer de volledige key.")
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

_ss_defaults = {
    "milp_pending":       False,
    "scenarios_pending":  False,
    "milp_schedule":      None,
    "milp_summary":       None,
    "milp_initial_soc":   0.50,
    "scenarios":          {},
    "scenario_errors":    {},
}
for _k, _v in _ss_defaults.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

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
    value=(st.session_state.date_start, st.session_state.date_end),
    min_value=min_date,
    max_value=max(max_date, tomorrow),
)
if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
    if date_range[0] != st.session_state.date_start or date_range[1] != st.session_state.date_end:
        _set_period(date_range[0], date_range[1])

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
        _auto_key = st.session_state.get("entsoe_key", "")
        if not _auto_key:
            st.warning("ENTSO-E API key niet gevonden. Vul die in via de sidebar.")
        else:
            client = EntsoeClient(_auto_key)
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
                max_power_kw=discharge_power_kw,         # injectie-limiet
                charge_power_kw=milp_charge_upper_kw,    # afname-limiet
                min_soc=min_soc_pct / 100,
                min_end_soc=min_end_soc_pct / 100,
                initial_soc=st.session_state.get("milp_initial_soc", 0.50),
                time_horizon_hours=None,
                execute_until=sel_end,
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
        battery_kwh=battery_kwh,
        max_power_kw=discharge_power_kw,         # injectie-limiet (5 of 10 kW)
        charge_power_kw=milp_charge_upper_kw,    # afname-limiet (9.2 of 15.9 kW)
        min_soc=min_soc_pct / 100,
        min_end_soc=min_end_soc_pct / 100,
        initial_soc=init_soc,
        time_horizon_hours=None,
    )

    # ── Bouw day-ahead input ───────────────────────────────────────────────
    da_pub         = day_ahead_published()
    tomorrow_data  = df[df["datetime"].dt.date == tomorrow]
    tomorrow_avail = not tomorrow_data.empty

    # Probeer automatisch dag-ahead op te halen als nog niet in dataset
    if da_pub and not tomorrow_avail and st.session_state.get("entsoe_key"):
        try:
            with st.spinner(f"Day-ahead voor {fdate(tomorrow)} ophalen voor MILP+DA scenario…"):
                _da_client = EntsoeClient(st.session_state.entsoe_key)
                _da_df = _da_client.get_day_ahead_prices(tomorrow, tomorrow + timedelta(days=1))
                if not _da_df.empty:
                    _merge_prices(_da_df)
                    df = st.session_state.df_prices
                    tomorrow_data  = df[df["datetime"].dt.date == tomorrow]
                    tomorrow_avail = not tomorrow_data.empty
        except Exception:
            pass

    if da_pub and tomorrow_avail and sel_end >= today:
        milp_da_input = pd.concat([sim_df, tomorrow_data]).drop_duplicates(
            "datetime").sort_values("datetime").reset_index(drop=True)
        da_label    = f"{fdate(tomorrow)} day-ahead beschikbaar"
        da_slots    = len(tomorrow_data)
        da_extended = True
    else:
        milp_da_input = sim_df.copy()
        da_slots      = 0
        da_extended   = False
        if not da_pub:
            da_label = "day-ahead nog niet gepubliceerd (vóór 13:00 CET)"
        elif not tomorrow_avail:
            da_label = f"{fdate(tomorrow)} niet in dataset — vul ENTSO-E key in voor auto-fetch"
        else:
            da_label = "periode eindigt voor vandaag"

    # ── Transparantie header ───────────────────────────────────────────────
    st.info(
        f"**Scenario inputs:**\n"
        f"- Scenario 1 (Rule-based): {len(sim_df)} slots ({fdate(sel_start)}→{fdate(sel_end)})\n"
        f"- Scenario 2 (MILP Basis): zelfde {len(sim_df)} slots\n"
        f"- Scenario 3 (MILP+DA): {len(milp_da_input)} slots "
        + (f"(**+{da_slots} slots {fdate(tomorrow)}**)" if da_extended
           else f"(⚠️ **zelfde als basis — {da_label}**)") + "\n"
        f"- Scenario 4 (MILP+Solar): zelfde als scenario 3 + eigen PV ({own_kwp} kWp)"
    )

    if not da_extended:
        st.warning(
            f"⚠️ **Scenario 2 (MILP Basis) en Scenario 3 (MILP+DA) zijn identiek** "
            f"omdat {da_label}.\n\n"
            f"Voor een zinvolle vergelijking: selecteer **meerdere dagen** (bv. 'Deze Week') "
            f"of wacht tot na 13:00 CET en laad morgen's prices via ENTSO-E."
        )

    prog = st.progress(0, text="Scenario's voorbereiden…")
    scenarios = {}
    errors    = {}

    def _run_milp(prices, label, execute_until, **kwargs):
        """Roep optimizer aan; val terug op versie zonder charge_power_kw bij TypeError."""
        try:
            return optimize_battery_schedule(
                prices, label=label, execute_until=execute_until, **kwargs)
        except TypeError as te:
            if "charge_power_kw" in str(te) or "unexpected keyword" in str(te):
                # Oudere optimizer versie — roep zonder nieuwe parameters aan
                safe_kwargs = {k: v for k, v in kwargs.items()
                               if k not in ("charge_power_kw", "cap_eur_per_kw_year", "cap_min_kw")}
                return optimize_battery_schedule(
                    prices, label=label + " (compat)", execute_until=execute_until, **safe_kwargs)
            raise

    try:
        prog.progress(10, text="▶ Scenario 1/5: MILP Basis (geselecteerde periode)…")
        sch1, s1 = _run_milp(sim_df, label="MILP Basis",
                              execute_until=sel_end, **milp_args)
        scenarios["milp_basic"] = (sch1, s1)
        prog.progress(30, text="▶ Scenario 2/5: MILP + Day-ahead…")
    except Exception as e:
        errors["milp_basic"] = str(e)
        prog.progress(30)

    try:
        sch2, s2 = _run_milp(milp_da_input, label=f"MILP+DA ({da_label})",
                              execute_until=sel_end, **milp_args)
        scenarios["milp_da"] = (sch2, s2)
        prog.progress(55, text="▶ Scenario 3/5: MILP + Day-ahead + Solar…")
    except Exception as e:
        errors["milp_da"] = str(e)
        prog.progress(55)

    if own_kwp > 0:
        try:
            solar_kwh = pd.Series(dtype=float)
            if ELIA_AVAILABLE:
                try:
                    ec     = EliaClient()
                    df_sol = ec.get_solar_forecast()
                    if df_sol.empty:
                        df_sol = ec.get_historical_solar(sel_start, sel_end + timedelta(days=1))
                    if not df_sol.empty:
                        solar_kwh = estimate_own_solar_kwh(df_sol, own_kwp=own_kwp)
                except Exception:
                    solar_kwh = pd.Series(dtype=float)

            solar_loaded = not solar_kwh.empty and solar_kwh.sum() > 0
            solar_label  = f"MILP+DA+Solar ({own_kwp}kWp)" if solar_loaded else "MILP+DA+Solar (geen solar data)"
            try:
                sch3, s3 = optimize_battery_schedule_solar(
                    milp_da_input, solar_kwh, label=solar_label,
                    execute_until=sel_end, **milp_args)
            except TypeError as te:
                if "charge_power_kw" in str(te) or "unexpected keyword" in str(te):
                    safe_kwargs = {k: v for k, v in milp_args.items()
                                   if k not in ("charge_power_kw", "cap_eur_per_kw_year", "cap_min_kw")}
                    sch3, s3 = optimize_battery_schedule_solar(
                        milp_da_input, solar_kwh, label=solar_label + " (compat)",
                        execute_until=sel_end, **safe_kwargs)
                else:
                    raise
            s3["solar_own_kwp"]     = own_kwp
            s3["solar_data_loaded"] = solar_loaded
            scenarios["milp_solar"] = (sch3, s3)
            prog.progress(80, text="▶ Scenario 4/5 klaar. Scenario 5/5: Wind + Solar…")
        except Exception as e:
            errors["milp_solar"] = str(e)
    else:
        prog.progress(90)

    # ── Scenario 5: MILP + DA + Solar + Wind forecast ─────────────────────
    if own_kwp > 0 and ELIA_AVAILABLE:
        try:
            prog.progress(90, text="▶ Scenario 5/5: MILP + DA + Solar + Wind forecast 🌬️☀️…")
            # Hergebruik solar_kwh van scenario 4 (al opgehaald)
            solar_kwh_wind = solar_kwh if 'solar_kwh' in dir() else pd.Series(dtype=float)

            # Wind prijs-aanpassing ophalen
            wind_price_adj = pd.Series(dtype=float)
            try:
                ec_wind   = EliaClient()
                surplus_df= ec_wind.get_renewable_surplus_forecast()
                if not surplus_df.empty and "price_adjustment_eur_mwh" in surplus_df.columns:
                    wind_price_adj = pd.Series(
                        surplus_df["price_adjustment_eur_mwh"].values,
                        index=pd.DatetimeIndex(surplus_df["datetime"]),
                        name="wind_price_adj",
                    )
            except Exception as wind_e:
                errors["wind_fetch"] = str(wind_e)

            wind_loaded  = not wind_price_adj.empty and (wind_price_adj != 0).any()
            solar_loaded5= not solar_kwh_wind.empty and solar_kwh_wind.sum() > 0
            wind_label   = (
                f"MILP+DA+Solar+Wind ({own_kwp}kWp, wind adj actief)"
                if wind_loaded else
                f"MILP+DA+Solar+Wind ({own_kwp}kWp, geen winddata → zelfde als Solar)"
            )

            sch5, s5 = optimize_battery_schedule_wind_solar(
                milp_da_input,
                solar_kwh_wind,
                wind_price_adj,
                label=wind_label,
                execute_until=sel_end,
                **milp_args,
            )
            s5["solar_own_kwp"]      = own_kwp
            s5["solar_data_loaded"]  = solar_loaded5
            s5["wind_data_loaded"]   = wind_loaded
            scenarios["milp_wind_solar"] = (sch5, s5)
        except Exception as e:
            errors["milp_wind_solar"] = str(e)
    else:
        if own_kwp == 0:
            errors["milp_wind_solar"] = "Eigen PV-vermogen = 0 kWp — stel in via sidebar"

    prog.progress(100, text="✅ Alle 5 scenario's berekend!")

    st.session_state.scenarios         = scenarios
    st.session_state.scenarios_pending = False
    st.session_state.scenario_errors   = errors

    if "milp_da" in scenarios:
        sch_da, summ_da = scenarios["milp_da"]
        st.session_state.milp_schedule = sch_da
        st.session_state.milp_summary  = summ_da

    st.rerun()




# ─────────────────────────────────────────────────────────────────────────────
# Rule-based simulation
# ─────────────────────────────────────────────────────────────────────────────
def quick_simulate(data, cap_kwh, pwr_kw, ch_thresh, dis_thresh,
                   neg_boost, min_soc=0.10, init_soc=0.50,
                   charge_pwr_kw: float | None = None,
                   cap_eur_per_kw_year: float = 60.0,
                   cap_min_kw: float = 2.5):
    """
    Rule-based batterijsimulatie — zelfde efficiency als MILP (eta = sqrt(0.92)).

    Capaciteitstarief (Fluvius):
      Gebaseerd op de hoogste netto-afname (kW) van het net in een 15-min kwartier.
      Minimum forfait: 2.5 kW → €12.50/maand.
      Kost = max(2.5 kW, piekvraag) × €60/jaar × (n_dagen/365).

    Parameters:
      charge_pwr_kw       : max laadvermogen (afname). Standaard = pwr_kw.
      cap_eur_per_kw_year : capaciteitstarief (default €60/kW/jaar).
      cap_min_kw          : minimumforfait (default 2.5 kW).
    """
    eta          = 0.92 ** 0.5
    soc          = init_soc
    charge_kw    = charge_pwr_kw if charge_pwr_kw is not None else pwr_kw
    max_e_ch     = (charge_kw * 0.25) / 1000   # MWh/slot laden (AC-netgrens)
    max_e_dis    = (pwr_kw    * 0.25) / 1000   # MWh/slot ontladen
    cap_mwh      = cap_kwh / 1000
    results      = []
    cum_rev      = 0.0
    peak_kw_seen = 0.0  # bijhouden: hoogste afnamevermogen (kW) van het net

    for _, row in data.iterrows():
        p = row["price_eur_mwh"]; action = "HOLD"; e_mwh = 0.0; rev = 0.0
        if p < 0 and neg_boost:
            e = min(max_e_ch, (1 - soc) * cap_mwh / eta)
            if e > 0.0001:
                e_mwh = e; soc += e_mwh * eta / cap_mwh
                rev = -e_mwh * p; action = "CHARGE (NEG)"
        elif p < ch_thresh:
            e = min(max_e_ch, (1 - soc) * cap_mwh / eta)
            if e > 0.0001:
                e_mwh = e; soc += e_mwh * eta / cap_mwh
                rev = -e_mwh * p; action = "CHARGE"
        elif p > dis_thresh:
            avail_ac = min(max_e_dis, (soc - min_soc) * cap_mwh * eta)
            if avail_ac > 0.0001:
                e_mwh = avail_ac
                soc  -= e_mwh / (eta * cap_mwh)
                rev   = e_mwh * p; action = "DISCHARGE"

        # Bijhouden van de piek-afname (enkel bij laden van net)
        if "CHARGE" in action:
            slot_kw = (e_mwh * 1000) / 0.25   # kWh → kW (per uur)
            peak_kw_seen = max(peak_kw_seen, slot_kw)

        cum_rev += rev
        results.append({"datetime": row["datetime"], "price": p, "action": action,
                         "energy_kwh": e_mwh * 1000, "revenue": rev,
                         "soc": soc * 100, "cum_rev": cum_rev})


    df_result = pd.DataFrame(results)
    # Geen attrs — cap tarief wordt buiten de functie berekend op basis van
    # charge_power_kw slider, zodat het altijd correct en transparant is.
    return df_result

sim = quick_simulate(sim_df, battery_kwh, max_power_kw, charge_thresh,
                     discharge_thresh, negative_boost, min_soc_pct / 100,
                     initial_soc_pct / 100,
                     charge_pwr_kw=charge_power_kw)

# Capaciteitstarief rule-based: direct berekend uit de slider charge_power_kw
# Geen pandas attrs — volledig transparant en betrouwbaar
rb_n_months  = len(sim_df) * 0.25 / 24.0 / 30.44
rb_peak_kw   = max(2.5, charge_power_kw)        # slider = rule-based laadlimiet
rb_cap_cost  = rb_peak_kw * (60.0 / 12.0) * rb_n_months
rb_cap_mnd   = rb_peak_kw * 60.0 / 12.0         # €/maand bij deze piek
rb_gross_rev = sim["cum_rev"].iloc[-1] if not sim.empty else 0.0
rb_net_rev   = rb_gross_rev - rb_cap_cost
# cum_rev_after_cap toevoegen aan sim
if not sim.empty:
    sim["cum_rev_after_cap"] = sim["cum_rev"] - rb_cap_cost

milp_df    = st.session_state.get("milp_schedule")
milp_summ  = st.session_state.get("milp_summary") or {}
milp_ready = milp_df is not None and bool(milp_summ)

# ─────────────────────────────────────────────────────────────────────────────
# KPI row
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("🔋 Simulatie Resultaten")
m1, m2, m3, m4 = st.columns(4)
m1.metric(
    "Net Revenue (Rule-based)",
    f"{rb_net_rev:.2f} €",
    delta=f"bruto {rb_gross_rev:.2f} € − cap {rb_cap_cost:.2f} €",
    delta_color="off",
    help=f"Na aftrek capaciteitstarief: piek {rb_peak_kw:.1f} kW → {rb_cap_mnd:.2f} €/mnd"
)
m2.metric("Totaal geladen", f"{sim['energy_kwh'].sum():.1f} kWh")
m3.metric("Gem. SOC", f"{sim['soc'].mean():.1f} %")
if milp_ready:
    m4.metric("Net Revenue (MILP)",
              f"{milp_summ['total_net_revenue_eur']:.2f} €",
              delta=f"{milp_summ['total_net_revenue_eur'] - rb_net_rev:+.2f} € vs Rule-based")
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

    # Financial breakdown — enkel execute-slots (niet lookahead)
    st.markdown("#### 💰 Financieel Overzicht")
    exec_df_fin = milp_df[~milp_df["is_lookahead"]] if "is_lookahead" in milp_df.columns \
                  else milp_df[milp_df["datetime"].dt.date <= sel_end]
    pos_ch  = exec_df_fin[(exec_df_fin["charge_kwh"] > 0) & (exec_df_fin["price_eur_mwh"] > 0)]
    neg_ch  = exec_df_fin[(exec_df_fin["charge_kwh"] > 0) & (exec_df_fin["price_eur_mwh"] <= 0)]
    dis_df  = exec_df_fin[exec_df_fin["discharge_kwh"] > 0]
    cost_p  = abs(pos_ch["net_revenue_eur"].sum())
    inc_neg = abs(neg_ch["net_revenue_eur"].sum())
    inc_dis = dis_df["net_revenue_eur"].sum()
    net_rev      = milp_summ.get("revenue_execute_eur", milp_summ.get("total_net_revenue_eur", 0))
    net_after_cap= milp_summ.get("revenue_after_cap_eur", net_rev)
    cap_cost     = milp_summ.get("cap_tarief_period_eur", 0)
    peak_kw      = milp_summ.get("peak_charge_kw", charge_power_kw)
    cap_monthly  = milp_summ.get("cap_tarief_monthly_eur", peak_kw * 60 / 12)

    f1, f2, f3, f4 = st.columns(4)
    f1.metric("Ontlaad-inkomsten",   f"+{inc_dis:.2f} €",
              help="Verdiend door ontladen bij hoge prijs (5 kW injectie)")
    f2.metric("Inkomsten (p≤0)",     f"+{inc_neg:.2f} €",
              help="Ontvangen voor laden bij negatieve/nulprijs")
    f3.metric("Kosten (laden, p>0)", f"-{cost_p:.2f} €",
              delta_color="inverse",
              help="Betaald voor laden van het net bij positieve prijs")
    f4.metric("Capaciteitstarief",   f"-{cap_cost:.2f} €",
              delta_color="inverse",
              help=f"MILP koos piek {peak_kw:.2f} kW → {cap_monthly:.2f} €/mnd equivalent")

    # Net revenue rij
    rev_col1, rev_col2 = st.columns(2)
    rev_col1.metric(
        "Net Revenue (voor cap.tarief)",
        f"{net_rev:.2f} €",
        delta=f"{net_rev - rb_net_rev:+.2f} vs Rule-based"
    )
    rev_col2.metric(
        "Net Revenue (na cap.tarief)",
        f"{net_after_cap:.2f} €",
        delta=f"-{cap_cost:.2f} € cap.tarief ({peak_kw:.1f} kW piek)",
        delta_color="inverse",
        help=f"MILP koos laadpiek = {peak_kw:.2f} kW (max toegestaan: {charge_power_kw} kW). "
             f"Ontlaadvermogen: {discharge_power_kw} kW. "
             f"Capaciteitstarief maandequivalent: {cap_monthly:.2f} €/mnd."
    )
    computed = inc_dis + inc_neg - cost_p - cap_cost
    if abs(computed - net_after_cap) > 0.05:
        st.caption(f"ℹ️ Check: {inc_dis:.2f} + {inc_neg:.2f} - {cost_p:.2f} - {cap_cost:.2f} "
                   f"= {computed:.2f} € (Δ={computed-net_after_cap:+.2f} €)")

    # Comparison table
    st.markdown("#### 📊 Vergelijking (geselecteerde periode)")
    milp_period = milp_df[milp_df["datetime"].dt.date <= sel_end]
    comp = pd.DataFrame({
        "Metric":     ["Net Revenue (€)", "Geladen (kWh)", "Ontladen (kWh)", "Eind SOC (%)"],
        "Rule-based": [round(rb_net_rev, 2),
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

    # ── MILP Optimale Acties tabel ────────────────────────────────────────────
    am  = (milp_df["charge_kwh"] > 0.01) | (milp_df["discharge_kwh"] > 0.01)
    dtl = milp_df[am][["datetime","price_eur_mwh","charge_kwh","discharge_kwh",
                        "charge_solar_kwh","net_revenue_eur","soc_pct","is_lookahead"]].copy()

    # Vermogen (kW) = kWh per 15-min slot × 4
    dtl["Laadvermogen (kW)"]    = (dtl["charge_kwh"] - dtl.get("charge_solar_kwh", 0)) * 4
    dtl["Injectievermogen (kW)"]= dtl["discharge_kwh"] * 4

    # Cumulatieve totalen
    dtl["Cum. geladen (kWh)"]   = dtl["charge_kwh"].cumsum()
    dtl["Cum. ontladen (kWh)"]  = dtl["discharge_kwh"].cumsum()

    # Revenue per kWh (voor ontladen)
    dtl["Rev/kWh (€)"] = dtl.apply(
        lambda r: r["net_revenue_eur"] / r["discharge_kwh"]
                  if r["discharge_kwh"] > 0.01 else (
                  r["net_revenue_eur"] / r["charge_kwh"]
                  if r["charge_kwh"] > 0.01 else 0), axis=1)

    # Type kolom
    dtl["Type"] = dtl.apply(lambda r:
        ("🔵 Solar laden" if r.get("charge_solar_kwh", 0) > 0.01 else
         "🟢 Inkomsten"   if r["net_revenue_eur"] > 0 else
         "🔴 Kosten")
        + (" ⏭ lookahead" if r.get("is_lookahead", False) else ""), axis=1)

    dtl = dtl.rename(columns={
        "datetime":       "Tijd",
        "price_eur_mwh":  "Prijs (€/MWh)",
        "charge_kwh":     "Geladen (kWh)",
        "discharge_kwh":  "Ontladen (kWh)",
        "net_revenue_eur":"Slot Rev (€)",
        "soc_pct":        "SOC (%)",
    })

    # Kolom volgorde
    col_order = ["Tijd","Type","Prijs (€/MWh)",
                 "Geladen (kWh)","Laadvermogen (kW)",
                 "Ontladen (kWh)","Injectievermogen (kW)",
                 "Rev/kWh (€)","Slot Rev (€)",
                 "SOC (%)","Cum. geladen (kWh)","Cum. ontladen (kWh)"]
    col_order = [c for c in col_order if c in dtl.columns]
    dtl = dtl[col_order].drop(columns=["is_lookahead","charge_solar_kwh"], errors="ignore")

    # Afronden voor leesbaarheid
    for col in dtl.select_dtypes(include="number").columns:
        dtl[col] = dtl[col].round(3)

    st.dataframe(dtl, use_container_width=True, hide_index=True, height=360,
                 column_config={
                     "Prijs (€/MWh)":        st.column_config.NumberColumn(format="%.2f"),
                     "Geladen (kWh)":         st.column_config.NumberColumn(format="%.3f"),
                     "Laadvermogen (kW)":     st.column_config.NumberColumn(format="%.2f"),
                     "Ontladen (kWh)":        st.column_config.NumberColumn(format="%.3f"),
                     "Injectievermogen (kW)": st.column_config.NumberColumn(format="%.2f"),
                     "Rev/kWh (€)":           st.column_config.NumberColumn(format="%.4f"),
                     "Slot Rev (€)":          st.column_config.NumberColumn(format="%.4f"),
                     "SOC (%)":               st.column_config.NumberColumn(format="%.1f"),
                     "Cum. geladen (kWh)":    st.column_config.NumberColumn(format="%.2f"),
                     "Cum. ontladen (kWh)":   st.column_config.NumberColumn(format="%.2f"),
                 })

    # Totaalrij
    exec_dtl = dtl[~dtl["Type"].str.contains("lookahead", na=False)]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Totaal geladen",  f"{exec_dtl['Geladen (kWh)'].sum():.2f} kWh")
    c2.metric("Totaal ontladen", f"{exec_dtl['Ontladen (kWh)'].sum():.2f} kWh")
    c3.metric("Gem. injectie",   f"{exec_dtl[exec_dtl['Ontladen (kWh)']>0]['Injectievermogen (kW)'].mean():.2f} kW")
    c4.metric("Gem. rev/kWh",    f"{exec_dtl[exec_dtl['Ontladen (kWh)']>0]['Rev/kWh (€)'].mean():.4f} €")

    # CSV export
    csv_be = _to_belgian_csv(dtl)
    st.download_button(
        label="📥 Download als CSV (Belgisch formaat voor Excel)",
        data=csv_be,
        file_name=f"milp_acties_{sel_start}_{sel_end}.csv",
        mime="text/csv",
        help="Decimaalteken = komma, scheidingsteken = puntkomma — direct te openen in Excel (BE/NL)"
    )

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

if st.session_state.get("scenarios") is not None and st.session_state.get("scenario_errors") is not None:
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
    rb_rev     = rb_net_rev  # netto na capaciteitstarief (niet sim["cum_rev"]!)
    rows       = []

    # Lookbehind: gisteren's optimale SOC als startpunt
    _yest_soc = st.session_state.get("milp_initial_soc", 0.50) * 100
    _soc_src  = "Gisteren optimaal" if yesterday_soc is not None else "Manueel/standaard"

    rb_active = int((sim["action"] != "HOLD").sum())
    rows.append({
        "Scenario":        "1️⃣ Rule-based",
        "Actieve slots":   rb_active,
        "Start SOC":       f"{_yest_soc:.0f}% ({_soc_src})",
        "Lookahead slots": 0,
        "Bruto Rev (€)":   f"{rb_gross_rev:.2f}",
        "Cap.tarief (€)":  f"-{rb_cap_cost:.2f}",
        "Netto Rev (€)":   f"{rb_rev:.2f}",
        "Geladen (kWh)":   f"{sim['energy_kwh'].sum():.1f}",
        "Ontladen (kWh)":  f"{sim[sim['action']=="DISCHARGE"]['energy_kwh'].sum():.1f}",
        "Eind SOC (%)":    f"{sim['soc'].iloc[-1]:.1f}",
        "Verbetering":     "—",
    })

    emoji = ["2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    keys  = ["milp_basic", "milp_da", "milp_solar", "milp_wind_solar"]
    names = ["MILP Basis", "MILP + Day-ahead", "MILP + DA + Solar ☀️", "MILP + DA + Solar + Wind 🌬️☀️"]

    for i, (key, name) in enumerate(zip(keys, names)):
        if key in scen:
            sch_k, s = scen[key]
            # Altijd netto revenue NA cap.tarief voor eerlijke vergelijking
            rev_gross = s.get("revenue_execute_eur", s.get("total_net_revenue_eur", 0))
            cap_cost_s= s.get("cap_tarief_period_eur", 0)
            rev       = s.get("revenue_after_cap_eur", s.get("total_net_revenue_eur", 0))
            n_lah     = s.get("num_slots_lookahead", 0)
            rev_lah   = s.get("revenue_lookahead_eur", 0)

            exec_sch = sch_k[~sch_k["is_lookahead"]] if "is_lookahead" in sch_k.columns \
                       else sch_k[sch_k["datetime"].dt.date <= sel_end]
            n_active = int(((exec_sch["charge_kwh"] > 0.01) |
                            (exec_sch["discharge_kwh"] > 0.01)).sum())

            lah_note = ""
            if n_lah > 0 and rev_lah != 0:
                end_soc = s.get("final_soc_pct", 0)
                lah_note = (f" (+{rev_lah:.2f}€ morgen, klaar op {end_soc:.0f}%)")

            rows.append({
                "Scenario":          f"{emoji[i]} {name}",
                "Actieve slots":     n_active,
                "Start SOC":         f"{_yest_soc:.0f}% ({_soc_src})",
                "Lookahead slots":   n_lah,
                "Bruto Rev (€)":     f"{rev_gross:.2f}",
                "Cap.tarief (€)":    f"-{cap_cost_s:.2f}",
                "Netto Rev (€)":     f"{rev:.2f}{lah_note}",
                "Geladen (kWh)":     f"{s['total_charged_kwh']:.1f}",
                "Ontladen (kWh)":    f"{s['total_discharged_kwh']:.1f}",
                "Eind SOC (%)":      f"{s['final_soc_pct']:.1f}",
                "Verbetering":       f"+{rev - rb_rev:.2f} €" if rev > rb_rev else f"{rev - rb_rev:.2f} €",
            })
        elif key in scen_errors:
            rows.append({
                "Scenario": f"{emoji[i]} {name}",
                "Actieve slots": "—", "Start SOC": "—", "Lookahead slots": "—",
                "Bruto Rev (€)": "—", "Cap.tarief (€)": "—",
                "Netto Rev (€)": f"❌ {scen_errors[key][:40]}",
                "Geladen (kWh)": "—", "Ontladen (kWh)": "—",
                "Eind SOC (%)": "—", "Verbetering": "—",
            })

    # Toon eventuele fouten prominent
    if scen_errors:
        st.error("⚠️ **Fouten bij berekening van scenario's:**")
        for key, err in scen_errors.items():
            st.code(f"{key}: {err}", language="text")

    comp_df = pd.DataFrame(rows)
    st.dataframe(comp_df, use_container_width=True, hide_index=True)

    has_lookahead = any(r.get("Lookahead slots", 0) not in (0, "—") for r in rows[1:])
    st.caption(
        "💡 **Netto Rev** = Bruto arbitrage-opbrengst minus capaciteitstarief. "
        "Alle scenarios berekenen het capaciteitstarief op basis van de werkelijke piek-afname. "
        "Toekomstige kosten (groene stroomcertificaten, nettarieven, ...) "
        "worden hier later ook in mindering gebracht. "
        + (f"**Lookbehind**: Start SOC = {_soc_src}. " if _soc_src else "")
        + ("**Lookahead**: morgen's prijzen beïnvloeden de eind-SOC keuze — trades worden pas morgen uitgevoerd."
           if has_lookahead else
           "Lookahead = 0 (day-ahead morgen nog niet beschikbaar — na 13:00 CET).")
    )

    fig_bar = go.Figure(go.Bar(
        x=[r["Scenario"] for r in rows],
        y=[_safe_float(r.get("Netto Rev (€)", r.get("Net Revenue (€)", "0"))) for r in rows],
        marker_color=["royalblue", "#E67E22", "#27AE60", "#8E44AD"][:len(rows)],
        text=[f"{_safe_float(r.get('Netto Rev (€)', r.get('Net Revenue (€)', '0'))):.2f} €" for r in rows],
        textposition="outside",
    ))
    fig_bar.update_layout(
        title="Net Revenue per Scenario — Execute periode (€)",
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
        ("milp_basic", "MILP Basis",          "dash"),
        ("milp_da",    "MILP + Day-ahead",    "dot"),
        ("milp_solar", "MILP + DA + Solar ☀️","dashdot"),
    ]:
        if key in scen:
            sch_k, _ = scen[key]
            color = COLORS[key][0]
            # Execute-periode: volle lijn
            exec_part = sch_k[~sch_k["is_lookahead"]] if "is_lookahead" in sch_k.columns \
                        else sch_k[sch_k["datetime"].dt.date <= sel_end]
            fig_soc_all.add_trace(go.Scatter(
                x=exec_part["datetime"], y=exec_part["soc_pct"],
                mode="lines", name=name,
                line=dict(color=color, width=2, dash=dash_style)))
            # Lookahead-periode: transparant, gestippeld
            lah_part = sch_k[sch_k["is_lookahead"]] if "is_lookahead" in sch_k.columns \
                       else pd.DataFrame()
            if not lah_part.empty:
                # Verbind execute met lookahead (geen gat in de lijn)
                bridge = exec_part.tail(1)
                lah_full = pd.concat([bridge, lah_part])
                fig_soc_all.add_trace(go.Scatter(
                    x=lah_full["datetime"], y=lah_full["soc_pct"],
                    mode="lines", name=f"{name} (lookahead morgen)",
                    line=dict(color=color, width=1.5, dash="dot"),
                    opacity=0.4, showlegend=False))

    fig_soc_all.add_hline(y=min_soc_pct, line_dash="dash", line_color="orange",
        annotation_text=f"Min {min_soc_pct}% reserve")
    fig_soc_all.update_yaxes(range=[0, 100], title="SOC (%)")
    fig_soc_all.update_layout(
        title="Battery State of Charge — Alle Scenario's (transparant = lookahead morgen)",
        xaxis_title="Tijd",
        legend=dict(orientation="h", y=-0.2))
    st.plotly_chart(fig_soc_all, use_container_width=True)

    # ── Cumulatieve revenue overlay ────────────────────────────────────────
    fig_rev_all = go.Figure()
    fig_rev_all.add_trace(go.Scatter(
        x=sim["datetime"], y=sim["cum_rev_after_cap"],
        mode="lines", name="Rule-based (na cap.tarief)",
        line=dict(color="royalblue", width=1.5, dash="dash")))

    for key, name, dash_style in [
        ("milp_basic", "MILP Basis",          "dash"),
        ("milp_da",    "MILP + Day-ahead",    "dot"),
        ("milp_solar", "MILP + DA + Solar ☀️","dashdot"),
    ]:
        if key in scen:
            sch_k, _ = scen[key]
            color = COLORS[key][0]
            exec_part = sch_k[~sch_k["is_lookahead"]].copy() if "is_lookahead" in sch_k.columns \
                        else sch_k[sch_k["datetime"].dt.date <= sel_end].copy()
            exec_part["cum_rev"] = exec_part["net_revenue_eur"].cumsum()
            fig_rev_all.add_trace(go.Scatter(
                x=exec_part["datetime"], y=exec_part["cum_rev"],
                mode="lines", name=name,
                line=dict(color=color, width=2, dash=dash_style)))
            # Lookahead
            lah_part = sch_k[sch_k["is_lookahead"]].copy() if "is_lookahead" in sch_k.columns \
                       else pd.DataFrame()
            if not lah_part.empty:
                bridge     = exec_part.tail(1).copy()
                lah_part["cum_rev"] = bridge["cum_rev"].iloc[0] + lah_part["net_revenue_eur"].cumsum()
                lah_full   = pd.concat([bridge[["datetime","cum_rev"]], lah_part[["datetime","cum_rev"]]])
                fig_rev_all.add_trace(go.Scatter(
                    x=lah_full["datetime"], y=lah_full["cum_rev"],
                    mode="lines", name=f"{name} (lookahead)",
                    line=dict(color=color, width=1.5, dash="dot"),
                    opacity=0.4, showlegend=False))

    fig_rev_all.update_layout(
        title="Cumulatieve Revenue — Execute periode (transparant = lookahead morgen)",
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
        tab_imb, tab_solar, tab_wind = st.tabs([
            "⚡ Imbalance Prijzen",
            "☀️ Solar PV Forecast",
            "🌬️ Wind + Hernieuwbaar Surplus",
        ])

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
                            hist_end = date.today()
                            hist_start = hist_end - timedelta(days=hist_days)
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

        # ── TAB 3: Wind + Hernieuwbaar Surplus ───────────────────────────────
        with tab_wind:
            st.markdown(
                "**Wind- en hernieuwbaar surplus forecast voor België** via Elia (ods086).\n\n"
                "Strategisch belang voor EMS:\n"
                "- **Hoog surplus** (wind + zon > 4000 MW) → verwacht **negatieve/lage prijzen** → plan **laden**\n"
                "- De prijs-correctie toont hoeveel €/MWh lager de prijs verwacht wordt t.o.v. het basisniveau\n"
                "- Scenario 5 (MILP+Solar+Wind) gebruikt deze correctie automatisch in de MILP-berekening"
            )

            wind_col1, wind_col2 = st.columns(2)

            with wind_col1:
                if st.button("🌬️ Wind forecast + Surplus (ods086)", key="btn_wind_now"):
                    with st.spinner("Elia wind forecast + surplus berekenen…"):
                        try:
                            ec         = EliaClient()
                            df_wind    = ec.get_wind_forecast()
                            surplus_df = ec.get_renewable_surplus_forecast()
                            advice     = ec.get_wind_solar_ems_advice()

                            st.info(f"💡 **EMS Advies:** {advice.get('advice', '—')}")

                            if not surplus_df.empty:
                                # Surplus grafiek met prijs-correctie
                                fig_w = go.Figure()

                                # Solar MW
                                if "solar_mw" in surplus_df.columns:
                                    fig_w.add_trace(go.Bar(
                                        x=surplus_df["datetime"],
                                        y=surplus_df["solar_mw"],
                                        name="Solar (MW)", marker_color="#FFA500",
                                        opacity=0.7))
                                # Wind MW (gestapeld op solar)
                                if "wind_mw" in surplus_df.columns:
                                    fig_w.add_trace(go.Bar(
                                        x=surplus_df["datetime"],
                                        y=surplus_df["wind_mw"],
                                        name="Wind (MW)", marker_color="#4CA3DD",
                                        opacity=0.7))
                                # Prijs-correctie op secundaire as
                                if "price_adjustment_eur_mwh" in surplus_df.columns:
                                    fig_w.add_trace(go.Scatter(
                                        x=surplus_df["datetime"],
                                        y=surplus_df["price_adjustment_eur_mwh"],
                                        name="Prijscorrectie (€/MWh)",
                                        line=dict(color="red", width=2, dash="dot"),
                                        yaxis="y2"))

                                fig_w.update_layout(
                                    title="Hernieuwbaar Surplus = Wind + Solar (MW) + Verwachte Prijscorrectie",
                                    xaxis_title="Tijd",
                                    yaxis=dict(title="Vermogen (MW)"),
                                    yaxis2=dict(
                                        title="Prijscorrectie (€/MWh)",
                                        overlaying="y", side="right",
                                        tickformat=".0f",
                                        color="red",
                                    ),
                                    barmode="stack",
                                    legend=dict(x=0, y=1.1, orientation="h"),
                                )
                                st.plotly_chart(fig_w, use_container_width=True)

                                # KPI metrics
                                tomorrow = date.today() + timedelta(days=1)
                                tm_df = surplus_df[surplus_df["datetime"].dt.date == tomorrow]
                                if not tm_df.empty:
                                    w1, w2, w3, w4 = st.columns(4)
                                    w1.metric("Piek surplus morgen",
                                              f"{tm_df['surplus_mw'].max():.0f} MW",
                                              help="Max wind + solar morgen")
                                    w2.metric("Max prijscorrectie",
                                              f"{tm_df['price_adjustment_eur_mwh'].min():.1f} €/MWh",
                                              delta_color="inverse",
                                              help="Negatief = lagere verwachte prijs door surplus")
                                    w3.metric("Laadkwartieren met >20€ surplus",
                                              f"{(tm_df['price_adjustment_eur_mwh'] < -20).sum()}",
                                              help="Kwartieren waarbij MILP proactief zal laden")
                                    w4.metric("Piek tijdstip",
                                              str(tm_df.loc[tm_df['surplus_mw'].idxmax(),
                                                            'datetime'])[:16][-5:])

                                    st.caption(
                                        "ℹ️ **Prijscorrectieformule**: elke 1000 MW surplus boven 2000 MW "
                                        "geeft een verwachte prijsdaling van -8 €/MWh. "
                                        "Bij 6000 MW totaal surplus = -32 €/MWh correctie. "
                                        "Scenario 5 (MILP+Solar+Wind) past deze correctie toe in de "
                                        "optimalisatie-objectieffunctie."
                                    )

                                with st.expander("📋 Ruwe surplus data", expanded=False):
                                    st.dataframe(surplus_df.round(1),
                                                 use_container_width=True, hide_index=True,
                                                 height=300)
                            else:
                                st.warning("Geen wind/solar surplus data beschikbaar.")

                            # Ruwe wind data
                            if not df_wind.empty:
                                with st.expander("📋 Ruwe wind forecast (ods086)", expanded=False):
                                    st.caption(f"Kolommen: {list(df_wind.columns)}")
                                    st.dataframe(df_wind.head(30),
                                                 use_container_width=True, hide_index=True)

                        except Exception as e:
                            st.error(f"Wind forecast fout: {e}")
                            st.caption("Tip: controleer of `elia-py` de `get_wind_power_estimation_and_forecast` "
                                       "methode ondersteunt (vereist elia-py ≥ 0.3.1)")

            with wind_col2:
                st.markdown("**Historische wind productie**")
                wind_hist_days = st.slider("Dagen terug", 1, 30, 7, key="wind_hist_days")
                if st.button("📅 Historische wind (ods086)", key="btn_wind_hist"):
                    with st.spinner("Historische wind ophalen…"):
                        try:
                            ec         = EliaClient()
                            hist_end   = date.today()
                            hist_start = hist_end - timedelta(days=wind_hist_days)
                            df_wh      = ec.get_historical_wind(hist_start, hist_end)

                            if not df_wh.empty:
                                st.success(f"✅ {len(df_wh)} rijen | {wind_hist_days} dagen")

                                plot_cols = [c for c in df_wh.columns
                                             if c not in ("datetime", "region")
                                             and df_wh[c].dtype in ("float64", "int64")]

                                if plot_cols and "datetime" in df_wh.columns:
                                    fig_wh = go.Figure()
                                    colors = ["#4CA3DD", "#1E90FF", "#87CEEB"]
                                    for i, col in enumerate(plot_cols[:3]):
                                        fig_wh.add_trace(go.Scatter(
                                            x=df_wh["datetime"], y=df_wh[col],
                                            mode="lines", name=col,
                                            line=dict(color=colors[i % len(colors)], width=1.5)))
                                    fig_wh.update_layout(
                                        title=f"Historische Wind Productie — {wind_hist_days} dagen",
                                        xaxis_title="Tijd", yaxis_title="MW")
                                    st.plotly_chart(fig_wh, use_container_width=True)

                                st.caption(
                                    "💡 Correleer windproductie met day-ahead prijzen: "
                                    "hoge wind 's nachts → soms negatieve prijzen. "
                                    "Hoge wind + zon overdag → sterkste prijsdaling."
                                )
                            else:
                                st.warning("Geen historische winddata.")
                        except Exception as e:
                            st.error(f"Wind historisch fout: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Battery Sizing Advisor
# ─────────────────────────────────────────────────────────────────────────────
with st.expander("🔋 Battery Sizing Advisor — Optimale batterijgrootte", expanded=False):
    st.markdown(
        "**Welke batterijgrootte geeft de beste rendabiliteit op jouw energieprofiel?**\n\n"
        "De MILP-optimizer draait voor meerdere batterijgroottes op jouw geladen prijsdata. "
        "Voor elke grootte berekent hij de optimale arbitrage-opbrengst, capaciteitstarief en "
        "kapitaalkost — zodat je de echte netto winst en terugverdientijd per grootte vergelijkt.\n\n"
        "⚠️ *Dit is een MILP-sweep en neemt enige rekentijd (±30 seconden voor 8 groottes).*"
    )

    sz_c1, sz_c2, sz_c3 = st.columns(3)
    with sz_c1:
        capex_kwh = st.number_input(
            "CAPEX batterij (€/kWh geïnstalleerd)", 10, 1000, 500, 10,
            help="Totale installatiekost per kWh capaciteit. DIY LFP-cellen: ±€80-150/kWh, professioneel geïnstalleerd: ±€400-600/kWh.")
        lifespan  = st.number_input(
            "Verwachte levensduur (jaar)", 5, 25, 12, 1,
            help="LFP-batterijen halen typisch 10-15 jaar bij 1 cyclus/dag.")
    with sz_c2:
        sizes_min = st.number_input("Min. capaciteit sweep (kWh)", 2, 20, 5, 1)
        sizes_max = st.number_input("Max. capaciteit sweep (kWh)", 5, 100, 30, 5)
        sizes_step= st.number_input("Stap (kWh)", 1, 10, 5, 1)
    with sz_c3:
        use_solar_sz  = st.checkbox("Solar meenemen in sweep", value=own_kwp > 0,
                                     help="Gebruikt eigen PV-vermogen van de sidebar")
        sz_injectie = st.number_input(
            "Injectievermogen sweep (kW)", 0.5, max_inj_kw, float(discharge_power_kw), 0.5,
            help=f"Max ontlaadvermogen naar net. Wettelijke limiet: {max_inj_kw:.0f} kW."
        )
        sz_afname = st.number_input(
            "Afnamevermogen sweep (kW)", 0.5, max_afname_kw, float(max_afname_kw), 0.5,
            help=f"Max laadvermogen van net. Wettelijke limiet: {max_afname_kw} kW."
        )

        # ── Data-kwaliteitscheck ──────────────────────────────────────────────────
        n_days_loaded = len(df) * 0.25 / 24.0
        n_days_sel    = len(sim_df) * 0.25 / 24.0
        sz_data       = sim_df  # standaard: gebruik geselecteerde periode
        if n_days_loaded < 350:
            st.warning(
                f"⚠️ Slechts **{n_days_loaded:.0f} dagen geladen.** "
                "Voor een betrouwbare battery sizing is **1 volledig jaar (365 dagen) "
                "vereist** — seizoenseffecten bepalen tot 40% van de jaaropbrengst. "
                "Gebruik de **365 d** knop onder ENTSO-E."
            )
        elif n_days_sel < 350:
            st.warning(
                f"⚠️ Geselecteerde periode: **{n_days_sel:.0f} dagen**. "
                f"Extrapolatie naar 1 jaar geeft mogelijk tot {max(0, int(abs(n_days_sel/182.5-1)*40)):.0f}% foutmarge. "
                "Selecteer een volledig jaar voor maximale nauwkeurigheid."
            )
            sz_data = df  # gebruik alle beschikbare data als fallback
        else:
            st.success(f"✅ {n_days_sel:.0f} dagen geselecteerd — ideaal voor jaaranalyse.")

        if not df.empty and st.button(
                "🚀 Start Battery Sizing Analyse", type="primary",
                key="btn_sizing", use_container_width=True):

            battery_sizes = list(range(int(sizes_min), int(sizes_max) + 1, int(sizes_step)))
            if not battery_sizes:
                st.error("Ongeldige sweep-instellingen.")
            else:
                # Vollaad-check: waarschuw als batterij nooit volgeladen kan worden op 1 dag
                max_chargeable_day = float(sz_afname) * 0.959 * 24  # max kWh/dag bij doorlopend laden
                oversized = [kwh for kwh in battery_sizes if kwh * 0.9 > max_chargeable_day]
                if oversized:
                    st.warning(
                        f"⚠️ Batterijen groter dan **{max_chargeable_day/0.9:.0f} kWh** kunnen bij "
                        f"{sz_afname:.1f} kW afnamevermogen nooit volledig worden opgeladen in 24u. "
                        f"Onrealistisch voor: {oversized} kWh. "
                        f"Resultaten voor die groottes zijn gebaseerd op partieel laden — "
                        f"grotere batterij ≠ meer opbrengst in dit geval."
                    )

                with st.spinner(f"MILP sweep over {len(battery_sizes)} groottes "
                                f"({sizes_min}–{sizes_max} kWh, stap {sizes_step} kWh)…"):
                    try:
                        solar_sz = None
                        if use_solar_sz and ELIA_AVAILABLE and own_kwp > 0:
                            try:
                                ec_sz     = EliaClient()
                                df_sol_sz = ec_sz.get_solar_forecast()
                                if df_sol_sz.empty:
                                    df_sol_sz = ec_sz.get_historical_solar(sel_start, sel_end)
                                if not df_sol_sz.empty:
                                    solar_sz = estimate_own_solar_kwh(df_sol_sz, own_kwp=own_kwp)
                            except Exception:
                                solar_sz = None

                        sz_results = battery_sizing_analysis(
                            sz_data,
                            battery_sizes_kwh=[float(s) for s in battery_sizes],
                            max_power_kw=float(sz_injectie),
                            charge_power_kw=float(sz_afname),
                            min_soc=min_soc_pct / 100,
                            initial_soc=initial_soc_pct / 100,
                            capex_per_kwh=float(capex_kwh),
                            lifespan_years=float(lifespan),
                        )
                    except Exception as e:
                        st.error(f"Sizing analyse fout: {e}")

    if "sizing_results" in st.session_state and st.session_state["sizing_results"] is not None:
        sr    = st.session_state["sizing_results"]
        valid = sr[sr["_netto_year"] > -900].copy()

        if not valid.empty:
            best_idx   = valid["_netto_year"].idxmax()
            best_kwh   = valid.loc[best_idx, "Capaciteit (kWh)"]
            best_netto = valid.loc[best_idx, "_netto_year"]
            best_irr   = valid.loc[best_idx, "_irr"]
            best_tv    = valid.loc[best_idx, "_terugverd"]

            # Vollaad-check voor aanbevolen grootte
            max_chargeable_day_sz = float(sr.get("_sz_afname", sz_afname if "sz_afname" in dir() else 9.2)) * 0.959 * 24
            if best_kwh * 0.9 > max_chargeable_day_sz:
                st.error(
                    f"⚠️ De aanbevolen batterij van **{best_kwh:.0f} kWh** kan bij het ingestelde "
                    f"afnamevermogen NOOIT volledig worden opgeladen! "
                    f"Verklein de sweep-range of verhoog het afnamevermogen."
                )

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("🏆 Optimale grootte",  f"{best_kwh:.0f} kWh")
            k2.metric("Netto winst/jaar",      f"{best_netto:+.0f} €")
            k3.metric("Terugverdientijd",      f"{best_tv:.1f} jaar")
            k4.metric("IRR",                   f"{best_irr:.1f} %")

            fig_sz = go.Figure()
            fig_sz.add_trace(go.Bar(
                x=valid["Capaciteit (kWh)"], y=valid["_rev_year"],
                name="Arbitrage-opbrengst (€/jr)", marker_color="#27AE60", opacity=0.85))
            fig_sz.add_trace(go.Bar(
                x=valid["Capaciteit (kWh)"], y=-valid["_capex_year"],
                name="CAPEX/jaar (€)", marker_color="#E74C3C", opacity=0.85))
            fig_sz.add_trace(go.Bar(
                x=valid["Capaciteit (kWh)"], y=-valid["_cap_tar_year"],
                name="Cap.tarief/jaar (€)", marker_color="#E67E22", opacity=0.85))
            fig_sz.add_trace(go.Scatter(
                x=valid["Capaciteit (kWh)"], y=valid["_netto_year"],
                name="Netto winst/jaar (€)",
                line=dict(color="white", width=3),
                mode="lines+markers", marker=dict(size=8)))
            fig_sz.add_vline(
                x=best_kwh, line_dash="dash", line_color="#F1C40F",
                annotation_text=f"Optimum: {best_kwh:.0f} kWh",
                annotation_position="top right")
            fig_sz.update_layout(
                title=f"Battery Sizing Analyse — {capex_kwh}€/kWh CAPEX, {lifespan}j levensduur",
                xaxis_title="Batterijcapaciteit (kWh)", yaxis_title="€ per jaar",
                barmode="relative", legend=dict(x=0, y=1.12, orientation="h"),
            )
            st.plotly_chart(fig_sz, use_container_width=True)

            fig_irr = go.Figure()
            fig_irr.add_trace(go.Scatter(
                x=valid["Capaciteit (kWh)"], y=valid["_irr"],
                name="IRR (%)", line=dict(color="#3498DB", width=2),
                mode="lines+markers"))
            fig_irr.add_trace(go.Scatter(
                x=valid["Capaciteit (kWh)"], y=valid["_terugverd"].clip(upper=30),
                name="Terugverdientijd (jaar)",
                line=dict(color="#E67E22", width=2, dash="dot"),
                mode="lines+markers", yaxis="y2"))
            fig_irr.update_layout(
                title="IRR en Terugverdientijd per Batterijgrootte",
                xaxis_title="Capaciteit (kWh)",
                yaxis=dict(title="IRR (%)"),
                yaxis2=dict(title="Terugverdientijd (jaar)", overlaying="y",
                            side="right", range=[0, 30]),
                legend=dict(x=0, y=1.12, orientation="h"),
            )
            st.plotly_chart(fig_irr, use_container_width=True)

            display_cols = ["Capaciteit (kWh)", "Rev. jaar (€)", "CAPEX jaar (€)",
                            "Cap.tarief jaar (€)", "Netto winst jaar (€)",
                            "Terugverdientijd (j)", "IRR (%)", "MILP laadpiek (kW)"]
            display_cols = [c for c in display_cols if c in sr.columns]
            st.dataframe(sr[display_cols], use_container_width=True, hide_index=True)

            st.caption(
                f"ℹ️ Gebaseerd op {len(sim_df)*0.25/24:.0f} dagen echte prijsdata. "
                f"Injectie: {sz_injectie:.1f} kW | Afname: {sz_afname:.1f} kW. "
                f"CAPEX €{capex_kwh}/kWh over {lifespan} jaar. "
                f"Exclusief: onderhoudskosten, subsidies."
            )


# ─────────────────────────────────────────────────────────────────────────────
# Mono vs Driefasig Vergelijkingstabel
# ─────────────────────────────────────────────────────────────────────────────
with st.expander("⚡ Mono vs Driefasig — Gedetailleerde Vergelijkingstabel", expanded=False):
    st.markdown(
        "Volledige kostprijs- en opbrengstanalyse naast elkaar, "
        "zodat je in één oogopslag ziet welke opstelling het meest rendabel is."
    )

    cmp_c1, cmp_c2, cmp_c3 = st.columns(3)
    with cmp_c1:
        cmp_capex  = st.number_input("CAPEX (€/kWh)", 10, 1000, 88, 1, key="cmp_capex",
                       help="Kostprijs per kWh batterijcapaciteit")
        cmp_leven  = st.number_input("Levensduur (jaar)", 5, 25, 12, 1, key="cmp_leven")
    with cmp_c2:
        cmp_kwh_m  = st.number_input("Batterij monofase (kWh)", 5, 100, 15, 5, key="cmp_kwh_m",
                       help="Optimale grootte voor monofase (typisch 15 kWh)")
        cmp_kwh_d  = st.number_input("Batterij driefasig (kWh)", 5, 100, 25, 5, key="cmp_kwh_d",
                       help="Optimale grootte voor driefasig (typisch 25 kWh)")
    with cmp_c3:
        cmp_onderhoud = st.number_input("Onderhoudskosten (€/jr)", 0, 500, 50, 10, key="cmp_onderhoud")
        cmp_subsidie  = st.number_input("Subsidie / premie (€)", 0, 5000, 0, 100, key="cmp_subsidie",
                          help="Éénmalige subsidie (bv. Vlaanderen, gemeente)")

    # ── Data-kwaliteitscheck ────────────────────────────────────────────────────
    n_days_cmp = len(sim_df) * 0.25 / 24.0
    cmp_data   = sim_df
    if n_days_cmp < 350:
        pct = max(0, int(abs(n_days_cmp/182.5-1)*40))
        st.warning(
            f"⚠️ **{n_days_cmp:.0f} dagen geselecteerd** — voor een correcte mono/driefasig "
            f"vergelijking is **1 volledig jaar aanbevolen** (tot {pct}% foutmarge). "
            "Laad 365 dagen via ENTSO-E en selecteer het volledige jaar."
        )
    else:
        st.success(f"✅ {n_days_cmp:.0f} dagen — ideaal voor de vergelijkingstabel.")

    if not df.empty and st.button("📊 Bereken vergelijkingstabel",
                                   type="primary", key="btn_cmp",
                                   use_container_width=True):
        with st.spinner("MILP berekeningen mono- en driefasig…"):
            try:
                cmp_results = {}
                for naam, (inj, afl, kwh) in {
                    "Monofase":  (5.0,  9.2,  cmp_kwh_m),
                    "Driefasig": (10.0, 15.9, cmp_kwh_d),
                }.items():
                    r = battery_sizing_analysis(
                        cmp_data, battery_sizes_kwh=[float(kwh)],
                        max_power_kw=inj, charge_power_kw=afl,
                        min_soc=min_soc_pct/100, initial_soc=initial_soc_pct/100,
                        capex_per_kwh=float(cmp_capex), lifespan_years=float(cmp_leven),
                    )
                    row        = r.iloc[0]
                    n_days_sim = len(sim_df) * 0.25 / 24.0
                    arbitrage_yr = row["_rev_year"]
                    cap_tar_yr   = row["_cap_tar_year"]
                    capex_tot    = kwh * cmp_capex
                    capex_yr     = capex_tot / cmp_leven
                    sub_yr       = cmp_subsidie / cmp_leven
                    netto_yr     = arbitrage_yr - cap_tar_yr - capex_yr - cmp_onderhoud + sub_yr
                    netto_cap    = capex_tot - cmp_subsidie
                    tv           = netto_cap / max(arbitrage_yr - cap_tar_yr - cmp_onderhoud, 0.01)
                    irr          = (arbitrage_yr - cap_tar_yr - cmp_onderhoud) / max(netto_cap, 1) * 100
                    cmp_results[naam] = {
                        "kwh": kwh, "inj_kw": inj, "afl_kw": afl,
                        "milp_piek":    float(row["MILP laadpiek (kW)"]),
                        "arbitrage_yr": arbitrage_yr,
                        "cap_tar_yr":   cap_tar_yr,
                        "onderhoud_yr": float(cmp_onderhoud),
                        "subsidie":     float(cmp_subsidie),
                        "sub_yr":       sub_yr,
                        "capex_tot":    capex_tot,
                        "capex_yr":     capex_yr,
                        "netto_yr":     netto_yr,
                        "irr":          irr,
                        "tv":           tv,
                        "lever":        cmp_leven,
                    }
                st.session_state["cmp_results"] = cmp_results
            except Exception as e:
                st.error(f"Vergelijkingsberekening fout: {e}")

    if st.session_state.get("cmp_results"):
        cr = st.session_state["cmp_results"]
        mo = cr.get("Monofase", {})
        dr = cr.get("Driefasig", {})

        if mo and dr:
            diff_netto = dr["netto_yr"] - mo["netto_yr"]
            diff_total = diff_netto * dr["lever"]
            winner     = "Driefasig" if diff_netto > 0 else "Monofase"

            if diff_netto > 0:
                st.success(f"🏆 **Driefasig** wint: +{abs(diff_netto):,.0f} €/jaar meer netto. "
                           f"Over {dr['lever']} jaar: +{abs(diff_total):,.0f} € extra.")
            else:
                st.info(f"🏆 **Monofase** wint: +{abs(diff_netto):,.0f} €/jaar meer netto. "
                        f"Driefasige meerkosten niet terugverdiend.")

            def be(v, sign=""):
                """Formatteer als Belgisch euro-bedrag."""
                s = f"{abs(v):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
                return f"{sign}{s}" if sign else s

            sections = [
                ("🏗️ INVESTERING", [
                    ("Batterijcapaciteit",        lambda c: f"{c['kwh']} kWh",                                       ""),
                    ("Totale investeringskost",   lambda c: be(c["capex_tot"]),                                       "eenmalig"),
                    ("Subsidie / premie",         lambda c: be(c["subsidie"], "- ") if c["subsidie"] > 0 else "—",  "aftrek"),
                    ("Netto te investeren",       lambda c: be(c["capex_tot"] - c["subsidie"]),                      "effectieve kost"),
                    ("Afschrijving per jaar",     lambda c: be(c["capex_yr"], "- "),                                 f"{c['lever']}j levensduur"),
                ]),
                ("⚡ TECHNISCHE LIMIETEN", [
                    ("Max. injectie op net",      lambda c: f"{c['inj_kw']:.0f} kW",     "Fluvius-limiet"),
                    ("Max. afname van net",       lambda c: f"{c['afl_kw']:.1f} kW",     "Fluvius-limiet"),
                    ("MILP gekozen laadpiek",     lambda c: f"{c['milp_piek']:.1f} kW",  "Optimaal per MILP"),
                ]),
                ("💶 JAARLIJKSE FINANCIËN", [
                    ("+ Arbitrage-opbrengst",     lambda c: be(c["arbitrage_yr"], "+ "), "laden laag → ontladen hoog"),
                    ("− Capaciteitstarief",       lambda c: be(c["cap_tar_yr"],   "- "), "piek × €60/kW/jaar"),
                    ("− Onderhoudskosten",        lambda c: be(c["onderhoud_yr"], "- "), "geschat"),
                    ("− Afschrijving CAPEX",      lambda c: be(c["capex_yr"],     "- "), ""),
                    ("+ Subsidie (jaarequiv.)",   lambda c: be(c["sub_yr"],       "+ ") if c["subsidie"] > 0 else "—", ""),
                ]),
                ("🏆 RESULTAAT", [
                    ("Netto winst per jaar",      lambda c: be(c["netto_yr"],        "+ "), "na alle kosten"),
                    ("IRR",                       lambda c: f"{c['irr']:.1f} %",           "opbrengst / investering"),
                    ("Terugverdientijd",          lambda c: f"{c['tv']:.1f} jaar",          ""),
                    ("Totale winst (looptijd)",   lambda c: be(c["netto_yr"] * c["lever"], "+ "), f"over {c['lever']} jaar"),
                ]),
            ]

            for sec_title, sec_rows in sections:
                st.markdown(f"**{sec_title}**")
                tbl = []
                for label, val_fn, note in sec_rows:
                    tbl.append({
                        "Onderdeel":    label,
                        "Toelichting":  note,
                        "⚡ Monofase":  val_fn(mo),
                        "⚡⚡⚡ Driefasig": val_fn(dr),
                    })
                st.dataframe(pd.DataFrame(tbl), use_container_width=True,
                             hide_index=True,
                             column_config={
                                 "Onderdeel":       st.column_config.TextColumn(width="medium"),
                                 "Toelichting":     st.column_config.TextColumn(width="medium"),
                                 "⚡ Monofase":     st.column_config.TextColumn(width="small"),
                                 "⚡⚡⚡ Driefasig":  st.column_config.TextColumn(width="small"),
                             })

            # KPI vergelijking
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Monofase netto/jr",  f"{mo['netto_yr']:,.0f} €".replace(",", "."))
            k2.metric("Driefasig netto/jr", f"{dr['netto_yr']:,.0f} €".replace(",", "."),
                      delta=f"+{diff_netto:,.0f} €".replace(",", ".") if diff_netto > 0 else f"{diff_netto:,.0f} €".replace(",", "."))
            k3.metric("Monofase TV",        f"{mo['tv']:.1f} jaar")
            k4.metric("Driefasig TV",       f"{dr['tv']:.1f} jaar",
                      delta=f"{dr['tv']-mo['tv']:+.1f} jaar", delta_color="inverse")

            st.caption(
                f"ℹ️ Berekend op MILP-optimalisatie op de geselecteerde periode "
                f"({len(sim_df)*0.25/24:.0f} dagen), geëxtrapoleerd naar 1 jaar. "
                "Exclusief: netaansluitingskosten, omvormerkosten, belastingen. "
                "Geen garantie voor toekomstig rendement."
            )
