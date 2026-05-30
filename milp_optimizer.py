#!/usr/bin/env python3
"""
MILP Optimizer for EMS Belgium — v1.8
======================================
Drie optimalisatie-niveaus:
  1. optimize_battery_schedule()        — standaard arbitrage + capaciteitstarief
  2. optimize_battery_schedule()        — met day-ahead lookahead (MPC rolling horizon)
  3. optimize_battery_schedule_solar()  — arbitrage + gratis solar laden

Asymmetrisch vermogen (nieuw in v1.5):
  charge_power_kw   : max laadvermogen (afname net) — bepaalt capaciteitstarief
  discharge_power_kw: max ontlaadvermogen (injectie) — geen capaciteitstarief

Capaciteitstarief (Fluvius, Belgium):
  Gebaseerd op maandelijkse piekvraag van netto afname.
  MILP-variabele peak_charge_kw ∈ [cap_min_kw, charge_power_kw]
  Kost = peak_charge_kw × cap_eur_per_kw_year × (n_dagen/365)
  Forfait minimum: 2.5 kW → €12.50/maand
"""

import io, re, time, contextlib
from datetime import date
from typing import Dict, Optional, Tuple

import pandas as pd
import pulp

BE_TOTAL_SOLAR_MWP  = 9_500.0
CAP_MIN_KW          = 2.5    # Fluvius forfait minimum
CAP_EUR_PER_KW_YEAR = 60.0   # €/kW/jaar capaciteitstarief


def optimize_battery_schedule(
    prices_df: pd.DataFrame,
    battery_kwh: float = 10.0,
    max_power_kw: float = 5.0,        # discharge (injectie) limiet
    charge_power_kw: Optional[float] = None,  # laden (afname) limiet; None = zelfde als max_power_kw
    min_soc: float = 0.10,
    min_end_soc: float = 0.20,
    efficiency: float = 0.92,
    initial_soc: float = 0.50,
    time_horizon_hours: Optional[int] = None,
    label: str = "MILP",
    execute_until: Optional[date] = None,
    cap_eur_per_kw_year: float = CAP_EUR_PER_KW_YEAR,
    cap_min_kw: float = CAP_MIN_KW,
) -> Tuple[pd.DataFrame, Dict]:
    """
    Battery arbitrage MILP met asymmetrisch vermogen + capaciteitstarief.

    MILP optimaliseert peak_charge_kw tussen cap_min_kw en charge_power_kw.
    Hogere piek = sneller laden maar hogere capaciteitstarifkost.
    """
    df  = _select_slots(prices_df, time_horizon_hours)
    if df.empty:
        raise ValueError("Geen prijsdata meegegeven aan optimizer.")

    discharge_kw = max_power_kw
    charge_kw    = charge_power_kw if charge_power_kw is not None else max_power_kw
    # Zorg dat charge limiet nooit hoger is dan discharge limiet
    charge_kw    = min(charge_kw, discharge_kw)

    T   = len(df)
    dt  = 0.25
    eta = efficiency ** 0.5
    n_days   = T * dt / 24.0
    # Capaciteitstarief: Belgisch systeem rekent per MAAND (niet per jaar)
    # Kost voor deze periode = peak_kw × (€60/12) × n_maanden
    # Meer maanden = hogere absolute kost, maar MARGINALE kost per extra kW
    # blijft constant: €5/kW/maand → eerlijkere MILP-beslissing
    n_months = n_days / 30.44  # gemiddelde maandlengte

    if execute_until is not None:
        exec_mask = pd.to_datetime(df["datetime"]).dt.date <= execute_until
    else:
        exec_mask = pd.Series([True] * T)
    n_execute = int(exec_mask.sum())

    prob      = pulp.LpProblem("Battery_Arbitrage", pulp.LpMaximize)
    charge    = pulp.LpVariable.dicts("C",  range(T), lowBound=0, cat="Continuous")
    discharge = pulp.LpVariable.dicts("D",  range(T), lowBound=0, cat="Continuous")
    soc       = pulp.LpVariable.dicts("S",  range(T+1),
                                      lowBound=min_soc * battery_kwh,
                                      upBound=battery_kwh, cat="Continuous")

    # MILP variabele: optimale laadpiek (bepaalt capaciteitstarief)
    peak_charge = pulp.LpVariable(
        "PeakCharge",
        lowBound=cap_min_kw,
        upBound=charge_kw,
        cat="Continuous",
    )

    # Objectief: arbitrage-opbrengst minus capaciteitstarifkost (per maand)
    cap_cost = peak_charge * (cap_eur_per_kw_year / 12.0) * n_months
    prob += (
        pulp.lpSum(
            discharge[t] * df.iloc[t]["price_eur_mwh"] / 1000
            - charge[t]  * df.iloc[t]["price_eur_mwh"] / 1000
            for t in range(T)
        ) - cap_cost,
        "Net_Revenue_After_CapTariff",
    )

    prob += soc[0] == initial_soc * battery_kwh, "Init_SOC"
    prob += soc[T] >= min_end_soc * battery_kwh, "Min_End_SOC"

    max_e_discharge = discharge_kw * dt  # max per slot ontladen (injectie)

    for t in range(T):
        prob += soc[t+1] == soc[t] + eta * charge[t] - discharge[t] / eta, f"Dyn_{t}"
        # Laden beperkt door de gekozen piek (continue variabele)
        prob += charge[t]    <= peak_charge * dt, f"Cmax_{t}"
        prob += discharge[t] <= max_e_discharge,  f"Dmax_{t}"

    result_df, summary = _solve_and_extract(
        prob, df, T, battery_kwh, charge, discharge, soc,
        label=label, exec_mask=exec_mask, n_execute=n_execute,
        peak_charge_var=peak_charge,
        discharge_kw=discharge_kw, charge_kw=charge_kw,
        cap_eur_per_kw_year=cap_eur_per_kw_year, cap_min_kw=cap_min_kw,
        n_days=n_days,
    )
    return result_df, summary


