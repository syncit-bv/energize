#!/usr/bin/env python3
"""
Electricity Maps API Client for EMS Belgium
Useful for carbon intensity, forecasts, and price data.

Sandbox / Production API key required.
Documentation: https://docs.electricitymaps.com/

Common useful endpoints for battery optimization:
- Carbon intensity (current + forecast)
- Power breakdown
- Price data (where available)
"""

import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any
import time

BASE_URL = "https://api.electricitymaps.com/v3"


class ElectricityMapsClient:
    def __init__(self, api_key: str, use_sandbox: bool = True):
        """
        Initialize client.
        
        Args:
            api_key: Your Electricity Maps API key (Sandbox or Production)
            use_sandbox: If True, use sandbox endpoint (for testing)
        """
        if not api_key:
            raise ValueError("Electricity Maps API key is required.")
        
        self.api_key = api_key
        self.use_sandbox = use_sandbox
        
        self.session = requests.Session()
        self.session.headers.update({
            "auth-token": api_key,
            "Accept": "application/json"
        })
        
        # Use sandbox base if testing
        if use_sandbox:
            # Sandbox often uses same base but with test key
            self.base_url = BASE_URL
        else:
            self.base_url = BASE_URL

    def _make_request(self, endpoint: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """Internal request handler with basic retry."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        
        max_retries = 2
        for attempt in range(max_retries):
            try:
                response = self.session.get(url, params=params, timeout=20)
                
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
        
        raise Exception("Failed to get valid response from Electricity Maps")

    # ==================== CARBON INTENSITY ====================
    
    def get_carbon_intensity_latest(self, zone: str = "BE") -> Dict[str, Any]:
        """Get latest carbon intensity for a zone (e.g. 'BE' for Belgium)."""
        return self._make_request(f"carbon-intensity/latest", params={"zone": zone})

    def get_carbon_intensity_history(
        self, 
        zone: str = "BE", 
        start: Optional[str] = None, 
        end: Optional[str] = None
    ) -> pd.DataFrame:
        """
        Get historical carbon intensity.
        Returns DataFrame with datetime and carbon intensity (gCO2eq/kWh).
        """
        params = {"zone": zone}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
            
        data = self._make_request("carbon-intensity/history", params=params)
        
        if "history" not in data:
            return pd.DataFrame()
        
        records = []
        for item in data["history"]:
            dt = datetime.fromisoformat(item["datetime"].replace("Z", "+00:00"))
            records.append({
                "datetime": dt,
                "carbon_intensity_gco2_kwh": item.get("carbonIntensity"),
                "zone": zone,
                "is_forecast": False
            })
        
        return pd.DataFrame(records).sort_values("datetime").reset_index(drop=True)

    def get_carbon_intensity_forecast(self, zone: str = "BE") -> pd.DataFrame:
        """Get carbon intensity forecast."""
        data = self._make_request("carbon-intensity/forecast", params={"zone": zone})
        
        if "forecast" not in data:
            return pd.DataFrame()
        
        records = []
        for item in data["forecast"]:
            dt = datetime.fromisoformat(item["datetime"].replace("Z", "+00:00"))
            records.append({
                "datetime": dt,
                "carbon_intensity_gco2_kwh": item.get("carbonIntensity"),
                "zone": zone,
                "is_forecast": True
            })
        
        return pd.DataFrame(records).sort_values("datetime").reset_index(drop=True)

    # ==================== PRICE DATA (if available for zone) ====================
    
    def get_price_latest(self, zone: str = "BE") -> Dict[str, Any]:
        """Get latest electricity price (where available)."""
        try:
            return self._make_request("price/latest", params={"zone": zone})
        except Exception:
            return {"error": "Price data not available for this zone or plan"}

    # ==================== CONVENIENCE ====================
    
    def get_full_insight(self, zone: str = "BE") -> Dict[str, Any]:
        """
        Get a combined view useful for battery optimization decisions.
        Includes current carbon + price (if available).
        """
        result = {
            "zone": zone,
            "timestamp": datetime.now().isoformat()
        }
        
        try:
            result["carbon_latest"] = self.get_carbon_intensity_latest(zone)
        except Exception as e:
            result["carbon_latest"] = {"error": str(e)}
        
        try:
            result["price_latest"] = self.get_price_latest(zone)
        except Exception as e:
            result["price_latest"] = {"error": str(e)}
        
        return result


if __name__ == "__main__":
    # Example usage with Sandbox key
    SANDBOX_KEY = "YOUR_SANDBOX_KEY_HERE"
    
    client = ElectricityMapsClient(SANDBOX_KEY, use_sandbox=True)
    
    print("=== Carbon Intensity Latest (Belgium) ===")
    try:
        carbon = client.get_carbon_intensity_latest("BE")
        print(carbon)
    except Exception as e:
        print(f"Error: {e}")
    
    print("\n=== Carbon Forecast (first 3) ===")
    try:
        forecast = client.get_carbon_intensity_forecast("BE")
        print(forecast.head(3))
    except Exception as e:
        print(f"Error: {e}")