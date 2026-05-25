#!/usr/bin/env python3
"""
Electricity Maps API Client for EMS Belgium
Focus: Day-Ahead Electricity Prices (v4 API)

Using the v4 endpoint as requested.
"""

import requests
import pandas as pd
from datetime import datetime, date
from typing import Optional, Dict, Any
import time

BASE_URL = "https://api.electricitymaps.com/v4"  # v4 as per Developer Hub request


class ElectricityMapsClient:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Electricity Maps API key is required.")
        
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "auth-token": api_key,
            "Accept": "application/json"
        })

    def _make_request(self, endpoint: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        
        # Fallback: send key both in header (already set) and as query param
        if params is None:
            params = {}
        params["auth-token"] = self.api_key
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.session.get(url, params=params, timeout=25)
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 429:
                    wait = 2 ** attempt
                    print(f"Rate limit. Waiting {wait}s...")
                    time.sleep(wait)
                    continue
                else:
                    response.raise_for_status()
            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    raise Exception(f"Electricity Maps API error: {e}")
                time.sleep(1)
        raise Exception("Failed after retries")

    def get_day_ahead_prices(
        self, 
        zone: str = "BE", 
        start: Optional[str | date] = None, 
        end: Optional[str | date] = None
    ) -> pd.DataFrame:
        """
        Fetch Day-Ahead Prices.
        Uses the exact endpoint from the Developer Hub Playground: /price-day-ahead/actual
        """
        params = {"zone": zone}
        
        if start:
            if isinstance(start, date) and not isinstance(start, datetime):
                start = datetime.combine(start, datetime.min.time())
            params["start"] = start.strftime("%Y-%m-%dT%H:%M:%S.000Z") if isinstance(start, datetime) else start
        
        if end:
            if isinstance(end, date) and not isinstance(end, datetime):
                end = datetime.combine(end, datetime.min.time())
            params["end"] = end.strftime("%Y-%m-%dT%H:%M:%S.000Z") if isinstance(end, datetime) else end

        data = self._make_request("price-day-ahead/actual", params=params)
        
        if "data" not in data or not data.get("data"):
            return pd.DataFrame(columns=['datetime', 'price_eur_mwh', 'date', 'hour', 'quarter', 'price_eur_kwh'])
        
        records = []
        for item in data["data"]:
            dt = datetime.fromisoformat(item["datetime"].replace("Z", "+00:00"))
            price = float(item["value"])
            
            records.append({
                "datetime": dt,
                "price_eur_mwh": price,
                "date": dt.date(),
                "hour": dt.hour,
                "quarter": (dt.minute // 15) + 1
            })
        
        df = pd.DataFrame(records).sort_values("datetime").reset_index(drop=True)
        df["price_eur_kwh"] = df["price_eur_mwh"] / 1000.0
        return df


if __name__ == "__main__":
    # Test with your Sandbox key
    SANDBOX_KEY = "UYf4kmp5qvGC8B2qjFhc"
    
    client = ElectricityMapsClient(SANDBOX_KEY)
    
    print("Fetching Day-Ahead Prices BE (24-25 May 2026)...")
    df = client.get_day_ahead_prices("BE", "2026-05-24", "2026-05-25")
    
    print(df.head(10))
    print(f"\nTotal: {len(df)} rows | Negative prices: {(df['price_eur_mwh'] < 0).sum()}")