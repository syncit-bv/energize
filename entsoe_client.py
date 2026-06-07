"""
Wrapper around entsoe-py for use by app/routers/prices.py.
Importeerbaar als: from entsoe_client import EntsoeClient
"""
from __future__ import annotations
import logging
from datetime import date as Date
import pandas as pd

logger = logging.getLogger(__name__)


class EntsoeClient:
    COUNTRY = "BE"
    TZ      = "Europe/Brussels"

    def __init__(self, api_key: str):
        from entsoe import EntsoePandasClient
        self._client = EntsoePandasClient(api_key=api_key)

    def get_day_ahead_prices(self, start: Date, end: Date) -> pd.DataFrame:
        """
        Retourneert DataFrame met kolommen: datetime (str ISO-8601), price_eur_mwh (float).
        end is exclusief (ENTSO-E conventie).
        """
        ts_start = pd.Timestamp(str(start), tz=self.TZ)
        ts_end   = pd.Timestamp(str(end),   tz=self.TZ)

        series = self._client.query_day_ahead_prices(
            self.COUNTRY, start=ts_start, end=ts_end
        )

        df = series.reset_index()
        df.columns = ["datetime", "price_eur_mwh"]
        df["datetime"]     = df["datetime"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
        df["price_eur_mwh"] = df["price_eur_mwh"].astype(float).round(2)
        return df
