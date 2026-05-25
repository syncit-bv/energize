#!/usr/bin/env python3
"""
ENTSO-E Transparency Platform API Client for EMS Belgium
Fetches Day-Ahead Prices (Document Type A44) for Belgium.

Requires free API key from: https://transparency.entsoe.eu/

Usage:
    from entsoe_client import EntsoeClient
    client = EntsoeClient(api_key="YOUR_KEY")
    df = client.get_day_ahead_prices(start="2026-05-01", end="2026-05-25")
"""

import requests
import xml.etree.ElementTree as ET
import pandas as pd
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional
import time

# Belgium Domain Code (EIC)
BELGIUM_DOMAIN = "10YBE----------2"

# ENTSO-E Transparency Platform base URL
BASE_URL = "https://web-api.tp.entsoe.eu/api"

class EntsoeClient:
    def __init__(self, api_key: str):
        if not api_key or api_key == "YOUR_API_KEY_HERE":
            raise ValueError("Valid ENTSO-E API key is required. Get one for free at https://transparency.entsoe.eu/")
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/xml"})

    def _make_request(self, params: dict) -> str:
        """Internal method to call ENTSO-E API with retry logic."""
        params["securityToken"] = self.api_key
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.session.get(BASE_URL, params=params, timeout=30)
                
                if response.status_code == 200:
                    return response.text
                elif response.status_code == 429:
                    # Rate limit - wait and retry
                    wait_time = 2 ** attempt
                    print(f"Rate limit hit. Waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    response.raise_for_status()
                    
            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    raise Exception(f"ENTSO-E API request failed after {max_retries} attempts: {e}")
                time.sleep(1)
        
        raise Exception("Failed to get response from ENTSO-E after retries")

    def get_day_ahead_prices(
        self, 
        start: str | date, 
        end: str | date,
        domain: str = BELGIUM_DOMAIN
    ) -> pd.DataFrame:
        """
        Fetch Day-Ahead Prices (A44) for a given period.
        
        Args:
            start: Start date (YYYY-MM-DD or date object)
            end: End date (YYYY-MM-DD or date object) - exclusive
            domain: EIC domain code (default: Belgium)
            
        Returns:
            DataFrame with columns: datetime, price_eur_mwh, date, hour, quarter, price_eur_kwh
        """
        if isinstance(start, str):
            start = datetime.strptime(start, "%Y-%m-%d").date()
        if isinstance(end, str):
            end = datetime.strptime(end, "%Y-%m-%d").date()
            
        # ENTSO-E expects periodStart and periodEnd in YYYYMMDDHHMM format (UTC)
        period_start = start.strftime("%Y%m%d") + "0000"
        period_end = end.strftime("%Y%m%d") + "0000"
        
        params = {
            "documentType": "A44",  # Day-ahead prices
            "in_Domain": domain,
            "out_Domain": domain,
            "periodStart": period_start,
            "periodEnd": period_end,
        }
        
        xml_content = self._make_request(params)
        
        # Parse the XML response
        return self._parse_day_ahead_xml(xml_content)

    def _parse_day_ahead_xml(self, xml_content: str) -> pd.DataFrame:
        """Parse ENTSO-E Publication_MarketDocument XML for day-ahead prices."""
        root = ET.fromstring(xml_content)
        
        ns = {'ns': 'urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3'}
        
        records = []
        for ts in root.findall('.//ns:TimeSeries', ns):
            for period in ts.findall('.//ns:Period', ns):
                start_str = period.find('.//ns:timeInterval/ns:start', ns).text
                start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                
                for point in period.findall('.//ns:Point', ns):
                    pos = int(point.find('ns:position', ns).text)
                    price = float(point.find('ns:price.amount', ns).text)
                    dt = start_dt + timedelta(minutes=15 * (pos - 1))
                    
                    records.append({
                        'datetime': dt,
                        'price_eur_mwh': price,
                        'date': dt.date(),
                        'hour': dt.hour,
                        'quarter': (dt.minute // 15) + 1
                    })
        
        if not records:
            return pd.DataFrame(columns=['datetime', 'price_eur_mwh', 'date', 'hour', 'quarter', 'price_eur_kwh'])
        
        df = pd.DataFrame(records)
        df = df.sort_values('datetime').reset_index(drop=True)
        df['price_eur_kwh'] = df['price_eur_mwh'] / 1000.0
        
        return df

    def fetch_and_save_latest(
        self, 
        days_back: int = 7,
        output_path: str | Path = "prices_belgium_latest.parquet"
    ) -> pd.DataFrame:
        """
        Convenience method: Fetch last N days and save to parquet.
        Useful for daily automation (e.g. cron job after 15:00 when day-ahead is published).
        """
        end = date.today() + timedelta(days=1)
        start = end - timedelta(days=days_back)
        
        df = self.get_day_ahead_prices(start, end)
        
        if not df.empty:
            output_path = Path(output_path)
            df.to_parquet(output_path, index=False)
            print(f"Saved {len(df)} price points to {output_path}")
        
        return df


if __name__ == "__main__":
    # Example usage (replace with your key)
    API_KEY = "YOUR_API_KEY_HERE"
    
    client = EntsoeClient(API_KEY)
    
    # Fetch last 3 days as example
    df = client.fetch_and_save_latest(days_back=3)
    print(df.head())
    print(f"\nTotal rows: {len(df)}")
    print(f"Negative prices: {(df['price_eur_mwh'] < 0).sum()}")