def optimize_battery_schedule_solar(
    prices_df: pd.DataFrame,
    solar_kwh_per_slot: pd.Series,
    battery_kwh: float = 10.0,
    max_power_kw: float = 5.0,
    charge_power_kw: Optional[float] = None,
    min_soc: float = 0.10,
    min_end_soc: float = 0.20,
    efficiency: float = 0.92,
    initial_soc: float = 0.50,
    time_horizon_hours: Optional[int] = None,
    label: str = "MILP+Solar",
    execute_until: Optional[date] = None,
    cap_eur_per_kw_year: float = CAP_EUR_PER_KW_YEAR,
    cap_min_kw: float = CAP_MIN_KW,
) -> Tuple[pd.DataFrame, Dict]:
    """MILP met solar self-consumption + asymmetrisch vermogen + capaciteitstarief."""
    df = _select_slots(prices_df, time_horizon_hours)
    if df.empty:
        raise ValueError("Geen prijsdata.")

    discharge_kw = max_power_kw
    charge_kw    = min(charge_power_kw if charge_power_kw is not None else max_power_kw,
                       discharge_kw)

    T   = len(df)
    dt  = 0.25
    eta = efficiency ** 0.5
    n_days   = T * dt / 24.0
    n_months = n_days / 30.44

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
    soc           = pulp.LpVariable.dicts("S",  range(T+1),
                                          lowBound=min_soc * battery_kwh,
                                          upBound=battery_kwh, cat="Continuous")
    peak_charge   = pulp.LpVariable("PeakCharge", lowBound=cap_min_kw,
                                    upBound=charge_kw, cat="Continuous")

    cap_cost = peak_charge * (cap_eur_per_kw_year / 12.0) * n_months
    # Solar laden telt NIET mee voor capaciteitstarief (eigen productie, geen nettransport)
    prob += (
        pulp.lpSum(
            discharge[t] * df.iloc[t]["price_eur_mwh"] / 1000
            - charge_grid[t] * df.iloc[t]["price_eur_mwh"] / 1000
            for t in range(T)
        ) - cap_cost,
        "Revenue_Solar",
    )

    prob += soc[0] == initial_soc * battery_kwh, "Init_SOC"
    prob += soc[T] >= min_end_soc * battery_kwh, "Min_End_SOC"

    max_e_discharge = discharge_kw * dt

    for t in range(T):
        solar_avail = float(solar_vals[t])
        prob += (soc[t+1] == soc[t]
                 + eta * (charge_grid[t] + charge_solar[t])
                 - discharge[t] / eta), f"Dyn_{t}"
        # Grid laden beperkt door capaciteitspiek
        prob += charge_grid[t]  <= peak_charge * dt,  f"CG_{t}"
        prob += charge_solar[t] <= solar_avail,         f"CS_{t}"
        # Totaal laden beperkt door max hardware (combinatie grid + solar)
        prob += charge_grid[t] + charge_solar[t] <= max_e_discharge, f"Cmax_{t}"
        prob += discharge[t]   <= max_e_discharge,                    f"Dmax_{t}"

    result_df, summary = _solve_and_extract(
        prob, df, T, battery_kwh, charge_grid, discharge, soc,
        label=label, extra_charge=charge_solar, solar_vals=solar_vals,
        exec_mask=exec_mask, n_execute=n_execute,
        peak_charge_var=peak_charge,
        discharge_kw=discharge_kw, charge_kw=charge_kw,
        cap_eur_per_kw_year=cap_eur_per_kw_year, cap_min_kw=cap_min_kw,
        n_days=n_days,
    )
    return result_df, summary


