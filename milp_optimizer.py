#!/usr/bin/env python3
"""
MILP Optimizer for EMS Belgium
Maximizes net revenue from battery arbitrage on Belgian day-ahead prices
while respecting minimum SOC reserve (e.g. 10%).
"""

import io
import contextlib
import time
import pandas as pd
import pulp
from typing import Dict, Tuple, Optional


def optimize_battery_schedule(
    prices_df: pd.DataFrame,
    battery_kwh: float = 10.0,
    max_power_kw: float = 5.0,
    min_soc: float = 0.10,
    min_end_soc: float = 0.20,
    efficiency: float = 0.92,
    initial_soc: float = 0.50,
    time_horizon_hours: Optional[int] = None,   # None = use ALL rows in prices_df
) -> Tuple[pd.DataFrame, Dict]:
    """
    Solve MILP to find optimal charge/discharge schedule.

    Parameters
    ----------
    prices_df         : DataFrame with at least 'datetime' and 'price_eur_mwh' columns.
    battery_kwh       : Usable battery capacity in kWh.
    max_power_kw      : Maximum charge / discharge power in kW.
    min_soc           : Minimum allowed SOC as a fraction (e.g. 0.10 = 10 %).
    min_end_soc       : Minimum SOC at the END of the optimisation horizon (fraction).
    efficiency        : Round-trip efficiency (split symmetrically over charge/discharge).
    initial_soc       : Starting SOC as a fraction (e.g. 0.50 = 50 %).
    time_horizon_hours: Limit the number of 15-min slots used.
                        None (default) → use every row in prices_df (correct for
                        multi-day back-tests triggered from the dashboard).

    Returns
    -------
    (result_df, summary) where summary includes solve_time_sec and solver_iterations.
    """

    # ── Slot selection ────────────────────────────────────────────────────────
    if time_horizon_hours is None or time_horizon_hours <= 0:
        df = prices_df.copy()
    else:
        max_slots = int(time_horizon_hours * 4)
        df = prices_df.head(max_slots).copy()

    if len(df) == 0:
        raise ValueError("No price data provided to optimizer.")

    T  = len(df)   # number of 15-min slots
    dt = 0.25      # hours per slot

    # ── Build MILP problem ────────────────────────────────────────────────────
    prob = pulp.LpProblem("Battery_Arbitrage_MILP", pulp.LpMaximize)

    charge    = pulp.LpVariable.dicts("Charge",    range(T), lowBound=0, cat="Continuous")
    discharge = pulp.LpVariable.dicts("Discharge", range(T), lowBound=0, cat="Continuous")
    soc       = pulp.LpVariable.dicts(
        "SOC", range(T + 1),
        lowBound=min_soc * battery_kwh,
        upBound=battery_kwh,
        cat="Continuous",
    )

    # Objective
    prob += pulp.lpSum(
        discharge[t] * df.iloc[t]["price_eur_mwh"] / 1000
        - charge[t]  * df.iloc[t]["price_eur_mwh"] / 1000
        for t in range(T)
    ), "Net_Revenue"

    # Initial SOC
    prob += soc[0] == initial_soc * battery_kwh, "Initial_SOC"

    max_energy_per_slot = max_power_kw * dt
    eta = efficiency ** 0.5   # split symmetrically

    for t in range(T):
        prob += soc[t + 1] == soc[t] + eta * charge[t] - discharge[t] / eta, f"SOC_dyn_{t}"
        prob += charge[t]    <= max_energy_per_slot, f"Max_charge_{t}"
        prob += discharge[t] <= max_energy_per_slot, f"Max_discharge_{t}"

    # Terminal SOC constraint
    prob += soc[T] >= min_end_soc * battery_kwh, "Min_End_SOC"

    # ── Solve (capture stdout for solver stats) ───────────────────────────────
    t_start    = time.time()
    log_buffer = io.StringIO()

    with contextlib.redirect_stdout(log_buffer):
        status = prob.solve(pulp.PULP_CBC_CMD(msg=True, timeLimit=60))

    solve_time  = round(time.time() - t_start, 2)
    solver_log  = log_buffer.getvalue()

    # Parse Total iterations from CBC log
    iterations = _parse_cbc_iterations(solver_log)

    if pulp.LpStatus[status] not in ("Optimal", "Feasible"):
        print(f"Warning: MILP status = {pulp.LpStatus[status]}")

    # ── Extract results ───────────────────────────────────────────────────────
    results = []
    for t in range(T):
        c_kwh  = pulp.value(charge[t])    or 0.0
        d_kwh  = pulp.value(discharge[t]) or 0.0
        s_kwh  = pulp.value(soc[t + 1])   or 0.0
        p      = df.iloc[t]["price_eur_mwh"]
        rev    = d_kwh * p / 1000 - c_kwh * p / 1000

        results.append({
            "datetime":        df.iloc[t]["datetime"],
            "price_eur_mwh":   p,
            "charge_kwh":      c_kwh,
            "discharge_kwh":   d_kwh,
            "soc_kwh":         s_kwh,
            "soc_pct":         s_kwh / battery_kwh * 100,
            "net_revenue_eur": rev,
        })

    result_df         = pd.DataFrame(results)
    total_net_revenue = pulp.value(prob.objective) or 0.0

    summary = {
        "status":                 pulp.LpStatus[status],
        "total_net_revenue_eur":  round(total_net_revenue, 4),
        "total_charged_kwh":      round(result_df["charge_kwh"].sum(), 2),
        "total_discharged_kwh":   round(result_df["discharge_kwh"].sum(), 2),
        "final_soc_pct":          round(result_df["soc_pct"].iloc[-1], 1),
        "min_soc_used":           min_soc * 100,
        "num_slots":              T,
        "solve_time_sec":         solve_time,
        "solver_iterations":      iterations,
        "solver_log":             solver_log,
    }

    return result_df, summary


def _parse_cbc_iterations(log: str) -> int:
    """
    Extract simplex iterations from CBC solver log.

    CBC writes something like:
      'Optimal objective 22.44 - 322 iterations time 0.01, Presolve 0.00'
    or
      'Total iterations:   322'
    """
    import re
    for line in log.splitlines():
        # Pattern 1: "- N iterations"  (most common CBC format)
        m = re.search(r"-\s+(\d+)\s+iterations", line)
        if m:
            return int(m.group(1))
        # Pattern 2: "Total iterations: N"
        m = re.search(r"[Tt]otal\s+iterations[:\s]+(\d+)", line)
        if m:
            return int(m.group(1))
    return 0


# ── Quick CLI test ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from price_parser import parse_entsoe_prices

    df = parse_entsoe_prices(
        "/home/workdir/attachments/Energy_Prices_202512312300-202612312300.xml"
    )

    may1 = df[df["datetime"].dt.date == pd.to_datetime("2026-05-01").date()].copy()

    print("Running MILP on 1 May 2026 (extreme negative prices)…")
    schedule, summary = optimize_battery_schedule(
        may1, battery_kwh=10.0, max_power_kw=5.0, min_soc=0.10, initial_soc=0.50
    )

    print("\n=== MILP Optimization Summary ===")
    for k, v in summary.items():
        if k != "solver_log":
            print(f"  {k}: {v}")

    print("\nSolver log snippet:")
    print("\n".join(summary["solver_log"].splitlines()[-10:]))

    print("\nSample optimal schedule (first 8 hours):")
    print(
        schedule[["datetime", "price_eur_mwh", "charge_kwh", "discharge_kwh", "soc_pct"]]
        .head(32)
        .to_string()
    )
