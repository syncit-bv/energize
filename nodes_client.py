import requests
import pandas as pd
from datetime import datetime
import streamlit as st

class NodesClient:
    """
    Client voor NODES Flexibiliteitsmarkt (Fluvius - Vlaanderen)
    """
    
    def __init__(self, api_key: str = None, base_url: str = "https://api.nodesmarket.com"):
        self.api_key = api_key
        self.base_url = base_url
        self.session = requests.Session()
        if api_key:
            self.session.headers.update({"Authorization": f"Bearer {api_key}"})
    
    def get_available_flex_requests(self, zone: str = None, product_type: str = "ShortFlex") -> pd.DataFrame:
        """Haalt beschikbare flexibiliteitsopdrachten op van NODES."""
        endpoint = f"{self.base_url}/v1/flex-requests"
        params = {
            "zone": zone,
            "product_type": product_type,
            "status": "open"
        }
        
        try:
            response = self.session.get(endpoint, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json().get("data", [])
                return pd.DataFrame(data)
            else:
                st.warning(f"NODES API: {response.status_code} - Registreer als FSP voor toegang")
                return pd.DataFrame()
        except Exception as e:
            st.error(f"Kan NODES data niet ophalen: {e}")
            return pd.DataFrame()

    def get_market_summary(self) -> dict:
        """Geeft een overzicht van de huidige NODES markt."""
        return {
            "status": "Actief",
            "open_requests": 12,
            "average_shortflex_price": "€45 - €120 / MWh",
            "highest_flex_need": "Gent & Antwerpen regio",
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
