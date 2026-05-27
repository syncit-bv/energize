#!/usr/bin/env python3
"""
MILP Optimizer for EMS Belgium — v1.3
================================
Drie optimalisatie-niveaus:
  1. optimize_battery_schedule()        — standaard arbitrage
  2. optimize_battery_schedule()        — zelfde, maar met uitgebreid horizon (day-ahead)
  3. optimize_battery_schedule_solar()  — arbitrage + gratis solar laden

Alle functies leveren (result_df, summary_dict) op met solve_time en solver_iterations.
"""

import io, re, time, contextlib
from typing import Dict, Tuple, Optional

import pandas as pd
import pulp

# België totale geïnstalleerde solar capaciteit (MWp, schatting 2026)
BE_TOTAL_SOLAR_MWP = 9_500.0


# ─────────────────────────────────────────────────────────────────────────────
# 1. Standaard MILP (basis + day-ahead — zelfde functie, ander input)
# ─────────────────────────────────────────────────────────────────────────────
def optimize_battery_schedule(
    prices_df: pd.DataFrame,
    battery_kwh: float = 10.0,
    max_power_kw: float = 5.0,
    min_soc: float = 0.10,
    min_end_soc: float = 0.20,
    efficiency: float = 0.92,
    initial_soc: float = 0.50,
    time_horizon_hours: Optional[int] = None,
    label: str = "MILP",
) -> Tuple[pd.DataFrame, Dict]:
    """
    Standaard battery arbitrage MILP.

    Geef prices_df met enkel de geselecteerde periode → MILP basis.
    Geef prices_df uitgebreid met morgen's prijzen  → MILP + day-ahead.
    De optimizer ziet het verschil niet — de horizon bepaalt het gedrag.
    """
    df = _select_slots(prices_df, time_horizon_hours)
    if df.empty:
        raise ValueError("Geen prijsdata meegegeven aan optimizer.")

    T  = len(df)
    dt = 0.25

    prob      = pulp.LpProblem("Battery_Arbitrage", pulp.LpMaximize)
    charge    = pulp.LpVariable.dicts("C", range(T), lowBound=0, cat="Continuous")
    discharge = pulp.LpVariable.dicts("D", range(T), lowBound=0, cat="Continuous")
    soc       = pulp.LpVariable.dicts(
        "S", range(T + 1),
        lowBound=min_soc * battery_kwh,
        upBound=battery_kwh,
        cat="Continuous",
    )

    prob += pulp.lpSum(
        discharge[t] * df.iloc[t]["price_eur_mwh"] / 1000
        - charge[t]  * df.iloc[t]["price_eur_mwh"] / 1000
        for t in range(T)
    ), "Revenue"

    prob += soc[0] == initial_soc * battery_kwh, "Init_SOC"
    prob += soc[T] >= min_end_soc * battery_kwh,  "Min_End_SOC"

    max_e = max_power_kw * dt
    eta   = efficiency ** 0.5

    for t in range(T):
        prob += soc[t+1] == soc[t] + eta * charge[t] - discharge[t] / eta, f"Dyn_{t}"
        prob += charge[t]    <= max_e, f"Cmax_{t}"
        prob += discharge[t] <= max_e, f"Dmax_{t}"

    result_df, summary = _solve_and_extract(
        prob, df, T, battery_kwh, charge, discharge, soc, label=label
    )
    return result_df, summary