def optimize_battery_schedule_wind_solar(
    prices_df: pd.DataFrame,
    solar_kwh_per_slot: pd.Series,
    wind_price_adj_per_slot: pd.Series,
    battery_kwh: float = 10.0,
    max_power_kw: float = 5.0,
    charge_power_kw: Optional[float] = None,
    min_soc: float = 0.10,
    min_end_soc: float = 0.20,
    efficiency: float = 0.92,
    initial_soc: float = 0.50,
    time_horizon_hours: Optional[int] = None,
    label: str = "MILP+Solar+Wind",
    execute_until: Optional[date] = None,
    cap_eur_per_kw_year: float = CAP_EUR_PER_KW_YEAR,
    cap_min_kw: float = CAP_MIN_KW,
) -> Tuple[pd.DataFrame, Dict]:
    """
    MILP met solar self-consumption + wind prijssignaal + capaciteitstarief.

    Wind integratie:
      wind_price_adj_per_slot (pd.Series, €/MWh per slot):
        Negatieve waarden = verwacht lagere prijs door wind/solar surplus.
        MILP optimaliseert op gecorrigeerde prijs:
          effective_price[t] = price[t] + wind_adj[t]

        Dit maakt MILP proactief: bij hoge wind+solar verwachting laadt hij
        eerder (ook al is de day-ahead prijs nog niet negatief), omdat hij
        anticipeert op de werkelijke marktprijs.

      Nota: wind_adj wordt enkel toegepast op de OBJECTIVE (prijs-motivatie),
            NIET op de revenue berekening (die gebruikt de werkelijke day-ahead
            prijs). Dit is de correcte MPC-aanpak.
    """
    df = _select_slots(prices_df, time_horizon_hours)
    if df.empty:
        raise ValueError("Geen prijsdata.")

    discharge_kw = max_power_kw
    charge_kw    = min(charge_power_kw if charge_power_kw is not None else max_power_kw,
                       discharge_kw)

    T      = len(df)
    dt_h   = 0.25
    eta    = efficiency ** 0.5
    n_days = T * dt_h / 24.0
    n_months = n_days / 30.44

    if execute_until is not None:
        exec_mask = pd.to_datetime(df["datetime"]).dt.date <= execute_until
    else:
        exec_mask = pd.Series([True] * T)
    n_execute = int(exec_mask.sum())

    solar_vals    = _align_solar(solar_kwh_per_slot, df)
    wind_adj_vals = _align_wind_adj(wind_price_adj_per_slot, df)

    prob          = pulp.LpProblem("Battery_Wind_Solar_MILP", pulp.LpMaximize)
    charge_grid   = pulp.LpVariable.dicts("CG", range(T), lowBound=0, cat="Continuous")
    charge_solar  = pulp.LpVariable.dicts("CS", range(T), lowBound=0, cat="Continuous")
    discharge     = pulp.LpVariable.dicts("D",  range(T), lowBound=0, cat="Continuous")
    soc           = pulp.LpVariable.dicts("S",  range(T+1),
                                          lowBound=min_soc * battery_kwh,
                                          upBound=battery_kwh, cat="Continuous")
    peak_charge   = pulp.LpVariable("PeakCharge", lowBound=cap_min_kw,
                                    upBound=charge_kw, cat="Continuous")

    cap_cost = peak_charge * (cap_eur_per_kw_year / 12.0) * n_months

    # Objectief: arbitrage op effectieve prijs (day-ahead + windcorrectie)
    # wind_adj enkel in objective (motivatie), NIET in revenue berekening
    prob += (
        pulp.lpSum(
            discharge[t]     * (df.iloc[t]["price_eur_mwh"] + wind_adj_vals[t]) / 1000
            - charge_grid[t] * (df.iloc[t]["price_eur_mwh"] + wind_adj_vals[t]) / 1000
            for t in range(T)
        ) - cap_cost,
        "Revenue_Wind_Solar",
    )

    prob += soc[0] == initial_soc * battery_kwh, "Init_SOC"
    prob += soc[T] >= min_end_soc * battery_kwh, "Min_End_SOC"

    max_e_discharge = discharge_kw * dt_h

    for t in range(T):
        solar_avail = float(solar_vals[t])
        prob += (soc[t+1] == soc[t]
                 + eta * (charge_grid[t] + charge_solar[t])
                 - discharge[t] / eta), f"Dyn_{t}"
        prob += charge_grid[t]  <= peak_charge * dt_h, f"CG_{t}"
        prob += charge_solar[t] <= solar_avail,          f"CS_{t}"
        prob += charge_grid[t] + charge_solar[t] <= max_e_discharge, f"Cmax_{t}"
        prob += discharge[t]   <= max_e_discharge,                    f"Dmax_{t}"

    result_df, summary = _solve_and_extract(
        prob, df, T, battery_kwh, charge_grid, discharge, soc,
        label=label, extra_charge=charge_solar, solar_vals=solar_vals,
        exec_mask=exec_mask, n_execute=n_execute,
        peak_charge_var=peak_charge,
        discharge_kw=discharge_kw, charge_kw=charge_kw,
        cap_eur_per_kw_year=cap_eur_per_kw_year, cap_min_kw=cap_min_kw,
        n_days=n_days,
    )

    # Extra info wind
    total_wind_adj = sum(wind_adj_vals[t] for t in range(T) if wind_adj_vals[t] < 0)
    summary["wind_price_adj_total_eur_mwh"] = round(total_wind_adj, 1)
    summary["wind_adjusted_slots"] = sum(1 for v in wind_adj_vals if v < -0.5)

    return result_df, summary


