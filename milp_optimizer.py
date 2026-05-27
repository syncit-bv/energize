#!/usr/bin/env python3
"""
MILP Optimizer for EMS Belgium — v1.4
======================================
Drie optimalisatie-niveaus:
  1. optimize_battery_schedule()        — standaard arbitrage
  2. optimize_battery_schedule()        — met day-ahead lookahead (MPC rolling horizon)
  3. optimize_battery_schedule_solar()  — arbitrage + gratis solar laden

Rolling Horizon (MPC) aanpak:
  execute_until = datum tot wanneer trades worden "uitgevoerd"
  Slots ná execute_until = lookahead: beïnvloeden de end-SOC keuze maar
  worden NIET uitgevoerd en tellen NIET mee in de revenue vergelijking.

  Voorbeeld: execute_until=vandaag, lookahead=morgen
  → MILP weet wat morgen's prijzen zijn en laadt vandaag optimaal vol
  → Morgen's trades staan als is_lookahead=True in result_df
  → summary["revenue_execute_eur"] = enkel vandaag's revenue
"""

import io, re, time, contextlib
from datetime import date, datetime
from typing import Dict, Optional, Tuple

import pandas as pd
import pulp

BE_TOTAL_SOLAR_MWP = 9_500.0


# ─────────────────────────────────────────────────────────────────────────────
# 1. Standaard MILP + Day-ahead (rolling horizon)
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
    execute_until: Optional[date] = None,
) -> Tuple[pd.DataFrame, Dict]:
    """
    Battery arbitrage MILP met optionele rolling horizon.

    Parameters
    ----------
    execute_until : date, optional
        Enkel slots op of vóór deze datum worden als "te executeren" beschouwd.
        Slots na execute_until zijn lookahead: MILP gebruikt ze om de optimale
        end-SOC te bepalen, maar ze worden niet uitgevoerd en tellen niet mee
        in revenue_execute_eur.
        None = alle slots uitvoeren (MILP Basis).
    """
    df = _select_slots(prices_df, time_horizon_hours)
    if df.empty:
        raise ValueError("Geen prijsdata meegegeven aan optimizer.")

    T   = len(df)
    dt  = 0.25
    eta = efficiency ** 0.5

    # Bepaal welke slots "execute" zijn vs "lookahead"
    if execute_until is not None:
        exec_mask = pd.to_datetime(df["datetime"]).dt.date <= execute_until
    else:
        exec_mask = pd.Series([True] * T)
    n_execute = int(exec_mask.sum())

    prob      = pulp.LpProblem("Battery_Arbitrage", pulp.LpMaximize)
    charge    = pulp.LpVariable.dicts("C", range(T), lowBound=0, cat="Continuous")
    discharge = pulp.LpVariable.dicts("D", range(T), lowBound=0, cat="Continuous")
    soc       = pulp.LpVariable.dicts(
        "S", range(T + 1),
        lowBound=min_soc * battery_kwh,
        upBound=battery_kwh,
        cat="Continuous",
    )

    # Objectief: maximaliseer revenue over ALLE slots (lookahead motiveert MILP
    # om de juiste end-SOC te kiezen, ook al worden die trades niet uitgevoerd)
    prob += pulp.lpSum(
        discharge[t] * df.iloc[t]["price_eur_mwh"] / 1000
        - charge[t]  * df.iloc[t]["price_eur_mwh"] / 1000
        for t in range(T)
    ), "Revenue"

    prob += soc[0] == initial_soc * battery_kwh, "Init_SOC"
    prob += soc[T] >= min_end_soc * battery_kwh,  "Min_End_SOC"

    max_e = max_power_kw * dt

    for t in range(T):
        prob += soc[t+1] == soc[t] + eta * charge[t] - discharge[t] / eta, f"Dyn_{t}"
        prob += charge[t]    <= max_e, f"Cmax_{t}"
        prob += discharge[t] <= max_e, f"Dmax_{t}"

    result_df, summary = _solve_and_extract(
        prob, df, T, battery_kwh, charge, discharge, soc,
        label=label,
        exec_mask=exec_mask,
        n_execute=n_execute,
    )
    return result_df, summary


