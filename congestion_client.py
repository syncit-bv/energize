import requests
import pandas as pd
from datetime import datetime, timedelta
import streamlit as st

class CongestionClient:
    """
    Client voor Fluvius Netcongestie data en gerelateerde open data bronnen.
    """
    
    def __init__(self):
        self.base_url = "https://data.vlaanderen.be/api/3/action/datastore_search"
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Energize-EMS/1.0"})

    def get_congestion_map(self, municipality: str = "Gent", limit: int = 1000) -> pd.DataFrame:
        """
        Haalt congestie-informatie op via Fluvius Open Data (Capaciteitswijzer).
        """
        params = {
            "resource_id": "a2b3c4d5-e6f7-8901-2345-6789abcdef01",
            "limit": limit,
            "q": municipality
        }
        
        try:
            response = self.session.get(self.base_url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            if data.get("success") and "records" in data.get("result", {}):
                df = pd.DataFrame(data["result"]["records"])
                return df
            else:
                st.warning("Geen congestiedata gevonden voor deze gemeente.")
                return pd.DataFrame()
                
        except Exception as e:
            st.error(f"Fluvius API error: {e}")
            return pd.DataFrame()

    def get_expected_congestion_hours(self, zone: str = "Gent", days_ahead: int = 7) -> pd.DataFrame:
        """
        Geeft een eenvoudige prognose van verwachte congestie-uren.
        """
        today = datetime.now()
        data = []
        
        for i in range(days_ahead):
            day = today + timedelta(days=i)
            risk_level = "Hoog" if day.weekday() < 5 and 11 <= day.hour <= 16 else "Laag"
            
            data.append({
                "date": day.date(),
                "risk_level": risk_level,
                "expected_peak_hours": "12:00-16:00" if risk_level == "Hoog" else "-",
                "recommended_action": "Beperk injectie" if risk_level == "Hoog" else "Normaal"
            })
        
        return pd.DataFrame(data)

    def get_congestion_summary(self, municipality: str = "Gent") -> dict:
        """Geeft een eenvoudige samenvatting voor dashboard gebruik."""
        df = self.get_expected_congestion_hours(municipality)
        
        if df.empty:
            return {"status": "Geen data", "high_risk_days": 0}
        
        high_risk = len(df[df["risk_level"] == "Hoog"])
        
        return {
            "status": "OK",
            "municipality": municipality,
            "high_risk_days_next_week": int(high_risk),
            "percentage_high_risk": round(high_risk / len(df) * 100, 1),
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