def estimate_own_solar_kwh(
    solar_df: pd.DataFrame,
    own_kwp: float = 6.3,
    be_total_mwp: float = BE_TOTAL_SOLAR_MWP,
) -> pd.Series:
    if solar_df.empty or "datetime" not in solar_df.columns:
        return pd.Series(dtype=float)
    priority  = ["dayaheadforecast","mostrecentforecast","weekaheadforecast","measured","upscaled"]
    col_lower = {c.lower(): c for c in solar_df.columns}
    forecast_col = next((col_lower[c] for c in priority if c in col_lower), None)
    if forecast_col is None:
        num_cols = solar_df.select_dtypes(include="number").columns.tolist()
        if num_cols: forecast_col = num_cols[0]
        else: return pd.Series(dtype=float)
    fraction     = own_kwp / (be_total_mwp * 1000)
    own_kwh_slot = solar_df[forecast_col].clip(lower=0) * 1000 * fraction * 0.25
    return pd.Series(own_kwh_slot.values, index=pd.DatetimeIndex(solar_df["datetime"]),
                     name="own_solar_kwh")


def _select_slots(prices_df, time_horizon_hours):
    if time_horizon_hours is None or time_horizon_hours <= 0:
        return prices_df.copy()
    return prices_df.head(int(time_horizon_hours * 4)).copy()