# ─────────────────────────────────────────────────────────────────────────────
# 2. MILP + Solar self-consumption (rolling horizon)
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
    execute_until: Optional[date] = None,
) -> Tuple[pd.DataFrame, Dict]:
    """MILP met solar self-consumption + optionele rolling horizon."""
    df = _select_slots(prices_df, time_horizon_hours)
    if df.empty:
        raise ValueError("Geen prijsdata meegegeven.")

    T   = len(df)
    dt  = 0.25
    eta = efficiency ** 0.5

    if execute_until is not None:
        exec_mask = pd.to_datetime(df["datetime"]).dt.date <= execute_until
    else:
        exec_mask = pd.Series([True] * T)
    n_execute = int(exec_mask.sum())

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

    prob += pulp.lpSum(
        discharge[t] * df.iloc[t]["price_eur_mwh"] / 1000
        - charge_grid[t] * df.iloc[t]["price_eur_mwh"] / 1000
        for t in range(T)
    ), "Revenue_Solar"

    prob += soc[0] == initial_soc * battery_kwh, "Init_SOC"
    prob += soc[T] >= min_end_soc * battery_kwh,  "Min_End_SOC"

    max_e = max_power_kw * dt

    for t in range(T):
        solar_avail = float(solar_vals[t])
        prob += (soc[t+1] == soc[t]
                 + eta * (charge_grid[t] + charge_solar[t])
                 - discharge[t] / eta), f"Dyn_{t}"
        prob += charge_grid[t] + charge_solar[t] <= max_e, f"Cmax_{t}"
        prob += charge_solar[t] <= solar_avail,             f"Solar_{t}"
        prob += discharge[t]    <= max_e,                   f"Dmax_{t}"

    result_df, summary = _solve_and_extract(
        prob, df, T, battery_kwh, charge_grid, discharge, soc,
        label=label,
        extra_charge=charge_solar,
        solar_vals=solar_vals,
        exec_mask=exec_mask,
        n_execute=n_execute,
    )
    return result_df, summary


