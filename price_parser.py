#!/usr/bin/env python3
"""
EMS MVP - Price Parser for ENTSO-E Publication_MarketDocument (A44 day-ahead prices Belgium)
"""

import xml.etree.ElementTree as ET
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

def parse_entsoe_prices(xml_path: str | Path) -> pd.DataFrame:
    """Parse the large ENTSO-E XML into a clean DataFrame with datetime and price (EUR/MWh)."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

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

    df = pd.DataFrame(records)
    df = df.sort_values('datetime').reset_index(drop=True)
    df['price_eur_kwh'] = df['price_eur_mwh'] / 1000.0  # for easier kWh calculations
    return df


if __name__ == "__main__":
    xml_file = "/home/workdir/attachments/Energy_Prices_202512312300-202612312300.xml"
    df = parse_entsoe_prices(xml_file)
    print(f"Parsed {len(df)} price points from {df['datetime'].min()} to {df['datetime'].max()}")
    print(df.head(10))
    print("\nNegative price summary:")
    neg = df[df['price_eur_mwh'] < 0]
    print(f"  Count: {len(neg)} quarters on {neg['date'].nunique()} days")
    print(f"  Min: {neg['price_eur_mwh'].min():.2f} €/MWh")
    # Save sample for quick use
    df.to_parquet("/home/workdir/artifacts/ems_mvp/prices_belgium.parquet", index=False)
    print("\nSaved cleaned prices to prices_belgium.parquet")