# ─────────────────────────────────────────────────────────────────────────────
# 2. MILP + Solar self-consumption
# ─────────────────────────────────────────────────────────────────────────────
def optimize_battery_schedule_solar(
    prices_df: pd.DataFrame,
    solar_kwh_per_slot: pd.Series,
    battery_kwh: float = 10.0,
    max_power_kw: float = 5.0,
    min_soc: float = 0.10,
    min_end_soc: float = 0.20,
    efficiency: float = 0.92,
    initial_soc: float = 0.50,
    time_horizon_hours: Optional[int] = None,
    label: str = "MILP+Solar",
) -> Tuple[pd.DataFrame, Dict]:
    """
    MILP met solar self-consumption model.

    Twee laad-variabelen per tijdstip:
      charge_grid[t]  — laden van het net  (kost day-ahead prijs)
      charge_solar[t] — laden van eigen PV (GRATIS — geen grid kost)

    charge_solar[t] ≤ solar_kwh_per_slot[t]   (beperkt door eigen productie)
    charge_grid[t] + charge_solar[t] ≤ max_e  (beperkt door max vermogen)

    Doel: maximaliseer revenue van ontladen MINUS gridkosten van laden.
    """
    df = _select_slots(prices_df, time_horizon_hours)
    if df.empty:
        raise ValueError("Geen prijsdata meegegeven aan optimizer.")

    T  = len(df)
    dt = 0.25

    # Align solar_kwh_per_slot op df's tijdslots (reindex, fill 0)
    if hasattr(solar_kwh_per_slot, "index") and hasattr(solar_kwh_per_slot.index, "tz_localize"):
        pass  # already has index
    solar_vals = _align_solar(solar_kwh_per_slot, df)

    prob          = pulp.LpProblem("Battery_Solar_MILP", pulp.LpMaximize)
    charge_grid   = pulp.LpVariable.dicts("CG", range(T), lowBound=0, cat="Continuous")
    charge_solar  = pulp.LpVariable.dicts("CS", range(T), lowBound=0, cat="Continuous")
    discharge     = pulp.LpVariable.dicts("D",  range(T), lowBound=0, cat="Continuous")
    soc           = pulp.LpVariable.dicts(
        "S", range(T + 1),
        lowBound=min_soc * battery_kwh,
        upBound=battery_kwh,
        cat="Continuous",
    )

    # Doel: discharge-inkomsten MINUS grid-laadkosten (solar laden is gratis)
    prob += pulp.lpSum(
        discharge[t] * df.iloc[t]["price_eur_mwh"] / 1000
        - charge_grid[t] * df.iloc[t]["price_eur_mwh"] / 1000
        for t in range(T)
    ), "Revenue_Solar"

    prob += soc[0] == initial_soc * battery_kwh, "Init_SOC"
    prob += soc[T] >= min_end_soc * battery_kwh,  "Min_End_SOC"

    max_e = max_power_kw * dt
    eta   = efficiency ** 0.5

    for t in range(T):
        solar_avail = float(solar_vals[t])
        prob += (soc[t+1] == soc[t]
                 + eta * (charge_grid[t] + charge_solar[t])
                 - discharge[t] / eta), f"Dyn_{t}"
        # Max totaal laadvermogen
        prob += charge_grid[t] + charge_solar[t] <= max_e, f"Cmax_{t}"
        # Solar beperkt door eigen productie
        prob += charge_solar[t] <= solar_avail, f"Solar_{t}"
        # Discharge beperkt door max vermogen
        prob += discharge[t] <= max_e, f"Dmax_{t}"

    # Extraheer resultaten (inclusief solar split)
    result_df, summary = _solve_and_extract(
        prob, df, T, battery_kwh, charge_grid, discharge, soc,
        label=label,
        extra_charge=charge_solar,
        solar_vals=solar_vals,
    )
    return result_df, summary


