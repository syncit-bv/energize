#!/usr/bin/env python3
"""
Elia Open Data Client voor EMS Belgium
=========================================
Elia is de Belgische TSO (Transmission System Operator).

Endpoints (opendata.elia.be/api/explore/v2.1/):
  ods032 → Imbalance prijzen per kwartier (MIP / MDP / alpha / NRV / SI)
  ods031 → Systeem onbalans (NRV in MW)
  ods086 → aFRR geactiveerd volume (upward/downward)
  ods088 → mFRR geactiveerd volume
  ods001 → Totale belasting BE (real-time)
  ods023 → Berekende onbalans-tarieven (voorlopig, T-1)

Relevantie voor EMS:
  • MIP (Marginal Incremental Price): prijs als je omhoog regelt (ontladen = verkopen)
    → Hoog MIP = ontladen is zeer winstgevend (grid heeft stroom nodig)
  • MDP (Marginal Decremental Price): prijs als je omlaag regelt (laden)
    → Laag/negatief MDP = laden wordt betaald (grid heeft absorptie nodig)
  • NRV (Net Regulation Volume): positief = grid is short, negatief = long
    → Grote positieve NRV = batterij ontladen is waardevol
  • alpha: imbalance coefficient (1 of 0.85) — bepaalt of je de MIP of de spot
    prijs ontvangt bij positieve onbalans
"""

import requests
import pandas as pd
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any
import time


BASE_URL = "https://opendata.elia.be/api/explore/v2.1/catalog/datasets"

# Dataset IDs op Elia Open Data Platform
DATASETS = {
    "imbalance_prices":  "ods032",   # MIP, MDP, alpha, NRV, SI — kwartierlijks
    "system_imbalance":  "ods031",   # NRV volume (MW) — kwartierlijks
    "afrr_volume":       "ods086",   # aFRR geactiveerd volume up/down
    "mfrr_volume":       "ods088",   # mFRR geactiveerd volume
    "total_load":        "ods001",   # Totale belasting (gemeten + voorspeld)
    "calc_imbalance":    "ods023",   # Berekende onbalanstarieven (T-1)
}


