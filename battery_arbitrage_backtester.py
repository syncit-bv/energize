#!/usr/bin/env python3
"""
EMS MVP - Simple Rule-Based Battery Arbitrage Backtester
Focus: Belgian day-ahead prices + battery storage for grid disbalance monetization
Shows potential of "charge free/paid electricity" and earn on high prices.
"""

import pandas as pd
import numpy as np
from datetime import datetime
import matplotlib.pyplot as plt
from pathlib import Path

# ============== CONFIG (easy to tune for MVP) ==============
BATTERY_USABLE_KWH = 10.0          # Realistic home battery (e.g. Tesla Powerwall usable)
MAX_POWER_KW = 5.0                 # Charge/discharge power limit
ROUNDTRIP_EFF = 0.92               # Realistic round-trip efficiency
START_SOC = 0.5                    # Start at 50%

# Strategy thresholds (tune these)
THRESH_CHARGE = 50.0               # €/MWh - charge below this
THRESH_DISCHARGE = 160.0           # €/MWh - discharge above this
NEGATIVE_BOOST = True              # Extra aggressive charge when negative

# Simulation period (focus on high-opportunity window)
SIM_START = "2026-04-25"
SIM_END = "2026-05-03"

OUTPUT_DIR = Path("/home/workdir/artifacts/ems_mvp")
OUTPUT_DIR.mkdir(exist_ok=True)


def simulate_battery_strategy(df: pd.DataFrame) -> pd.DataFrame:
    """Run rule-based charge/discharge simulation on price series."""
    df = df.copy()
    df = df[(df['datetime'] >= SIM_START) & (df['datetime'] <= SIM_END)].reset_index(drop=True)

    # Battery state
    soc = START_SOC
    capacity_mwh = BATTERY_USABLE_KWH / 1000.0
    max_energy_per_slot_mwh = (MAX_POWER_KW * 0.25) / 1000.0  # 15 min slot

    results = []
    cumulative_revenue = 0.0
    cumulative_energy_charged = 0.0
    cumulative_energy_discharged = 0.0

    for idx, row in df.iterrows():
        price = row['price_eur_mwh']
        action = "HOLD"
        energy_mwh = 0.0
        revenue = 0.0

        # Decision logic (MVP rule-based - later replace with optimization)
        if price < 0 and NEGATIVE_BOOST:
            # Charge max when we get PAID to take electricity (free + credit)
            charge_possible = min(max_energy_per_slot_mwh, (1 - soc) * capacity_mwh / 0.96)
            if charge_possible > 0.0001:
                energy_mwh = charge_possible
                soc += energy_mwh * 0.96 / capacity_mwh
                revenue = -energy_mwh * price   # negative price = positive revenue (you get paid to charge)
                action = "CHARGE (NEGATIVE)"
                cumulative_energy_charged += energy_mwh * 1000  # kWh

        elif price < THRESH_CHARGE:
            charge_possible = min(max_energy_per_slot_mwh, (1 - soc) * capacity_mwh / 0.96)
            if charge_possible > 0.0001:
                energy_mwh = charge_possible
                soc += energy_mwh * 0.96 / capacity_mwh
                revenue = -energy_mwh * price
                action = "CHARGE"
                cumulative_energy_charged += energy_mwh * 1000

        elif price > THRESH_DISCHARGE:
            discharge_possible = min(max_energy_per_slot_mwh, soc * capacity_mwh * 0.96)
            if discharge_possible > 0.0001:
                energy_mwh = discharge_possible
                soc -= energy_mwh / (capacity_mwh * 0.96)
                revenue = energy_mwh * price
                action = "DISCHARGE"
                cumulative_energy_discharged += energy_mwh * 1000

        cumulative_revenue += revenue

        results.append({
            'datetime': row['datetime'],
            'price_eur_mwh': price,
            'action': action,
            'energy_kwh': energy_mwh * 1000,
            'revenue_eur': revenue,
            'soc': soc * 100,
            'cumulative_revenue_eur': cumulative_revenue,
            'cumulative_charged_kwh': cumulative_energy_charged,
            'cumulative_discharged_kwh': cumulative_energy_discharged
        })

    return pd.DataFrame(results)


