"""
Elia Open Data REST client — geen authenticatie vereist.
Importeerbaar als: from elia_client import EliaClient
"""
from __future__ import annotations
import logging
from datetime import date as Date

import requests

logger = logging.getLogger(__name__)
BASE = "https://opendata.elia.be/api/explore/v2.1/catalog/datasets"


def _get(dataset: str, where: str, limit: int = 500) -> list:
    url = f"{BASE}/{dataset}/records"
    params = {
        "where":    where,
        "limit":    limit,
        "timezone": "UTC",
        "order_by": "datetime ASC",
    }
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", [])


def _f(val, *fallbacks) -> float:
    """Veilige float-conversie met fallbacks."""
    for v in (val, *fallbacks):
        if v is not None:
            try:
                return round(float(v), 3)
            except (TypeError, ValueError):
                continue
    return 0.0


def _date_range_where(target: Date) -> str:
    next_d = Date.fromordinal(target.toordinal() + 1)
    return f'datetime >= "{target}T00:00:00Z" AND datetime < "{next_d}T00:00:00Z"'


class EliaClient:

    def get_imbalance(self, target: Date) -> list[dict]:
        """
        Retourneert lijst van kwartierrecords voor de opgegeven datum.
        Velden per record: timestamp, system_imbalance, nrv, alpha
        Dataset: ods047 (imbalance per kwartier)
        """
        where = _date_range_where(target)
        try:
            recs = _get("ods047", where)
        except Exception as exc:
            logger.error("Elia ods047 fetch mislukt: %s", exc)
            raise

        rows = []
        for r in recs:
            rows.append({
                "timestamp":        r.get("datetime", ""),
                "system_imbalance": _f(r.get("si"), r.get("systemimbalance"), r.get("nrv")),
                "nrv":              _f(r.get("nrv"), r.get("nrvinterval")),
                "alpha":            _f(r.get("alpha"), r.get("mip")),
            })
        return rows

    def get_solar_wind(self, target: Date) -> list[dict]:
        """
        Retourneert gecombineerde zon+wind data voor de opgegeven datum.
        Velden: timestamp, solar_mw, wind_onshore_mw, wind_offshore_mw
        Datasets: ods087 (solar) + ods086 (wind)
        """
        where = _date_range_where(target)

        try:
            solar_recs = _get("ods087", where)
        except Exception as exc:
            logger.warning("Elia solar (ods087) fetch mislukt: %s", exc)
            solar_recs = []

        try:
            wind_recs = _get("ods086", where)
        except Exception as exc:
            logger.warning("Elia wind (ods086) fetch mislukt: %s", exc)
            wind_recs = []

        # Build per-timestamp dicts
        solar_map    = {r["datetime"]: _f(r.get("measured"), r.get("corrected"), r.get("uplift"))
                        for r in solar_recs if "datetime" in r}
        wind_on_map  = {r["datetime"]: _f(r.get("onshore_measured"), r.get("measuredonshore"),
                                           r.get("offshoremonitored"))
                        for r in wind_recs if "datetime" in r}
        wind_off_map = {r["datetime"]: _f(r.get("offshore_measured"), r.get("measuredoffshore"))
                        for r in wind_recs if "datetime" in r}

        all_ts = sorted(set(list(solar_map) + list(wind_on_map)))
        return [{
            "timestamp":        ts,
            "solar_mw":         solar_map.get(ts, 0.0),
            "wind_onshore_mw":  wind_on_map.get(ts, 0.0),
            "wind_offshore_mw": wind_off_map.get(ts, 0.0),
        } for ts in all_ts]
