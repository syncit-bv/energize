#!/usr/bin/env python3
"""
MILP Optimizer for EMS Belgium
Maximizes net revenue from battery arbitrage on Belgian day-ahead prices
while respecting minimum SOC reserve (e.g. 10%).
"""

import pandas as pd
import pulp
from typing import Dict, Tuple


def optimize_battery_schedule(
    prices_df: pd.DataFrame,
    battery_kwh: float = 10.0,
    max_power_kw: float = 5.0,
    min_soc: float = 0.10,
    min_end_soc: float = 0.20,
    efficiency: float = 0.92,
    initial_soc: float = 0.50,
    time_horizon_hours: int = 24
) -> Tuple[pd.DataFrame, Dict]:
    """
    Solve MILP to find optimal charge/discharge schedule.
    Returns a DataFrame with optimal actions + summary dict.
    """

    # Prepare data for the chosen horizon (use first N slots or full day)
    df = prices_df.head(int(time_horizon_hours * 4)).copy()  # 4 quarters per hour
    if len(df) == 0:
        raise ValueError("No price data provided")

    T = len(df)  # number of 15-min slots
    dt = 0.25    # hours per slot

    # Create the MILP problem
    prob = pulp.LpProblem("Battery_Arbitrage_MILP", pulp.LpMaximize)

    # Decision variables
    charge = pulp.LpVariable.dicts("Charge", range(T), lowBound=0, cat="Continuous")
    discharge = pulp.LpVariable.dicts("Discharge", range(T), lowBound=0, cat="Continuous")
    soc = pulp.LpVariable.dicts("SOC", range(T+1), lowBound=min_soc * battery_kwh, 
                                upBound=battery_kwh, cat="Continuous")

    # Objective: maximize net revenue = income from discharge - cost of charge
    revenue = pulp.lpSum([
        discharge[t] * df.iloc[t]['price_eur_mwh'] / 1000   # discharge gives income
        - charge[t] * df.iloc[t]['price_eur_mwh'] / 1000    # charge costs money (negative when price < 0)
        for t in range(T)
    ])
    prob += revenue, "Net_Revenue"

    # Initial SOC
    prob += soc[0] == initial_soc * battery_kwh, "Initial_SOC"

    # Dynamics + constraints per time slot
    max_energy_per_slot = max_power_kw * dt

    for t in range(T):
        # SOC evolution (round-trip efficiency applied on charge and discharge)
        eta = efficiency ** 0.5   # split efficiency for charge and discharge
        prob += soc[t+1] == soc[t] + eta * charge[t] - discharge[t] / eta, f"SOC_dynamics_{t}"

        # Power limits
        prob += charge[t] <= max_energy_per_slot, f"Max_charge_{t}"
        prob += discharge[t] <= max_energy_per_slot, f"Max_discharge_{t}"

        # SOC bounds already set in variable definition (min_soc and max)

    # Terminal SOC constraint (end of horizon) - prevents ending at absolute minimum
    prob += soc[T] >= min_end_soc * battery_kwh, "Min_End_SOC"

    # Solve
    status = prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=30))

    if pulp.LpStatus[status] != "Optimal":
        print(f"Warning: MILP status = {pulp.LpStatus[status]}")

    # Extract results
    results = []
    for t in range(T):
        results.append({
            'datetime': df.iloc[t]['datetime'],
            'price_eur_mwh': df.iloc[t]['price_eur_mwh'],
            'charge_kwh': pulp.value(charge[t]),
            'discharge_kwh': pulp.value(discharge[t]),
            'soc_kwh': pulp.value(soc[t+1]),
            'soc_pct': pulp.value(soc[t+1]) / battery_kwh * 100,
            'net_revenue_eur': pulp.value(discharge[t] * df.iloc[t]['price_eur_mwh'] / 1000 
                                          - charge[t] * df.iloc[t]['price_eur_mwh'] / 1000)
        })

    result_df = pd.DataFrame(results)
    total_net_revenue = pulp.value(prob.objective)

    summary = {
        'total_net_revenue_eur': round(total_net_revenue, 2),
        'total_charged_kwh': round(result_df['charge_kwh'].sum(), 1),
        'total_discharged_kwh': round(result_df['discharge_kwh'].sum(), 1),
        'final_soc_pct': round(result_df['soc_pct'].iloc[-1], 1),
        'status': pulp.LpStatus[status],
        'min_soc_used': min_soc * 100
    }

    return result_df, summary


if __name__ == "__main__":
    # Quick test on historical data
    from price_parser import parse_entsoe_prices

    df = parse_entsoe_prices("/home/workdir/attachments/Energy_Prices_202512312300-202612312300.xml")

    # Test on 1 May 2026 (famous negative price day)
    may1 = df[(df['datetime'].dt.date == pd.to_datetime("2026-05-01").date())].copy()

    print("Running MILP on 1 May 2026 (extreme negative prices)...")
    schedule, summary = optimize_battery_schedule(
        may1,
        battery_kwh=10.0,
        max_power_kw=5.0,
        min_soc=0.10,
        initial_soc=0.50
    )

    print("\n=== MILP Optimization Summary ===")
    for k, v in summary.items():
        print(f"{k}: {v}")

    print("\nSample of optimal schedule (first 8 hours):")
    print(schedule[['datetime', 'price_eur_mwh', 'charge_kwh', 'discharge_kwh', 'soc_pct']].head(32).to_string())