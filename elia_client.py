"""
Elia Open Data REST client — geen authenticatie vereist.
Importeerbaar als: from elia_client import EliaClient

Dataset status (gecontroleerd juni 2026):
  ods047  15-min validated imbalance  → bevroren na mei 2024; gebruik ods161 voor live data
  ods161  1-min live imbalance        → rolling live window, vandaag beschikbaar
  ods087  solar per regio             → live; meerdere rijen/tijdstip, aggregeer via SUM
  ods086  wind per regio + type       → live; meerdere rijen/tijdstip, splits on/offshore
  ods017/018/019                      → niet meer actief; vervangen door ods087/086
"""
from __future__ import annotations
import logging
from datetime import date as Date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)
BASE = "https://opendata.elia.be/api/explore/v2.1/catalog/datasets"
_BRUSSELS = ZoneInfo("Europe/Brussels")

# ods047 bevat geen data na deze datum
_ODS047_MAX_DATE = Date(2024, 5, 21)


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
    """
    Genereert een WHERE-clause die de volledige Brusselse dag (lokale tijd) dekt in UTC.
    Elia slaat timestamps op in UTC, maar de gebruiker kiest datums in Belgische tijd (CET/CEST).
    Voorbeeld: Belgische datum 7 jun → zoek in UTC 6 jun 22:00 t/m 7 jun 22:00 (CEST/UTC+2).
    """
    start_local = datetime(target.year, target.month, target.day, 0, 0, 0, tzinfo=_BRUSSELS)
    next_d      = target + timedelta(days=1)
    end_local   = datetime(next_d.year, next_d.month, next_d.day, 0, 0, 0, tzinfo=_BRUSSELS)
    start_utc   = start_local.astimezone(timezone.utc)
    end_utc     = end_local.astimezone(timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return f'datetime >= "{start_utc.strftime(fmt)}" AND datetime < "{end_utc.strftime(fmt)}"'


class EliaClient:

    # ── Imbalans (15-min validated) ──────────────────────────────────────────

    def get_imbalance(self, target: Date) -> list[dict]:
        """
        Belgische systeemonbalans per kwartier (Elia ods047).
        Velden: timestamp, system_imbalance (MW), nrv (MW), alpha (€/MWh).
        LET OP: ods047 bevat enkel data tot en met mei 2024.
        Voor actuele data: gebruik get_live_imbalance (ods161).
        """
        if target > _ODS047_MAX_DATE:
            logger.info(
                "ods047 bevat geen data na %s (gevraagd: %s) — lege lijst teruggegeven",
                _ODS047_MAX_DATE, target,
            )
            return []

        where = _date_range_where(target)
        try:
            recs = _get("ods047", where)
        except Exception as exc:
            logger.warning("Elia ods047 fetch mislukt voor %s: %s", target, exc)
            return []

        rows = []
        for r in recs:
            rows.append({
                "timestamp":        r.get("datetime", ""),
                "system_imbalance": _f(r.get("systemimbalance"), r.get("si")),
                "nrv":              _f(r.get("nrv"), r.get("netregulationvolume")),
                "alpha":            _f(r.get("alpha"), r.get("mip")),
            })
        return rows

    # ── Live 1-min onbalans ──────────────────────────────────────────────────

    def get_live_imbalance(self, target: Date) -> list[dict]:
        """
        Real-time 1-min systeemonbalans (Elia ods161).
        Velden: timestamp, system_imbalance (MW), nrv (MW), alpha (€/MWh).
        Rolling window van de huidige dag; enkel vandaag en recent beschikbaar.
        """
        where = _date_range_where(target)
        recs = _get("ods161", where, limit=1500)

        rows = []
        for r in recs:
            rows.append({
                "timestamp":        r.get("datetime", ""),
                "system_imbalance": _f(r.get("systemimbalance"), r.get("si")),
                "nrv":              _f(r.get("nrv"), r.get("ace")),
                "alpha":            _f(r.get("imbalanceprice"), r.get("alpha")),
            })
        return rows

    # ── Zon & Wind realisatie ────────────────────────────────────────────────

    def get_solar_wind(self, target: Date) -> list[dict]:
        """
        Totale zon- en windproductie voor België per kwartier.
        Velden: timestamp, solar_mw, wind_onshore_mw, wind_offshore_mw.

        Bron:
          ods087 — solar per regio → SUM(realtime) per tijdstip (limit=2000)
          ods086 — wind per regio + offshoreonshore → split + SUM per type (limit=5000)
        Beide datasets zijn live.
        """
        where = _date_range_where(target)

        solar_recs = _get("ods087", where, limit=2000)
        wind_recs  = _get("ods086", where, limit=5000)

        # Aggregeer solar: SUM realtime (fallback mostrecentforecast) per ts
        solar_map: dict[str, float] = {}
        for r in solar_recs:
            ts = r.get("datetime", "")
            if not ts:
                continue
            val = _f(r.get("realtime"), r.get("mostrecentforecast"))
            solar_map[ts] = solar_map.get(ts, 0.0) + val

        # Aggregeer wind: splits onshore / offshore
        wind_on_map:  dict[str, float] = {}
        wind_off_map: dict[str, float] = {}
        for r in wind_recs:
            ts = r.get("datetime", "")
            if not ts:
                continue
            val = _f(r.get("realtime"), r.get("mostrecentforecast"))
            if r.get("offshoreonshore") == "Offshore":
                wind_off_map[ts] = wind_off_map.get(ts, 0.0) + val
            else:
                wind_on_map[ts] = wind_on_map.get(ts, 0.0) + val

        all_ts = sorted(set(solar_map) | set(wind_on_map) | set(wind_off_map))
        return [{
            "timestamp":        ts,
            "solar_mw":         round(solar_map.get(ts, 0.0), 1),
            "wind_onshore_mw":  round(wind_on_map.get(ts, 0.0), 1),
            "wind_offshore_mw": round(wind_off_map.get(ts, 0.0), 1),
        } for ts in all_ts]

    # ── Prognose vs Realisatie: zon ──────────────────────────────────────────

    def get_solar_forecast(self, target: Date) -> list[dict]:
        """
        Zonne-energie dag-ahead prognose vs realisatie, geaggregeerd over alle regio's.
        Velden: timestamp, forecast_mw, measured_mw.
        Bron: ods087 — dayaheadforecast vs realtime, SUM per tijdstip.
        """
        where = _date_range_where(target)
        recs = _get("ods087", where, limit=2000)

        forecast_map: dict[str, float] = {}
        measured_map: dict[str, float] = {}
        for r in recs:
            ts = r.get("datetime", "")
            if not ts:
                continue
            forecast_map[ts] = forecast_map.get(ts, 0.0) + _f(
                r.get("dayaheadforecast"), r.get("mostrecentforecast")
            )
            measured_map[ts] = measured_map.get(ts, 0.0) + _f(r.get("realtime"))

        all_ts = sorted(set(forecast_map) | set(measured_map))
        return [{
            "timestamp":   ts,
            "forecast_mw": round(forecast_map.get(ts, 0.0), 1),
            "measured_mw": round(measured_map.get(ts, 0.0), 1),
        } for ts in all_ts]

    # ── Prognose vs Realisatie: wind ─────────────────────────────────────────

    def get_wind_forecast(self, target: Date) -> list[dict]:
        """
        Wind dag-ahead prognose vs realisatie, gesplitst onshore/offshore.
        Velden: timestamp, onshore_forecast_mw, onshore_measured_mw,
                           offshore_forecast_mw, offshore_measured_mw.
        Bron: ods086 — dayaheadforecast vs realtime, SUM per tijdstip per type.
        """
        where = _date_range_where(target)
        recs = _get("ods086", where, limit=5000)

        on_fc:  dict[str, float] = {}
        on_me:  dict[str, float] = {}
        off_fc: dict[str, float] = {}
        off_me: dict[str, float] = {}

        for r in recs:
            ts = r.get("datetime", "")
            if not ts:
                continue
            val_fc = _f(r.get("dayaheadforecast"), r.get("mostrecentforecast"))
            val_me = _f(r.get("realtime"))
            if r.get("offshoreonshore") == "Offshore":
                off_fc[ts] = off_fc.get(ts, 0.0) + val_fc
                off_me[ts] = off_me.get(ts, 0.0) + val_me
            else:
                on_fc[ts] = on_fc.get(ts, 0.0) + val_fc
                on_me[ts] = on_me.get(ts, 0.0) + val_me

        all_ts = sorted(set(on_fc) | set(off_fc))
        return [{
            "timestamp":            ts,
            "onshore_forecast_mw":  round(on_fc.get(ts, 0.0), 1),
            "onshore_measured_mw":  round(on_me.get(ts, 0.0), 1),
            "offshore_forecast_mw": round(off_fc.get(ts, 0.0), 1),
            "offshore_measured_mw": round(off_me.get(ts, 0.0), 1),
        } for ts in all_ts]