# ─────────────────────────────────────────────────────────────────────────────
# 3. Solar helper
# ─────────────────────────────────────────────────────────────────────────────
def estimate_own_solar_kwh(
    solar_df: pd.DataFrame,
    own_kwp: float = 6.3,
    be_total_mwp: float = BE_TOTAL_SOLAR_MWP,
) -> pd.Series:
    """Schaal Elia-brede solar forecast (MW) naar eigen installatie (kWh/kwartier)."""
    if solar_df.empty or "datetime" not in solar_df.columns:
        return pd.Series(dtype=float)

    priority  = ["dayaheadforecast","mostrecentforecast","weekaheadforecast","measured","upscaled"]
    col_lower = {c.lower(): c for c in solar_df.columns}
    forecast_col = next((col_lower[c] for c in priority if c in col_lower), None)
    if forecast_col is None:
        num_cols = solar_df.select_dtypes(include="number").columns.tolist()
        if num_cols:
            forecast_col = num_cols[0]
        else:
            return pd.Series(dtype=float)

    fraction     = own_kwp / (be_total_mwp * 1000)
    own_kw       = solar_df[forecast_col].clip(lower=0) * 1000 * fraction
    own_kwh_slot = own_kw * 0.25

    return pd.Series(
        own_kwh_slot.values,
        index=pd.DatetimeIndex(solar_df["datetime"]),
        name="own_solar_kwh",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Interne helpers
# ─────────────────────────────────────────────────────────────────────────────
def _select_slots(prices_df, time_horizon_hours):
    if time_horizon_hours is None or time_horizon_hours <= 0:
        return prices_df.copy()
    return prices_df.head(int(time_horizon_hours * 4)).copy()


def _align_solar(solar_series, prices_df):
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
    exec_mask=None,
    n_execute=None,
) -> Tuple[pd.DataFrame, Dict]:
    """Solve en extraheer resultaten met execute/lookahead splitsing."""
    if exec_mask is None:
        exec_mask = pd.Series([True] * T)
    if n_execute is None:
        n_execute = T

    t_start    = time.time()
    log_buffer = io.StringIO()
    with contextlib.redirect_stdout(log_buffer):
        status = prob.solve(pulp.PULP_CBC_CMD(msg=True, timeLimit=60))
    solve_time = round(time.time() - t_start, 2)
    solver_log = log_buffer.getvalue()
    iterations = _parse_iterations(solver_log)

    results = []
    for t in range(T):
        c_kwh  = pulp.value(charge_var[t])    or 0.0
        cs_kwh = (pulp.value(extra_charge[t]) or 0.0) if extra_charge else 0.0
        d_kwh  = pulp.value(discharge_var[t]) or 0.0
        s_kwh  = pulp.value(soc_var[t + 1])   or 0.0
        p      = df.iloc[t]["price_eur_mwh"]
        rev    = d_kwh * p / 1000 - c_kwh * p / 1000
        is_lah = not bool(exec_mask.iloc[t])

        row = {
            "datetime":          df.iloc[t]["datetime"],
            "price_eur_mwh":     p,
            "charge_kwh":        c_kwh + cs_kwh,
            "charge_grid_kwh":   c_kwh,
            "charge_solar_kwh":  cs_kwh,
            "discharge_kwh":     d_kwh,
            "soc_kwh":           s_kwh,
            "soc_pct":           s_kwh / battery_kwh * 100,
            "net_revenue_eur":   rev,
            "is_lookahead":      is_lah,   # True = morgen/preview, niet uitvoeren
        }
        if solar_vals:
            row["solar_available_kwh"] = solar_vals[t]
        results.append(row)

    result_df   = pd.DataFrame(results)
    total_rev   = pulp.value(prob.objective) or 0.0

    # Revenue splitsing: execute vs lookahead
    exec_df   = result_df[~result_df["is_lookahead"]]
    lah_df    = result_df[ result_df["is_lookahead"]]
    rev_exec  = round(exec_df["net_revenue_eur"].sum(), 4)
    rev_lah   = round(lah_df["net_revenue_eur"].sum(),  4)

    # SOC aan het einde van de execute-periode (= aanbevolen midnight SOC)
    final_exec_soc = round(exec_df["soc_pct"].iloc[-1], 1) if not exec_df.empty else \
                     round(result_df["soc_pct"].iloc[-1], 1)

    # Actieve slots = enkel slots met effectieve laad/ontlaad actie (geen HOLD)
    active_mask = (
        ((result_df["charge_kwh"] > 0.01) | (result_df["discharge_kwh"] > 0.01))
        & (~result_df["is_lookahead"])
    )
    n_active = int(active_mask.sum())

    summary = {
        "label":                   label,
        "status":                  pulp.LpStatus[status],
        "total_net_revenue_eur":   rev_exec,
        "revenue_execute_eur":     rev_exec,
        "revenue_lookahead_eur":   rev_lah,
        "num_slots":               T,
        "num_slots_execute":       n_execute,
        "num_slots_lookahead":     T - n_execute,
        "num_active_slots":        n_active,
        "total_charged_kwh":       round(exec_df["charge_kwh"].sum(), 2),
        "total_charged_grid_kwh":  round(exec_df["charge_grid_kwh"].sum(), 2),
        "total_charged_solar_kwh": round(exec_df["charge_solar_kwh"].sum(), 2),
        "total_discharged_kwh":    round(exec_df["discharge_kwh"].sum(), 2),
        "final_soc_pct":           final_exec_soc,
        "final_lookahead_soc_pct": round(result_df["soc_pct"].iloc[-1], 1),
        "solve_time_sec":          solve_time,
        "solver_iterations":       iterations,
        "solver_log":              solver_log,
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
    from datetime import date, timedelta

    today    = date(2026, 5, 27)
    tomorrow = today + timedelta(days=1)

    dates_t = pd.date_range('2026-05-27', periods=96, freq='15min', tz='UTC')
    dates_d = pd.date_range('2026-05-28', periods=96, freq='15min', tz='UTC')

    prices_t = np.concatenate([np.full(32,-20), np.full(32,60), np.full(32,100)])
    prices_d = np.concatenate([np.full(8,250),  np.full(88,50)])  # hoge piek morgen

    df = pd.DataFrame({
        'datetime':      list(dates_t) + list(dates_d),
        'price_eur_mwh': list(prices_t) + list(prices_d),
    })

    print("=== MILP Basis (enkel vandaag) ===")
    sch1, s1 = optimize_battery_schedule(
        df[df['datetime'].dt.date == today], label='MILP Basis')
    print(f"Revenue: {s1['revenue_execute_eur']:.3f} €  |  slots: {s1['num_slots_execute']}")
    print(f"Eind SOC vandaag: {s1['final_soc_pct']:.1f}%")

    print("\n=== MILP+DA (vandaag + morgen lookahead) ===")
    sch2, s2 = optimize_battery_schedule(
        df, label='MILP+DA', execute_until=today)
    exec_slots = sch2[~sch2['is_lookahead']]
    lah_slots  = sch2[ sch2['is_lookahead']]
    print(f"Revenue execute: {s2['revenue_execute_eur']:.3f} €  |  execute slots: {s2['num_slots_execute']}")
    print(f"Revenue lookahead (preview morgen): {s2['revenue_lookahead_eur']:.3f} €")
    print(f"Eind SOC vandaag: {s2['final_soc_pct']:.1f}%  ← hoger want MILP weet van morgen's pieken")
    print(f"Vandaag trades: {(exec_slots['charge_kwh']>0.01).sum()} ladingen, "
          f"{(exec_slots['discharge_kwh']>0.01).sum()} ontladingen")
    print(f"Morgen preview:  {(lah_slots['charge_kwh']>0.01).sum()} ladingen, "
          f"{(lah_slots['discharge_kwh']>0.01).sum()} ontladingen  (niet uitvoeren!)")