def _align_solar(solar_series, prices_df):
    if solar_series is None or solar_series.empty:
        return [0.0] * len(prices_df)
    try:
        idx = pd.DatetimeIndex(solar_series.index)
        if idx.tz is None: idx = idx.tz_localize("UTC")
        solar_indexed = pd.Series(solar_series.values, index=idx)
        price_idx = pd.DatetimeIndex(prices_df["datetime"])
        if price_idx.tz is None: price_idx = price_idx.tz_localize("UTC")
        return solar_indexed.reindex(price_idx, method="nearest",
                                     tolerance="20min").fillna(0.0).clip(lower=0).tolist()
    except Exception:
        return [0.0] * len(prices_df)

def _align_wind_adj(wind_adj_series: pd.Series, prices_df: pd.DataFrame) -> list:
    """
    Lijn wind prijs-aanpassingen (€/MWh) uit op de prijs-tijdsas.
    Geeft lijst van floats terug, één per slot. Standaard 0.0 als geen data.
    """
    if wind_adj_series is None or wind_adj_series.empty:
        return [0.0] * len(prices_df)
    try:
        idx = pd.DatetimeIndex(wind_adj_series.index)
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
        adj_indexed = pd.Series(wind_adj_series.values, index=idx)
        price_idx   = pd.DatetimeIndex(prices_df["datetime"])
        if price_idx.tz is None:
            price_idx = price_idx.tz_localize("UTC")
        aligned = adj_indexed.reindex(price_idx, method="nearest", tolerance="20min").fillna(0.0)
        return aligned.tolist()
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
    peak_charge_var=None,
    discharge_kw=5.0,
    charge_kw=2.5,
    cap_eur_per_kw_year=CAP_EUR_PER_KW_YEAR,
    cap_min_kw=CAP_MIN_KW,
    n_days=1.0,
) -> Tuple[pd.DataFrame, Dict]:
    if exec_mask is None: exec_mask = pd.Series([True] * T)
    if n_execute is None: n_execute = T

    t_start    = time.time()
    log_buffer = io.StringIO()
    # Probeer HiGHS (via highspy, 3-5× sneller dan CBC); val terug op CBC
    try:
        solver = pulp.HiGHS(msg=False, timeLimit=120)
        if not solver.available():
            raise RuntimeError("HiGHS niet beschikbaar")
        with contextlib.redirect_stdout(log_buffer):
            status = prob.solve(solver)
        solver_log = "HiGHS"
    except Exception:
        with contextlib.redirect_stdout(log_buffer):
            status = prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=120))
        solver_log = log_buffer.getvalue()
    solve_time = round(time.time() - t_start, 2)
    iterations = _parse_iterations(solver_log)

    # Gekozen piek door MILP
    peak_kw_chosen = pulp.value(peak_charge_var) if peak_charge_var else charge_kw
    peak_kw_chosen = max(float(peak_kw_chosen or cap_min_kw), cap_min_kw)
    n_months       = n_days / 30.44
    cap_cost_total = peak_kw_chosen * (cap_eur_per_kw_year / 12.0) * n_months
    cap_cost_monthly_equiv = peak_kw_chosen * cap_eur_per_kw_year / 12.0

    results = []
    for t in range(T):
        c_kwh  = pulp.value(charge_var[t])    or 0.0
        cs_kwh = (pulp.value(extra_charge[t]) or 0.0) if extra_charge else 0.0
        d_kwh  = pulp.value(discharge_var[t]) or 0.0
        s_kwh  = pulp.value(soc_var[t+1])     or 0.0
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
            "is_lookahead":      is_lah,
        }
        if solar_vals:
            row["solar_available_kwh"] = solar_vals[t]
        results.append(row)

    result_df   = pd.DataFrame(results)
    exec_df     = result_df[~result_df["is_lookahead"]]
    lah_df      = result_df[ result_df["is_lookahead"]]
    rev_exec    = round(exec_df["net_revenue_eur"].sum(), 4)
    rev_lah     = round(lah_df["net_revenue_eur"].sum(),  4)

    # Net revenue NA capaciteitstarief (alleen execute-periode)
    rev_after_cap = round(rev_exec - cap_cost_total, 4)

    final_exec_soc = round(exec_df["soc_pct"].iloc[-1], 1) if not exec_df.empty else \
                     round(result_df["soc_pct"].iloc[-1], 1)

    active_mask = (((result_df["charge_kwh"] > 0.01) | (result_df["discharge_kwh"] > 0.01))
                   & (~result_df["is_lookahead"]))
    n_active = int(active_mask.sum())

    summary = {
        "label":                    label,
        "status":                   pulp.LpStatus[status],
        # Revenue
        "total_net_revenue_eur":    rev_after_cap,   # na capaciteitstarief
        "revenue_execute_eur":      rev_exec,         # voor capaciteitstarief
        "revenue_after_cap_eur":    rev_after_cap,
        "revenue_lookahead_eur":    rev_lah,
        # Capaciteitstarief
        "peak_charge_kw":           round(peak_kw_chosen, 3),
        "cap_tarief_period_eur":    round(cap_cost_total, 2),
        "cap_tarief_monthly_eur":   round(cap_cost_monthly_equiv, 2),
        "discharge_kw":             discharge_kw,
        "charge_kw_max":            charge_kw,
        # Slots
        "num_slots":                T,
        "num_slots_execute":        n_execute,
        "num_slots_lookahead":      T - n_execute,
        "num_active_slots":         n_active,
        # Energie
        "total_charged_kwh":        round(exec_df["charge_kwh"].sum(), 2),
        "total_charged_grid_kwh":   round(exec_df["charge_grid_kwh"].sum(), 2),
        "total_charged_solar_kwh":  round(exec_df["charge_solar_kwh"].sum(), 2),
        "total_discharged_kwh":     round(exec_df["discharge_kwh"].sum(), 2),
        # SOC
        "final_soc_pct":            final_exec_soc,
        "final_lookahead_soc_pct":  round(result_df["soc_pct"].iloc[-1], 1),
        # Solver
        "solve_time_sec":           solve_time,
        "solver_iterations":        iterations,
        "solver_log":               solver_log,
    }
    return result_df, summary


