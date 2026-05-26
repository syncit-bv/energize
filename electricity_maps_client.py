#!/usr/bin/env python3
"""
Electricity Maps API Client for EMS Belgium
Endpoint: /v4/price-day-ahead/combined  (actual + forecast, hourly)

Notes:
- Sandbox key gives ~24h of intentionally inaccurate data (integration testing only).
- Production key gives real prices + multi-day forecasts.
- Data is HOURLY → expanded to 4× 15-min slots so MILP works correctly.
- Source is nordpool.com (same as ENTSO-E). For long historical backtests,
  use entsoe_client.py instead (free, unlimited history).
"""

import requests
import pandas as pd
from datetime import datetime, date, timedelta
from typing import Optional
import time


BASE_URL = "https://api.electricitymaps.com/v4"


class ElectricityMapsClient:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Electricity Maps API key is required.")
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "auth-token": api_key,
            "Accept": "application/json",
        })

    def _get(self, endpoint: str, params: Optional[dict] = None) -> dict:
        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        for attempt in range(3):
            try:
                r = self.session.get(url, params=params or {}, timeout=25)
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
            except requests.RequestException as e:
                if attempt == 2:
                    raise Exception(f"Electricity Maps API error: {e}")
                time.sleep(1)
        raise Exception("Electricity Maps: failed after retries")

    # ──────────────────────────────────────────────────────────────────────────
    # Main method: combined day-ahead prices (actual + forecast)
    # ──────────────────────────────────────────────────────────────────────────
    def get_day_ahead_prices(
        self,
        zone: str = "BE",
        start: Optional[date] = None,
        end:   Optional[date] = None,
    ) -> pd.DataFrame:
        """
        Fetch day-ahead prices via /price-day-ahead/combined.

        Returns a DataFrame with 15-min slots (hourly price repeated × 4)
        so it is directly compatible with the MILP optimizer and rule-based sim.

        Columns: datetime, price_eur_mwh, date, hour, quarter, price_eur_kwh, source
        """
        data = self._get("price-day-ahead/combined", params={"zone": zone})

        records = data.get("data") or data.get("prices") or []
        if not records:
            return _empty_df()

        rows = []
        for item in records:
            raw_dt = item.get("datetime") or item.get("timestamp")
            if not raw_dt:
                continue
            dt_hour = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
            price   = float(item["value"])
            src     = item.get("source", "electricitymaps")

            # Expand 1 hourly price → 4 quarter-hour slots (00, 15, 30, 45)
            for q in range(4):
                dt_slot = dt_hour + timedelta(minutes=15 * q)
                rows.append({
                    "datetime":      dt_slot,
                    "price_eur_mwh": price,
                    "date":          dt_slot.date(),
                    "hour":          dt_slot.hour,
                    "quarter":       q + 1,
                    "price_eur_kwh": price / 1000.0,
                    "source":        src,
                })

        df = pd.DataFrame(rows).sort_values("datetime").reset_index(drop=True)

        # Optional date filter (sandbox ignores start/end but production may support it)
        if start:
            df = df[df["date"] >= (start if isinstance(start, date) else start.date())]
        if end:
            df = df[df["date"] <= (end   if isinstance(end,   date) else end.date())]

        return df.reset_index(drop=True)

    def get_carbon_intensity(self, zone: str = "BE") -> dict:
        """Current carbon intensity (gCO₂eq/kWh) — useful for green charging logic."""
        return self._get("carbon-intensity/latest", params={"zone": zone})


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "datetime", "price_eur_mwh", "date", "hour", "quarter",
        "price_eur_kwh", "source",
    ])


# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    SANDBOX_KEY = "UYf4kmp5qvGC8B2qjFhc"
    client = ElectricityMapsClient(SANDBOX_KEY)

    print("Fetching combined day-ahead prices for BE…")
    df = client.get_day_ahead_prices("BE")

    print(f"\nRows returned : {len(df)}  (hourly prices × 4 = 15-min slots)")
    print(f"Period        : {df['datetime'].min()} → {df['datetime'].max()}")
    print(f"Negative slots: {(df['price_eur_mwh'] < 0).sum()}")
    print(f"Sources       : {df['source'].unique()}")
    print(f"\nSample:\n{df[['datetime','price_eur_mwh','quarter','source']].head(12).to_string()}")