class EliaClient:
    """
    Client voor Elia Open Data Platform.
    Geen API key vereist — publieke open data.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent":  "EnergyEMS-Belgium/1.0",
            "Accept":      "application/json",
        })

    # ─────────────────────────────────────────────────────────────────────────
    # Core request helper
    # ─────────────────────────────────────────────────────────────────────────
    def _get_records(
        self,
        dataset_id: str,
        start: datetime,
        end:   datetime,
        limit: int = 500,
        datetime_field: str = "datetime",
    ) -> list[dict]:
        """Fetch all records for a dataset between start and end."""
        url    = f"{BASE_URL}/{dataset_id}/records"
        where  = (
            f'{datetime_field} >= "{start.strftime("%Y-%m-%dT%H:%M:%S")}" '
            f'AND {datetime_field} < "{end.strftime("%Y-%m-%dT%H:%M:%S")}"'
        )
        params = {
            "where":    where,
            "order_by": f"{datetime_field} asc",
            "limit":    limit,
            "timezone": "Europe/Brussels",
        }

        all_records = []
        offset      = 0

        for _ in range(20):  # max 20 pages (10 000 records)
            params["offset"] = offset
            for attempt in range(3):
                try:
                    r = self.session.get(url, params=params, timeout=20)
                    r.raise_for_status()
                    break
                except requests.RequestException:
                    if attempt == 2:
                        raise
                    time.sleep(1)

            batch = r.json().get("results", [])
            if not batch:
                break
            all_records.extend(batch)
            if len(batch) < limit:
                break
            offset += limit

        return all_records

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Imbalance prijzen (MIP / MDP)  ← meest relevant voor EMS
    # ─────────────────────────────────────────────────────────────────────────
    def get_imbalance_prices(
        self,
        start: date | datetime,
        end:   date | datetime,
    ) -> pd.DataFrame:
        """
        Kwartier-imbalans-tarieven.

        Kolommen: datetime, nrv_mw, si_mw, mip_eur_mwh, mdp_eur_mwh,
                  alpha, cip_eur_mwh, cdp_eur_mwh

        Interpretatie:
          nrv_mw  > 0 → grid is short (ontladen waardevol)
          nrv_mw  < 0 → grid is long  (laden wordt betaald)
          mip     = prijs voor upward regulation (ontladen / leveren)
          mdp     = prijs voor downward regulation (laden / absorberen)
          alpha   = 1.0 of 0.85 (imbalance-verrekenings-coëfficiënt)
        """
        start_dt = _to_dt(start, start_of_day=True)
        end_dt   = _to_dt(end,   start_of_day=False)

        records = self._get_records(DATASETS["imbalance_prices"], start_dt, end_dt)

        if not records:
            return _empty_imbalance_df()

        rows = []
        for rec in records:
            try:
                dt = datetime.fromisoformat(
                    (rec.get("datetime") or rec.get("timestamp", "")).replace("Z", "+00:00")
                )
                rows.append({
                    "datetime":    dt,
                    "nrv_mw":      _f(rec, "nrv"),
                    "si_mw":       _f(rec, "si"),
                    "mip_eur_mwh": _f(rec, "mip"),
                    "mdp_eur_mwh": _f(rec, "mdp"),
                    "alpha":       _f(rec, "alpha", default=1.0),
                    "cip_eur_mwh": _f(rec, "cip"),
                    "cdp_eur_mwh": _f(rec, "cdp"),
                })
            except (ValueError, KeyError):
                continue

        df = pd.DataFrame(rows).sort_values("datetime").reset_index(drop=True)
        return df

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Laatste kwartier imbalance (real-time dashboard tile)
    # ─────────────────────────────────────────────────────────────────────────
    def get_latest_imbalance(self) -> Dict[str, Any]:
        """Meest recente kwartier-imbalans-snapshot voor live dashboard tile."""
        end   = datetime.utcnow() + timedelta(hours=1)
        start = end - timedelta(hours=3)
        df    = self.get_imbalance_prices(start.date(), end.date())

        if df.empty:
            return {"status": "Geen data", "nrv_mw": None}

        latest = df.iloc[-1]
        grid_state = "⚡ SHORT (ontladen = winstgevend)" if (latest["nrv_mw"] or 0) > 0 \
                else "🌊 LONG (laden wordt betaald)"

        return {
            "datetime":    str(latest["datetime"]),
            "nrv_mw":      latest["nrv_mw"],
            "si_mw":       latest["si_mw"],
            "mip_eur_mwh": latest["mip_eur_mwh"],
            "mdp_eur_mwh": latest["mdp_eur_mwh"],
            "alpha":        latest["alpha"],
            "grid_state":  grid_state,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 3. aFRR geactiveerde volumes
    # ─────────────────────────────────────────────────────────────────────────
    def get_afrr_volumes(
        self,
        start: date | datetime,
        end:   date | datetime,
    ) -> pd.DataFrame:
        """
        aFRR (automatic Frequency Restoration Reserve) geactiveerde volumes.

        Kolommen: datetime, afrr_up_mw, afrr_down_mw
        Hoge aFRR-up activatie → ontladen batterij via aggregator (Yuso) is winstgevend.
        """
        start_dt = _to_dt(start, start_of_day=True)
        end_dt   = _to_dt(end,   start_of_day=False)

        records = self._get_records(DATASETS["afrr_volume"], start_dt, end_dt)

        if not records:
            return pd.DataFrame(columns=["datetime", "afrr_up_mw", "afrr_down_mw"])

        rows = []
        for rec in records:
            try:
                dt = datetime.fromisoformat(
                    (rec.get("datetime") or "").replace("Z", "+00:00")
                )
                rows.append({
                    "datetime":    dt,
                    "afrr_up_mw":  _f(rec, "afrrup") or _f(rec, "upward") or _f(rec, "up"),
                    "afrr_down_mw":_f(rec, "afrrdown") or _f(rec, "downward") or _f(rec, "down"),
                })
            except (ValueError, KeyError):
                continue

        return pd.DataFrame(rows).sort_values("datetime").reset_index(drop=True)

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Totale belasting Belgium (load profile)
    # ─────────────────────────────────────────────────────────────────────────
    def get_total_load(
        self,
        start: date | datetime,
        end:   date | datetime,
    ) -> pd.DataFrame:
        """Totale belasting (MW) — handig voor demand-response correlatie."""
        start_dt = _to_dt(start, start_of_day=True)
        end_dt   = _to_dt(end,   start_of_day=False)
        records  = self._get_records(DATASETS["total_load"], start_dt, end_dt,
                                     datetime_field="datetime")

        if not records:
            return pd.DataFrame(columns=["datetime", "load_mw"])

        rows = []
        for rec in records:
            try:
                dt = datetime.fromisoformat(
                    (rec.get("datetime") or "").replace("Z", "+00:00")
                )
                load = _f(rec, "totalload") or _f(rec, "load") or _f(rec, "eliameasured")
                rows.append({"datetime": dt, "load_mw": load})
            except (ValueError, KeyError):
                continue

        return pd.DataFrame(rows).sort_values("datetime").reset_index(drop=True)

    # ─────────────────────────────────────────────────────────────────────────
    # 5. Gecombineerde EMS-intelligentie voor één dag
    # ─────────────────────────────────────────────────────────────────────────
    def get_ems_intelligence(self, target_date: date) -> Dict[str, Any]:
        """
        Combineert imbalance + aFRR data voor één dag tot een EMS-advies dict.
        Handig voor dashboard tiles.
        """
        end   = target_date + timedelta(days=1)
        df_imb = self.get_imbalance_prices(target_date, end)

        if df_imb.empty:
            return {"status": "Geen Elia data beschikbaar",
                    "tip": "Controleer de Elia Open Data verbinding."}

        nrv_pos   = df_imb[df_imb["nrv_mw"] > 50]   # grid short >50 MW
        nrv_neg   = df_imb[df_imb["nrv_mw"] < -50]  # grid long  >50 MW
        avg_mip   = df_imb["mip_eur_mwh"].mean()
        avg_mdp   = df_imb["mdp_eur_mwh"].mean()
        peak_mip  = df_imb["mip_eur_mwh"].max()

        return {
            "date":               str(target_date),
            "quarters_analyzed":  len(df_imb),
            "grid_short_qtrs":    len(nrv_pos),
            "grid_long_qtrs":     len(nrv_neg),
            "avg_mip_eur_mwh":    round(avg_mip,  2) if avg_mip  else None,
            "avg_mdp_eur_mwh":    round(avg_mdp,  2) if avg_mdp  else None,
            "peak_mip_eur_mwh":   round(peak_mip, 2) if peak_mip else None,
            "discharge_opportunity": len(nrv_pos) > 8,
            "charge_opportunity":    len(nrv_neg) > 8,
            "status": "OK",
        }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _to_dt(d: date | datetime, start_of_day: bool = True) -> datetime:
    if isinstance(d, datetime):
        return d
    if start_of_day:
        return datetime(d.year, d.month, d.day, 0, 0, 0)
    return datetime(d.year, d.month, d.day, 23, 59, 59)


def _f(rec: dict, key: str, default=None):
    """Safely get a float from a record, trying lowercase and uppercase."""
    val = rec.get(key) or rec.get(key.upper()) or rec.get(key.capitalize())
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _empty_imbalance_df() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "datetime", "nrv_mw", "si_mw",
        "mip_eur_mwh", "mdp_eur_mwh", "alpha",
        "cip_eur_mwh", "cdp_eur_mwh",
    ])


# ─────────────────────────────────────────────────────────────────────────────
# CLI test
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    client = EliaClient()

    today = date.today()
    print(f"Elia imbalance prijzen voor {today}…")
    df = client.get_imbalance_prices(today, today + timedelta(days=1))
    if not df.empty:
        print(df[["datetime", "nrv_mw", "mip_eur_mwh", "mdp_eur_mwh"]].head(12).to_string())
        print(f"\nGemiddeld MIP: {df['mip_eur_mwh'].mean():.2f} €/MWh")
        print(f"Gemiddeld MDP: {df['mdp_eur_mwh'].mean():.2f} €/MWh")
    else:
        print("Geen data beschikbaar (controleer verbinding met opendata.elia.be)")

    print("\nLatest imbalance snapshot:")
    snap = client.get_latest_imbalance()
    for k, v in snap.items():
        print(f"  {k}: {v}")