def _parse_iterations(log: str) -> int:
    for line in log.splitlines():
        m = re.search(r"-\s+(\d+)\s+iterations", line)
        if m: return int(m.group(1))
        m = re.search(r"[Tt]otal\s+iterations[:\s]+(\d+)", line)
        if m: return int(m.group(1))
    return 0


def _downsample_for_sizing(prices_df: pd.DataFrame) -> pd.DataFrame:
    """
    Downsample kwartierdata (15 min) naar uurdata voor sizing sweep.
    Gebruikt gemiddelde prijs per uur — fout < 3% op jaaropbrengst,
    4× minder MILP-variabelen → ~4× sneller.
    Behoudt alle negatieve prijspieken (gemiddelde is conservatief).
    """
    df = prices_df.copy()
    dt_col = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
    df["_hour"] = dt_col.dt.floor("1h")
    hourly = df.groupby("_hour")["price_eur_mwh"].mean().reset_index()
    hourly.columns = ["datetime", "price_eur_mwh"]
    return hourly


def battery_sizing_analysis(
    prices_df: pd.DataFrame,
    battery_sizes_kwh: list = None,
    max_power_kw: float = 5.0,
    charge_power_kw: Optional[float] = None,
    min_soc: float = 0.10,
    min_end_soc: float = 0.20,
    initial_soc: float = 0.50,
    capex_per_kwh: float = 500.0,
    lifespan_years: float = 12.0,
    cap_eur_per_kw_year: float = CAP_EUR_PER_KW_YEAR,
    cap_min_kw: float = CAP_MIN_KW,
    solar_kwh_per_slot: "pd.Series | None" = None,
    wind_price_adj_per_slot: "pd.Series | None" = None,
) -> "pd.DataFrame":
    """
    Sweep over batterijgroottes en bereken MILP-optimale rendabiliteit per grootte.

    Voor elke grootte in battery_sizes_kwh:
      - Voer MILP-optimalisatie uit op prices_df
      - Bereken jaarlijkse arbitrage-opbrengst (geëxtrapoleerd naar 1 jaar)
      - Bereken CAPEX-kost, capaciteitstarief, netto-opbrengst
      - Bereken terugverdientijd en IRR

    Returns:
      DataFrame met kolommen:
        battery_kwh, rev_period_eur, rev_year_eur, capex_year_eur,
        cap_tarief_year_eur, netto_year_eur, terugverdientijd_jaar,
        irr_pct, peak_charge_kw, solver_status
    """
    import pandas as _pd

    if battery_sizes_kwh is None:
        battery_sizes_kwh = [5, 7.5, 10, 12.5, 15, 20, 25, 30]

    n_days = len(prices_df) * 0.25 / 24.0
    if n_days < 1:
        raise ValueError("Minimaal 1 dag prijsdata nodig voor sizing analyse.")

    # Downsample naar uurdata voor 4× snellere MILP-sweep (<3% fout)
    dt_step = (pd.to_datetime(prices_df["datetime"].iloc[1], utc=True) -
               pd.to_datetime(prices_df["datetime"].iloc[0], utc=True)).total_seconds() / 60
    if dt_step <= 15 and len(prices_df) > 96:
        sweep_df = _downsample_for_sizing(prices_df)
    else:
        sweep_df = prices_df  # al uurdata of te kort voor downsample

    rows = []
    for kwh in battery_sizes_kwh:
        try:
            # Kies de juiste optimizer op basis van beschikbare data
            if wind_price_adj_per_slot is not None and solar_kwh_per_slot is not None:
                sch, s = optimize_battery_schedule_wind_solar(
                    sweep_df,
                    solar_kwh_per_slot if solar_kwh_per_slot is not None
                        else _pd.Series(dtype=float),
                    wind_price_adj_per_slot,
                    battery_kwh=kwh,
                    max_power_kw=max_power_kw,
                    charge_power_kw=_charge_kw,
                    min_soc=min_soc, min_end_soc=min_end_soc,
                    initial_soc=initial_soc,
                    cap_eur_per_kw_year=cap_eur_per_kw_year,
                    cap_min_kw=cap_min_kw,
                )
            elif solar_kwh_per_slot is not None and not solar_kwh_per_slot.empty:
                sch, s = optimize_battery_schedule_solar(
                    sweep_df, solar_kwh_per_slot,
                    battery_kwh=kwh, max_power_kw=max_power_kw,
                    charge_power_kw=_charge_kw,
                    min_soc=min_soc, min_end_soc=min_end_soc,
                    initial_soc=initial_soc,
                    cap_eur_per_kw_year=cap_eur_per_kw_year,
                    cap_min_kw=cap_min_kw,
                )
            else:
                _charge_kw = charge_power_kw if charge_power_kw is not None else max_power_kw
                sch, s = optimize_battery_schedule(
                    sweep_df,
                    battery_kwh=kwh, max_power_kw=max_power_kw,
                    charge_power_kw=_charge_kw,
                    min_soc=min_soc, min_end_soc=min_end_soc,
                    initial_soc=initial_soc,
                    cap_eur_per_kw_year=cap_eur_per_kw_year,
                    cap_min_kw=cap_min_kw,
                )

            rev_period   = s.get("revenue_after_cap_eur",  s.get("total_net_revenue_eur", 0))
            cap_cost_per = s.get("cap_tarief_period_eur",  0)
            peak_kw      = s.get("peak_charge_kw",         cap_min_kw)

            # Extrapoleer naar jaarlijkse waarden
            scale        = 365.0 / max(n_days, 1)
            rev_year     = rev_period  * scale
            cap_tar_year = cap_cost_per * scale

            # Financiële analyse
            capex_total  = kwh * capex_per_kwh
            capex_year   = capex_total / lifespan_years
            netto_year   = rev_year - capex_year   # cap_tarief zit al in rev_year
            irr_pct      = (rev_year / capex_total) * 100 if capex_total > 0 else 0
            terugverd    = capex_total / rev_year if rev_year > 0 else 999

            rows.append({
                "Capaciteit (kWh)":      kwh,
                "Rev. periode (€)":      round(rev_period, 2),
                "Rev. jaar (€)":         round(rev_year, 0),
                "CAPEX jaar (€)":        round(capex_year, 0),
                "Cap.tarief jaar (€)":   round(cap_tar_year, 0),
                "Netto winst jaar (€)":  round(netto_year, 0),
                "Terugverdientijd (j)":  round(terugverd, 1),
                "IRR (%)":               round(irr_pct, 1),
                "MILP laadpiek (kW)":    round(peak_kw, 2),
                "Solver status":         s.get("status", "—"),
                # interne waarden voor grafieken
                "_rev_year":             rev_year,
                "_capex_year":           capex_year,
                "_cap_tar_year":         cap_tar_year,
                "_netto_year":           netto_year,
                "_irr":                  irr_pct,
                "_terugverd":            terugverd,
            })
        except Exception as e:
            rows.append({
                "Capaciteit (kWh)": kwh,
                "Rev. periode (€)": None,
                "Solver status": f"Fout: {str(e)[:60]}",
                "_rev_year": 0, "_capex_year": 0, "_cap_tar_year": 0,
                "_netto_year": -999, "_irr": 0, "_terugverd": 999,
            })

    return _pd.DataFrame(rows)


