#!/usr/bin/env python3
"""
Elia Open Data Client voor EMS Belgium
=======================================
Wrapper rond elia-py (pip install elia-py).

Dataset overzicht (geverifieerd):
  IMBALANCE:
    ods162  → Near real-time imbalance prijzen, kwartierlijks (huidige dag, ververst /15 min)
    ods134  → Historische imbalance prijzen, kwartierlijks (na MARI 22/05/2024)
    ods047  → Historische imbalance prijzen, kwartierlijks (vóór MARI — deprecated)
  SOLAR PV:
    ods087  → Actuele solar forecast (intraday + day-ahead + week-ahead, kwartierlijks)
    ods032  → Historische solar PV productie (gemeten + upscaled, kwartierlijks)
  WIND:
    ods086  → Actuele wind forecast (intraday + day-ahead + week-ahead)
    (historisch wind = andere ods)

EMS strategie op basis van solar forecast:
  - Hoge solar forecast morgen → verwacht lage/negatieve prijzen rond 10u-14u
    → plan laden in die periode (gratis of betaald)
  - Lage solar forecast (bewolkt) → prijzen dalen minder
    → standaard MILP arbitrage strategie
  - Combinatie solar + day-ahead prijzen = meest accurate strategie
"""

from __future__ import annotations
import datetime as dt
from typing import Optional, Dict, Any, List
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

    # ─────────────────────────────────────────────────────────────────────────
    # IMBALANCE
    # ─────────────────────────────────────────────────────────────────────────

    def get_realtime_imbalance(self) -> pd.DataFrame:
        """
        Near real-time imbalance (ods162) — huidige dag, kwartierlijks.
        Ververst elke 15 min. Bevat data pas NADAT het eerste kwartier van de dag
        verstreken is (~00:15 CET). Om 01:20 kan er al data zijn voor 00:00-01:15.
        """
        df = self._c.get_near_real_time_imbalance_prices_per_quarter_hour()
        return self._standardize_imbalance(df)

    def get_imbalance_prices(
        self,
        start: dt.date | dt.datetime,
        end:   dt.date | dt.datetime,
    ) -> pd.DataFrame:
        """
        Historische imbalance prijzen per kwartier.
        Automatisch routing: ods134 (na MARI 22/05/2024) / ods047 (voor MARI).
        Grote bereiken worden automatisch opgesplitst in 5-daagse chunks (elia-py).
        """
        start_dt = _to_dt(start)
        end_dt   = _to_dt(end)
        MARI     = dt.datetime(2024, 5, 22)
        parts    = []

        if start_dt < MARI:
            try:
                df_pre = self._c.get_historical_imbalance_prices_per_quarter_hour_before_mari(
                    start=start_dt, end=min(end_dt, MARI))
                parts.append(self._standardize_imbalance(df_pre))
            except Exception as e:
                print(f"[Elia] ods047 fout: {e}")

        if end_dt > MARI:
            try:
                df_post = self._c.get_historical_imbalance_prices_per_quarter_hour(
                    start=max(start_dt, MARI), end=end_dt)
                parts.append(self._standardize_imbalance(df_post))
            except Exception as e:
                print(f"[Elia] ods134 fout: {e}")

        if not parts:
            return _empty_imbalance_df()
        return (pd.concat(parts)
                  .sort_values("datetime")
                  .drop_duplicates("datetime")
                  .reset_index(drop=True))

    def get_imbalance_best_available(self, target_date: dt.date) -> tuple[pd.DataFrame, str]:
        """
        Slimme fallback: probeer real-time → historisch → gisteren.
        Geeft (DataFrame, bron_label) terug.

        Dit lost het 01:20-probleem op: als vandaag nog geen data heeft,
        toon dan gisteren's profiel als referentie.
        """
        today     = dt.date.today()
        yesterday = today - dt.timedelta(days=1)

        # 1. Probeer real-time (ods162) als target = vandaag
        if target_date == today:
            try:
                df = self.get_realtime_imbalance()
                if not df.empty and _has_price_cols(df):
                    return df, f"Real-time (ods162) — {len(df)} kwartieren geladen"
            except Exception:
                pass

        # 2. Probeer historisch (ods134)
        try:
            df = self.get_imbalance_prices(
                dt.datetime.combine(target_date, dt.time.min),
                dt.datetime.combine(target_date + dt.timedelta(days=1), dt.time.min),
            )
            if not df.empty and _has_price_cols(df):
                return df, f"Historisch (ods134) — {len(df)} kwartieren"
        except Exception:
            pass

        # 3. Fallback naar gisteren als referentie
        if target_date == today:
            try:
                df = self.get_imbalance_prices(
                    dt.datetime.combine(yesterday, dt.time.min),
                    dt.datetime.combine(today, dt.time.min),
                )
                if not df.empty and _has_price_cols(df):
                    return df, f"⚠️ Gisteren ({yesterday}) als referentie — vandaag nog geen data"
            except Exception:
                pass

        return _empty_imbalance_df(), "Geen data beschikbaar"

    def get_latest_imbalance(self) -> Dict[str, Any]:
        """Meest recente kwartier snapshot voor live dashboard tile."""
        try:
            df = self.get_realtime_imbalance()
        except Exception as e:
            return {"status": f"Fout: {e}", "nrv_mw": None}

        if df.empty:
            return {
                "status": "Geen data — begin van de dag, eerste kwartier nog niet verstreken",
                "nrv_mw": None,
                "tip": "Data verschijnt na ~00:15 CET. Probeer over enkele minuten opnieuw."
            }

        r   = df.iloc[-1]
        nrv = float(r.get("nrv_mw", 0) or 0)
        mip = r.get("mip_eur_mwh")
        mdp = r.get("mdp_eur_mwh")
        grid = ("⚡ SHORT — ontladen = winstgevend" if nrv > 50
                else "🌊 LONG — laden wordt betaald" if nrv < -50
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
        """EMS-advies dict voor één dag (met slimme fallback)."""
        df, source = self.get_imbalance_best_available(target_date)

        if df.empty:
            return {"status": "Geen Elia data", "source": source}

        nrv = df.get("nrv_mw", pd.Series(dtype=float))
        mip = df.get("mip_eur_mwh", pd.Series(dtype=float))
        mdp = df.get("mdp_eur_mwh", pd.Series(dtype=float))

        return {
            "date":               str(target_date),
            "source":             source,
            "quarters_analyzed":  len(df),
            "grid_short_qtrs":    int((nrv > 50).sum()),
            "grid_long_qtrs":     int((nrv < -50).sum()),
            "avg_mip_eur_mwh":    _safe_round(mip.mean()),
            "avg_mdp_eur_mwh":    _safe_round(mdp.mean()),
            "peak_mip_eur_mwh":   _safe_round(mip.max()),
            "discharge_opportunity": bool(int((nrv > 50).sum()) > 8),
            "charge_opportunity":    bool(int((nrv < -50).sum()) > 8),
            "status": "OK",
        }

    # ─────────────────────────────────────────────────────────────────────────
    # SOLAR PV FORECAST
    # ─────────────────────────────────────────────────────────────────────────

    def get_solar_forecast(self, region: str | None = None) -> pd.DataFrame:
        """
        Actuele solar PV forecast voor België (ods087).
        Bevat: intraday forecast, day-ahead forecast, week-ahead forecast.
        Kwartierlijks, ververst elke 15 minuten.

        EMS gebruik:
          - Hoge solar forecast morgen 10u-14u → verwacht negatieve/lage prijzen
            → ideaal moment om te laden
          - Combineer met day-ahead ENTSO-E prijzen voor optimale MILP input

        Parameters:
          region: None (heel België) of 'Flanders', 'Wallonia', 'Brussels'
        """
        try:
            df = self._c.get_solar_power_estimation_and_forecast(region=region)
            return self._standardize_solar(df)
        except Exception as e:
            print(f"[Elia] ods087 solar forecast fout: {e}")
            return _empty_solar_df()

    def get_historical_solar(
        self,
        start: dt.date | dt.datetime,
        end:   dt.date | dt.datetime,
        region: str | None = None,
    ) -> pd.DataFrame:
        """
        Historische solar PV productie + forecast (ods032).
        Gemeten en upscaled zonnepanelen productie op het Belgische net.

        Nuttig voor:
          - Backtesting: correleer zonne-energie productie met prijsdalingen
          - Training van een ML-prijsmodel
          - Validatie van solar forecast vs realisatie
        """
        try:
            df = self._c.get_historical_solar_power_estimation_and_forecast(
                start=_to_dt(start), end=_to_dt(end), region=region)
            return self._standardize_solar(df)
        except Exception as e:
            print(f"[Elia] ods032 historische solar fout: {e}")
            return _empty_solar_df()

    def get_solar_ems_advice(self) -> Dict[str, Any]:
        """
        Vertaalt de solar forecast naar concreet EMS-laadadvies.
        Kijkt naar de day-ahead forecast voor morgen en geeft een aanbeveling.
        """
        df = self.get_solar_forecast()

        if df.empty:
            return {"status": "Geen solar data", "advice": "Gebruik standaard MILP strategie"}

        today    = dt.date.today()
        tomorrow = today + dt.timedelta(days=1)

        # Isoleer dag-ahead forecast voor morgen
        tomorrow_df = df[df["datetime"].dt.date == tomorrow] if "datetime" in df.columns else pd.DataFrame()

        # Zoek de peak forecast kolom
        da_col = _find_col(df, ["dayaheadforecast", "day_ahead", "dayahead", "mostrecentforecast"])
        meas_col = _find_col(df, ["measured", "realtime", "upscaled"])

        result = {
            "status":   "OK",
            "columns":  list(df.columns),
            "today_rows":    int((df["datetime"].dt.date == today).sum()) if "datetime" in df.columns else 0,
            "tomorrow_rows": int(len(tomorrow_df)),
        }

        if da_col and not tomorrow_df.empty:
            peak_mw  = tomorrow_df[da_col].max()
            peak_time = tomorrow_df.loc[tomorrow_df[da_col].idxmax(), "datetime"] \
                        if "datetime" in tomorrow_df.columns else None
            total_mwh = tomorrow_df[da_col].sum() * 0.25  # kwartierlijks → MWh

            result.update({
                "tomorrow_peak_mw":  round(float(peak_mw),  1) if pd.notna(peak_mw)  else None,
                "tomorrow_peak_time": str(peak_time)[:16]       if peak_time is not None else None,
                "tomorrow_total_mwh": round(float(total_mwh), 0) if pd.notna(total_mwh) else None,
                "forecast_column":   da_col,
                "advice": (
                    f"☀️ Hoge solar verwacht morgen (piek {peak_mw:.0f} MW rond {str(peak_time)[:16]}). "
                    "Verwacht lage/negatieve prijzen 10u-14u. Optimaliseer laadstrategie voor die periode."
                    if pd.notna(peak_mw) and peak_mw > 2000
                    else "🌤️ Matige solar verwacht morgen. Standaard MILP arbitrage."
                )
            })
        else:
            result["advice"] = "Day-ahead solar forecast niet beschikbaar — standaard strategie"

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Interne helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _standardize_imbalance(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normaliseer ruwe Elia imbalance DataFrame."""
        if df.empty:
            return _empty_imbalance_df()
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

        for col in ["nrv_mw","si_mw","mip_eur_mwh","mdp_eur_mwh","alpha"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df.sort_values("datetime").reset_index(drop=True)

    def _standardize_solar(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normaliseer ruwe Elia solar DataFrame."""
        if df.empty:
            return _empty_solar_df()
        if df.index.name == "datetime" or isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()
        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
        for col in df.select_dtypes(include="object").columns:
            if col != "datetime" and "region" not in col.lower():
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.sort_values("datetime").reset_index(drop=True) if "datetime" in df.columns else df


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _to_dt(d):
    return d if isinstance(d, dt.datetime) else dt.datetime(d.year, d.month, d.day)

def _safe_round(val, decimals=2):
    try:
        return round(float(val), decimals) if pd.notna(val) else None
    except Exception:
        return None

def _has_price_cols(df: pd.DataFrame) -> bool:
    return any(c in df.columns for c in ["mip_eur_mwh", "mdp_eur_mwh", "nrv_mw"])

def _find_col(df: pd.DataFrame, candidates: list) -> str | None:
    col_lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in col_lower:
            return col_lower[cand.lower()]
    return None

def _empty_imbalance_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["datetime","nrv_mw","si_mw","mip_eur_mwh",
                                   "mdp_eur_mwh","alpha","cip_eur_mwh","cdp_eur_mwh"])

def _empty_solar_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["datetime","measured","dayaheadforecast",
                                   "weekaheadforecast","mostrecentforecast","region"])


if __name__ == "__main__":
    client = EliaClient()

    print("=== Imbalance snapshot ===")
    snap = client.get_latest_imbalance()
    for k, v in snap.items(): print(f"  {k}: {v}")

    print("\n=== Solar forecast (ods087) ===")
    df_sol = client.get_solar_forecast()
    if not df_sol.empty:
        print(f"  {len(df_sol)} rijen | kolommen: {list(df_sol.columns)}")
        print(df_sol.head(3).to_string())
    else:
        print("  Geen data")

    print("\n=== Solar EMS advies ===")
    advice = client.get_solar_ems_advice()
    for k, v in advice.items(): print(f"  {k}: {v}")