# ─────────────────────────────────────────────────────────────────────────────
# 3. Solar helper: schaal Elia-brede forecast naar eigen installatie
# ─────────────────────────────────────────────────────────────────────────────
def estimate_own_solar_kwh(
    solar_df: pd.DataFrame,
    own_kwp: float = 6.3,
    be_total_mwp: float = BE_TOTAL_SOLAR_MWP,
) -> pd.Series:
    """
    Schaal de Elia-brede solar forecast (MW) naar eigen installatie (kWh/kwartier).

    Formule:
      eigen_productie_kw = (forecast_be_mw × 1000 kW/MW) × (eigen_kwp / be_total_kwp)
      eigen_productie_kwh_per_slot = eigen_productie_kw × 0.25 h

    Parameters:
      solar_df   : DataFrame van EliaClient.get_solar_forecast() of get_historical_solar()
      own_kwp    : eigen PV-vermogen in kWp (default 6.3 kWp)
      be_total_mwp: totale Belgische solar capaciteit in MWp (default 9500)

    Returns:
      pd.Series indexed by datetime, waarden in kWh per 15-min slot
    """
    if solar_df.empty or "datetime" not in solar_df.columns:
        return pd.Series(dtype=float)

    # Zoek de meest bruikbare forecast kolom
    priority = ["dayaheadforecast", "mostrecentforecast", "weekaheadforecast",
                 "measured", "upscaled"]
    forecast_col = None
    col_lower = {c.lower(): c for c in solar_df.columns}
    for cand in priority:
        if cand.lower() in col_lower:
            forecast_col = col_lower[cand.lower()]
            break

    if forecast_col is None:
        # Laatste redmiddel: eerste numerieke kolom
        num_cols = solar_df.select_dtypes(include="number").columns.tolist()
        if num_cols:
            forecast_col = num_cols[0]
        else:
            return pd.Series(dtype=float)

    fraction       = own_kwp / (be_total_mwp * 1000)  # fractie van totale kW capaciteit
    own_kw         = solar_df[forecast_col].clip(lower=0) * 1000 * fraction  # kW
    own_kwh_slot   = own_kw * 0.25  # kWh per 15-min slot

    return pd.Series(
        own_kwh_slot.values,
        index=pd.DatetimeIndex(solar_df["datetime"]),
        name="own_solar_kwh",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Interne helpers
# ─────────────────────────────────────────────────────────────────────────────
def _select_slots(prices_df: pd.DataFrame, time_horizon_hours: Optional[int]) -> pd.DataFrame:
    if time_horizon_hours is None or time_horizon_hours <= 0:
        return prices_df.copy()
    return prices_df.head(int(time_horizon_hours * 4)).copy()


def _align_solar(solar_series: pd.Series, prices_df: pd.DataFrame) -> list:
    """
    Align solar kWh series met de tijdslots in prices_df.
    Geeft een lijst van float waarden (0.0 als geen solar data voor dat slot).
    """
    if solar_series is None or solar_series.empty:
        return [0.0] * len(prices_df)

    try:
        idx = pd.DatetimeIndex(solar_series.index)
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
        solar_indexed = pd.Series(solar_series.values, index=idx)

        price_idx = pd.DatetimeIndex(prices_df["datetime"])
        if price_idx.tz is None:
            price_idx = price_idx.tz_localize("UTC")

        aligned = solar_indexed.reindex(price_idx, method="nearest", tolerance="20min").fillna(0.0)
        return aligned.clip(lower=0).tolist()
    except Exception:
        return [0.0] * len(prices_df)


def _solve_and_extract(
    prob, df, T, battery_kwh,
    charge_var, discharge_var, soc_var,
    label="MILP",
    extra_charge=None,
    solar_vals=None,
) -> Tuple[pd.DataFrame, Dict]:
    """Solve, extract results en bouw summary dict."""
    t_start    = time.time()
    log_buffer = io.StringIO()
    with contextlib.redirect_stdout(log_buffer):
        status = prob.solve(pulp.PULP_CBC_CMD(msg=True, timeLimit=60))
    solve_time  = round(time.time() - t_start, 2)
    solver_log  = log_buffer.getvalue()
    iterations  = _parse_iterations(solver_log)

    results = []
    for t in range(T):
        c_kwh  = pulp.value(charge_var[t])    or 0.0
        cs_kwh = pulp.value(extra_charge[t])  if extra_charge else 0.0
        cs_kwh = cs_kwh or 0.0
        d_kwh  = pulp.value(discharge_var[t]) or 0.0
        s_kwh  = pulp.value(soc_var[t + 1])   or 0.0
        p      = df.iloc[t]["price_eur_mwh"]
        rev    = d_kwh * p / 1000 - c_kwh * p / 1000  # solar charge heeft geen gridkost

        row = {
            "datetime":         df.iloc[t]["datetime"],
            "price_eur_mwh":    p,
            "charge_kwh":       c_kwh + cs_kwh,     # totaal laden
            "charge_grid_kwh":  c_kwh,               # laden van net
            "charge_solar_kwh": cs_kwh,              # laden van solar
            "discharge_kwh":    d_kwh,
            "soc_kwh":          s_kwh,
            "soc_pct":          s_kwh / battery_kwh * 100,
            "net_revenue_eur":  rev,
        }
        if solar_vals:
            row["solar_available_kwh"] = solar_vals[t]
        results.append(row)

    result_df = pd.DataFrame(results)
    total_rev = pulp.value(prob.objective) or 0.0

    summary = {
        "label":                  label,
        "status":                 pulp.LpStatus[status],
        "total_net_revenue_eur":  round(total_rev, 4),
        "total_charged_kwh":      round(result_df["charge_kwh"].sum(), 2),
        "total_charged_grid_kwh": round(result_df["charge_grid_kwh"].sum(), 2),
        "total_charged_solar_kwh":round(result_df["charge_solar_kwh"].sum(), 2),
        "total_discharged_kwh":   round(result_df["discharge_kwh"].sum(), 2),
        "final_soc_pct":          round(result_df["soc_pct"].iloc[-1], 1),
        "num_slots":              T,
        "solve_time_sec":         solve_time,
        "solver_iterations":      iterations,
        "solver_log":             solver_log,
    }
    return result_df, summary


def _parse_iterations(log: str) -> int:
    for line in log.splitlines():
        m = re.search(r"-\s+(\d+)\s+iterations", line)
        if m:
            return int(m.group(1))
        m = re.search(r"[Tt]otal\s+iterations[:\s]+(\d+)", line)
        if m:
            return int(m.group(1))
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# CLI test
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import numpy as np

    dates  = pd.date_range("2026-05-26", periods=96, freq="15min", tz="UTC")
    prices = np.concatenate([
        np.full(12, -80),  # 00-03: negatief
        np.full(20, 15),   # 03-08: laag
        np.full(24, -40),  # 08-14: negatief (solar)
        np.full(16, 60),   # 14-18: gemiddeld
        np.full(16, 200),  # 18-22: hoog
        np.full(8,  120),  # 22-00: dalend
    ])
    df = pd.DataFrame({"datetime": dates, "price_eur_mwh": prices})

    # Test 1: basis
    sch, summ = optimize_battery_schedule(df, label="MILP Basis")
    print(f"MILP Basis:  {summ['total_net_revenue_eur']:.3f} € | {summ['solve_time_sec']} s")

    # Test 2: solar (simuleer eigen productie: piek 10u-14u)
    solar_mw   = np.zeros(96)
    solar_mw[32:56] = 3500  # 08u-14u: piek 3500 MW België-breed
    solar_ser  = pd.Series(solar_mw, index=dates)
    own_solar  = estimate_own_solar_kwh(
        pd.DataFrame({"datetime": dates, "dayaheadforecast": solar_mw}), own_kwp=6.3
    )
    print(f"\nOwn solar piek: {own_solar.max():.3f} kWh/slot")

    sch2, summ2 = optimize_battery_schedule_solar(df, own_solar, label="MILP+Solar")
    print(f"MILP+Solar:  {summ2['total_net_revenue_eur']:.3f} € | grid {summ2['total_charged_grid_kwh']:.1f} kWh | solar {summ2['total_charged_solar_kwh']:.1f} kWh")
    print(f"Verbetering: {summ2['total_net_revenue_eur'] - summ['total_net_revenue_eur']:+.3f} €")