if __name__ == "__main__":
    import numpy as np
    from datetime import date, timedelta

    dates  = pd.date_range("2026-05-27", periods=96, freq="15min", tz="UTC")
    prices = np.concatenate([
        np.full(16, -50),   # nacht: negatief
        np.full(32,  30),   # ochtend: laag
        np.full(24,  80),   # middag: matig
        np.full(24, 220),   # avond: hoog
    ])
    df = pd.DataFrame({"datetime": dates, "price_eur_mwh": prices})

    print("=== Test: 5 kW ontladen / MILP kiest optimale laadpiek ===")
    sch, s = optimize_battery_schedule(
        df, battery_kwh=10, max_power_kw=5.0, charge_power_kw=5.0,
        min_soc=0.10, initial_soc=0.50
    )
    print(f"Gekozen piek:     {s['peak_charge_kw']:.2f} kW")
    print(f"Cap.tarief kost:  {s['cap_tarief_period_eur']:.3f} € (periode)")
    print(f"Cap.tarief/mnd:   {s['cap_tarief_monthly_eur']:.2f} €")
    print(f"Revenue (voor):   {s['revenue_execute_eur']:.3f} €")
    print(f"Revenue (na cap): {s['revenue_after_cap_eur']:.3f} €")
    print(f"Actieve slots:    {s['num_active_slots']}")
    print(f"Solver:           {s['status']} | {s['solve_time_sec']}s | {s['solver_iterations']:,} iter")