def print_summary(sim_df: pd.DataFrame, battery_kwh: float):
    """Print key performance metrics."""
    total_revenue = sim_df['cumulative_revenue_eur'].iloc[-1]
    total_charged = sim_df['cumulative_charged_kwh'].iloc[-1]
    total_discharged = sim_df['cumulative_discharged_kwh'].iloc[-1]
    avg_soc = sim_df['soc'].mean()

    print("\n" + "="*60)
    print("EMS MVP - BATTERY ARBITRAGE BACKTEST RESULTS")
    print("="*60)
    print(f"Period: {sim_df['datetime'].min()} → {sim_df['datetime'].max()}")
    print(f"Battery: {battery_kwh} kWh usable | Max power: {MAX_POWER_KW} kW | Eff: {ROUNDTRIP_EFF*100:.0f}%")
    print(f"Strategy: Charge < {THRESH_CHARGE} €/MWh | Discharge > {THRESH_DISCHARGE} €/MWh | Negative boost: {NEGATIVE_BOOST}")
    print("-"*60)
    print(f"Total net revenue:          {total_revenue:>10.2f} €")
    print(f"Total energy charged:       {total_charged:>10.1f} kWh")
    print(f"Total energy discharged:    {total_discharged:>10.1f} kWh")
    print(f"Average SOC:                {avg_soc:>10.1f} %")
    print("-"*60)

    # Highlight negative price days
    neg_days = sim_df[sim_df['price_eur_mwh'] < 0]['datetime'].dt.date.unique()
    print(f"Days with negative prices in sim: {len(neg_days)}")
    for d in neg_days:
        day_revenue = sim_df[sim_df['datetime'].dt.date == d]['revenue_eur'].sum()
        print(f"  {d}: {day_revenue:+.2f} € from strategy")

    print("="*60)
    print("Key insight: On days with extreme negative prices (e.g. 1 May 2026),")
    print("the EMS earns money WHILE charging (you get paid to absorb excess solar).")
    print("This is 'free electricity' + grid balancing contribution.")
    print("="*60 + "\n")


def plot_results(sim_df: pd.DataFrame):
    """Generate visual dashboard-style plots."""
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)

    # 1. Price + actions
    ax1 = axes[0]
    ax1.plot(sim_df['datetime'], sim_df['price_eur_mwh'], color='gray', linewidth=0.8, label='Day-ahead price')
    charge_mask = sim_df['action'].str.contains('CHARGE')
    discharge_mask = sim_df['action'] == 'DISCHARGE'
    ax1.scatter(sim_df[charge_mask]['datetime'], sim_df[charge_mask]['price_eur_mwh'],
                color='green', s=30, label='CHARGE (incl. negative)', zorder=5)
    ax1.scatter(sim_df[discharge_mask]['datetime'], sim_df[discharge_mask]['price_eur_mwh'],
                color='red', s=30, label='DISCHARGE', zorder=5)
    ax1.axhline(y=THRESH_CHARGE, color='green', linestyle='--', alpha=0.5, label=f'Charge threshold ({THRESH_CHARGE} €)')
    ax1.axhline(y=THRESH_DISCHARGE, color='red', linestyle='--', alpha=0.5, label=f'Discharge threshold ({THRESH_DISCHARGE} €)')
    ax1.set_ylabel('Price (€/MWh)')
    ax1.set_title('EMS MVP Dashboard - Price + Battery Actions (Belgian Grid)')
    ax1.legend(loc='upper right', fontsize=8)
    ax1.grid(True, alpha=0.3)

    # 2. SOC
    ax2 = axes[1]
    ax2.fill_between(sim_df['datetime'], 0, sim_df['soc'], color='blue', alpha=0.3)
    ax2.plot(sim_df['datetime'], sim_df['soc'], color='blue', linewidth=1.5)
    ax2.axhline(y=20, color='orange', linestyle='--', alpha=0.7)
    ax2.axhline(y=80, color='orange', linestyle='--', alpha=0.7)
    ax2.set_ylabel('State of Charge (%)')
    ax2.set_ylim(0, 105)
    ax2.grid(True, alpha=0.3)

    # 3. Cumulative revenue
    ax3 = axes[2]
    ax3.plot(sim_df['datetime'], sim_df['cumulative_revenue_eur'], color='purple', linewidth=2)
    ax3.fill_between(sim_df['datetime'], 0, sim_df['cumulative_revenue_eur'], color='purple', alpha=0.2)
    ax3.set_ylabel('Cumulative Revenue (€)')
    ax3.set_xlabel('Time (15-min intervals)')
    ax3.grid(True, alpha=0.3)

    plt.tight_layout()
    plot_path = OUTPUT_DIR / "ems_mvp_dashboard_backtest.png"
    plt.savefig(plot_path, dpi=150, bbox_inches='tight')
    print(f"Dashboard plot saved to: {plot_path}")
    plt.close()


if __name__ == "__main__":
    # Load pre-parsed or parse fresh
    parquet_path = OUTPUT_DIR / "prices_belgium.parquet"
    if parquet_path.exists():
        df = pd.read_parquet(parquet_path)
    else:
        from price_parser import parse_entsoe_prices
        df = parse_entsoe_prices("/home/workdir/attachments/Energy_Prices_202512312300-202612312300.xml")

    print(f"Running backtest on {len(df)} price points...")
    sim_df = simulate_battery_strategy(df)

    print_summary(sim_df, BATTERY_USABLE_KWH)
    plot_results(sim_df)

    # Save detailed results
    csv_path = OUTPUT_DIR / "backtest_results.csv"
    sim_df.to_csv(csv_path, index=False)
    print(f"Detailed results saved to: {csv_path}")

    # Quick highlight May 1
    may1 = sim_df[sim_df['datetime'].dt.date == pd.to_datetime("2026-05-01").date()]
    if len(may1) > 0:
        print(f"\nMay 1 2026 specific: Revenue that day = {may1['revenue_eur'].sum():.2f} €")
        print(f"  (This is the day with prices down to -499 €/MWh around midday)")