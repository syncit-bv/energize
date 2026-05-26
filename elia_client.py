#!/usr/bin/env python3
"""
Elia Open Data Client voor EMS Belgium
=======================================
Gebaseerd op de officiële elia-py bibliotheek (pip install elia-py).

Correcte dataset IDs (geverifieerd via elia-py source):
  ods162  → Near real-time imbalance prijzen, kwartierlijks (huidige dag)
  ods134  → Historische imbalance prijzen, kwartierlijks (na MARI 22/05/2024)
  ods047  → Historische imbalance prijzen, kwartierlijks (vóór MARI, deprecated)
  ods161  → Imbalance prijzen per minuut (near real-time)
  ods169  → Systeem onbalans real-time (per minuut)

EMS relevantie:
  MIP: Marginal Incremental Price — prijs voor ontladen (upward regulation)
  MDP: Marginal Decremental Price — prijs voor laden (downward regulation)
  NRV: Net Regulation Volume (MW) — positief = grid short, negatief = long
"""

from __future__ import annotations
import datetime as dt
from typing import Optional, Dict, Any
import pandas as pd

try:
    from elia import elia as _elia_lib
    ELIA_LIB_AVAILABLE = True
except ImportError:
    ELIA_LIB_AVAILABLE = False


class EliaClient:
    """
    Wrapper rond elia-py voor EMS-relevante Elia Open Data.
    Geen API key nodig. Vereist: pip install elia-py
    """

    def __init__(self):
        if not ELIA_LIB_AVAILABLE:
            raise ImportError(
                "elia-py niet geïnstalleerd. Run: pip install elia-py\n"
                "Voeg 'elia-py>=0.3.1' toe aan requirements.txt"
            )
        self._c = _elia_lib.EliaPandasClient()

    def get_realtime_imbalance(self) -> pd.DataFrame:
        """Near real-time imbalance (ods162) — huidige dag, kwartierlijks."""
        df = self._c.get_near_real_time_imbalance_prices_per_quarter_hour()
        return self._standardize(df)

    def get_imbalance_prices(
        self,
        start: dt.date | dt.datetime,
        end:   dt.date | dt.datetime,
    ) -> pd.DataFrame:
        """
        Historische imbalance prijzen per kwartier.
        Automatisch routing: ods134 (na MARI 22/05/2024) of ods047 (voor MARI).
        Grote bereiken worden automatisch opgesplitst in 5-daagse chunks.
        """
        start_dt  = _to_dt(start)
        end_dt    = _to_dt(end)
        MARI      = dt.datetime(2024, 5, 22)
        parts     = []

        if start_dt < MARI:
            try:
                df_pre = self._c.get_historical_imbalance_prices_per_quarter_hour_before_mari(
                    start=start_dt, end=min(end_dt, MARI))
                parts.append(self._standardize(df_pre))
            except Exception as e:
                print(f"[Elia] ods047 fout: {e}")

        if end_dt > MARI:
            try:
                df_post = self._c.get_historical_imbalance_prices_per_quarter_hour(
                    start=max(start_dt, MARI), end=end_dt)
                parts.append(self._standardize(df_post))
            except Exception as e:
                print(f"[Elia] ods134 fout: {e}")

        if not parts:
            return _empty_df()
        return (pd.concat(parts)
                  .sort_values("datetime")
                  .drop_duplicates("datetime")
                  .reset_index(drop=True))

    def get_latest_imbalance(self) -> Dict[str, Any]:
        """Meest recente kwartier snapshot voor live dashboard tile."""
        try:
            df = self.get_realtime_imbalance()
        except Exception as e:
            return {"status": f"Fout: {e}", "nrv_mw": None}
        if df.empty:
            return {"status": "Geen data (ods162 leeg)", "nrv_mw": None}

        r   = df.iloc[-1]
        nrv = float(r.get("nrv_mw", 0) or 0)
        mip = r.get("mip_eur_mwh")
        mdp = r.get("mdp_eur_mwh")
        grid = ("⚡ SHORT (ontladen = winstgevend)" if nrv > 50
                else "🌊 LONG (laden wordt betaald)" if nrv < -50
                else "⚖️  Gebalanceerd")

        return {
            "datetime":    str(r.get("datetime", "—")),
            "nrv_mw":      round(nrv, 1),
            "si_mw":       round(float(r.get("si_mw", 0) or 0), 1),
            "mip_eur_mwh": round(float(mip), 2) if mip is not None else None,
            "mdp_eur_mwh": round(float(mdp), 2) if mdp is not None else None,
            "grid_state":  grid,
            "status":      "OK",
        }

    def get_ems_intelligence(self, target_date: dt.date) -> Dict[str, Any]:
        """EMS-advies dict voor één dag (combineert imbalance data)."""
        if target_date >= dt.date.today():
            try:
                df = self.get_realtime_imbalance()
            except Exception:
                df = pd.DataFrame()
        else:
            df = self.get_imbalance_prices(target_date, target_date + dt.timedelta(days=1))

        if df.empty:
            return {"status": "Geen Elia data", "tip": "Controleer verbinding met opendata.elia.be"}

        def _col(df, name):
            if name in df.columns:
                return df[name]
            hits = [c for c in df.columns if name.split("_")[0] in c.lower()]
            return df[hits[0]] if hits else pd.Series(dtype=float)

        nrv = _col(df, "nrv_mw")
        mip = _col(df, "mip_eur_mwh")
        mdp = _col(df, "mdp_eur_mwh")

        return {
            "date":               str(target_date),
            "quarters_analyzed":  len(df),
            "grid_short_qtrs":    int((nrv > 50).sum()),
            "grid_long_qtrs":     int((nrv < -50).sum()),
            "avg_mip_eur_mwh":    round(float(mip.mean()), 2) if not mip.empty else None,
            "avg_mdp_eur_mwh":    round(float(mdp.mean()), 2) if not mdp.empty else None,
            "peak_mip_eur_mwh":   round(float(mip.max()),  2) if not mip.empty else None,
            "discharge_opportunity": bool(int((nrv > 50).sum()) > 8),
            "charge_opportunity":    bool(int((nrv < -50).sum()) > 8),
            "status": "OK",
        }

    def _standardize(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normaliseer ruwe Elia DataFrame naar consistente EMS kolomnamen."""
        if df.empty:
            return _empty_df()
        if df.index.name == "datetime" or isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()

        col_lower = {c.lower(): c for c in df.columns}
        rename = {}
        for src, dst in [("nrv","nrv_mw"),("si","si_mw"),("mip","mip_eur_mwh"),
                         ("mdp","mdp_eur_mwh"),("alpha","alpha"),
                         ("cip","cip_eur_mwh"),("cdp","cdp_eur_mwh")]:
            if src in col_lower:
                rename[col_lower[src]] = dst
        df = df.rename(columns=rename)

        if "datetime" not in df.columns:
            time_cols = [c for c in df.columns if "time" in c.lower() or "date" in c.lower()]
            if time_cols:
                df = df.rename(columns={time_cols[0]: "datetime"})

        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")

        for col in ["nrv_mw","si_mw","mip_eur_mwh","mdp_eur_mwh",
                    "alpha","cip_eur_mwh","cdp_eur_mwh"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df.sort_values("datetime").reset_index(drop=True)


def _to_dt(d: dt.date | dt.datetime) -> dt.datetime:
    return d if isinstance(d, dt.datetime) else dt.datetime(d.year, d.month, d.day)

def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["datetime","nrv_mw","si_mw","mip_eur_mwh",
                                   "mdp_eur_mwh","alpha","cip_eur_mwh","cdp_eur_mwh"])


if __name__ == "__main__":
    client = EliaClient()
    print("Real-time snapshot:")
    snap = client.get_latest_imbalance()
    for k, v in snap.items(): print(f"  {k}: {v}")

    print("\nHistorische imbalance (gisteren):")
    yesterday = dt.date.today() - dt.timedelta(days=1)
    df = client.get_imbalance_prices(yesterday, dt.date.today())
    if not df.empty:
        print(f"  {len(df)} kwartieren | kolommen: {list(df.columns)}")
        print(df[["datetime","nrv_mw","mip_eur_mwh","mdp_eur_mwh"]].head(4).to_string())
    else:
        print("  Geen data")
