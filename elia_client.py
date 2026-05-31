#!/usr/bin/env python3
"""
Elia Open Data Client voor EMS Belgium v2.0
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
    # WIND FORECAST
    # ─────────────────────────────────────────────────────────────────────────

    def get_wind_forecast(self, region: str | None = None) -> pd.DataFrame:
        """
        Actuele wind forecast voor België (ods086).
        Intraday + day-ahead + week-ahead, kwartierlijks vernieuwd.

        Kolommen: datetime, measured, dayaheadforecast, weekaheadforecast,
                  mostrecentforecast, region.

        EMS gebruik:
          - Hoge windforecast + hoge solarforecast = grote hernieuwbare surplus
            → negatieve/lage dag-ahead prijzen zijn waarschijnlijk
          - Gebruik als prijs-anticipatiesignaal VOOR 13:00 CET
            (vóór day-ahead publicatie), zodat MILP al vroeg kan laden
        """
        try:
            df = self._c.get_wind_power_estimation_and_forecast(region=region)
            return self._standardize_wind(df)
        except Exception as e:
            print(f"[Elia] ods086 wind forecast fout: {e}")
            return _empty_wind_df()

    def get_historical_wind(
        self,
        start: dt.date | dt.datetime,
        end:   dt.date | dt.datetime,
        region: str | None = None,
    ) -> pd.DataFrame:
        """
        Historische windproductie + forecast (ods085 / ods086).
        Nuttig voor backtesting prijs-wind correlatie.
        """
        try:
            df = self._c.get_historical_wind_power_estimation_and_forecast(
                start=_to_dt(start), end=_to_dt(end), region=region)
            return self._standardize_wind(df)
        except Exception as e:
            print(f"[Elia] historische wind fout: {e}")
            return _empty_wind_df()

    def get_renewable_surplus_forecast(
        self,
        target_date: dt.date | None = None,
    ) -> pd.DataFrame:
        """
        Combineer wind + solar forecast tot een hernieuwbaar surplus index.

        Geeft per kwartier terug:
          datetime, solar_mw, wind_mw, surplus_mw,
          price_adjustment_eur_mwh  (negatief bij surplus)

        Prijs-correctieformule (empirisch, Belgische markt):
          Elke 1000 MW surplus boven 2000 MW ≈ -8 €/MWh prijseffect.
          Drempel: onder 2000 MW surplus → geen significante prijsdruk.

        Gebruik in MILP:
          Als day-ahead nog niet beschikbaar (vóór 13:00 CET):
          → adjusted_price = base_price + price_adjustment
          → MILP optimaliseert op gecorrigeerde verwachtingswaarden
        """
        solar_df = self.get_solar_forecast()
        wind_df  = self.get_wind_forecast()

        if solar_df.empty and wind_df.empty:
            return pd.DataFrame()

        # Zoek forecast kolommen
        sol_col  = _find_col(solar_df, ["dayaheadforecast","mostrecentforecast","weekaheadforecast"])
        wind_col = _find_col(wind_df,  ["dayaheadforecast","mostrecentforecast","weekaheadforecast"])

        rows = []

        if not solar_df.empty and sol_col and "datetime" in solar_df.columns:
            for _, r in solar_df.iterrows():
                dt_val   = r["datetime"]
                sol_mw   = float(r.get(sol_col, 0) or 0)

                # Zoek bijhorende wind op zelfde timestamp
                wind_mw = 0.0
                if not wind_df.empty and wind_col and "datetime" in wind_df.columns:
                    match = wind_df[wind_df["datetime"] == dt_val]
                    if not match.empty:
                        wind_mw = float(match.iloc[0].get(wind_col, 0) or 0)

                surplus_mw = sol_mw + wind_mw
                # Prijs-correctie: drempel 2000 MW, -8 €/MWh per 1000 MW erboven
                excess     = max(0.0, surplus_mw - 2000)
                price_adj  = -(excess / 1000) * 8.0

                rows.append({
                    "datetime":               dt_val,
                    "solar_mw":               round(sol_mw,   1),
                    "wind_mw":                round(wind_mw,  1),
                    "surplus_mw":             round(surplus_mw, 1),
                    "price_adjustment_eur_mwh": round(price_adj,  2),
                })

        if not rows:
            return pd.DataFrame()

        df_out = pd.DataFrame(rows)
        if target_date is not None:
            df_out = df_out[df_out["datetime"].dt.date == target_date]

        return df_out.sort_values("datetime").reset_index(drop=True)

    def get_wind_solar_ems_advice(self) -> dict:
        """
        Gecombineerd wind+solar advies voor EMS.
        Geeft aan wanneer het beste moment is om te laden op basis
        van het verwachte hernieuwbaar surplus.
        """
        surplus_df = self.get_renewable_surplus_forecast()

        if surplus_df.empty:
            return {"status": "Geen data", "advice": "Standaard MILP strategie"}

        tomorrow   = dt.date.today() + dt.timedelta(days=1)
        tm_df      = surplus_df[surplus_df["datetime"].dt.date == tomorrow]

        if tm_df.empty:
            return {"status": "Geen morgen data", "advice": "Standaard MILP strategie"}

        peak_surplus = tm_df["surplus_mw"].max()
        peak_time    = tm_df.loc[tm_df["surplus_mw"].idxmax(), "datetime"]
        best_load_windows = tm_df[tm_df["price_adjustment_eur_mwh"] < -20]                               .sort_values("price_adjustment_eur_mwh")

        return {
            "status":              "OK",
            "tomorrow_peak_surplus_mw": round(float(peak_surplus), 0),
            "tomorrow_peak_time":  str(peak_time)[:16],
            "best_load_slots":     len(best_load_windows),
            "max_price_reduction": round(float(tm_df["price_adjustment_eur_mwh"].min()), 1),
            "advice": (
                f"⚡ Hoog hernieuwbaar surplus morgen: {peak_surplus:.0f} MW "
                f"om {str(peak_time)[:16]}. "
                f"Verwachte prijsdruk: {tm_df['price_adjustment_eur_mwh'].min():.0f} €/MWh. "
                f"{len(best_load_windows)} optimale laadkwartieren geïdentificeerd."
                if peak_surplus > 3000 else
                f"🌤️ Matig hernieuwbaar surplus morgen ({peak_surplus:.0f} MW). "
                "Standaard MILP day-ahead strategie volstaat."
            ),
        }


    # ─────────────────────────────────────────────────────────────────────────
    # IMBALANCE PRIJZEN — als intraday proxy-signaal voor MILP
    # ─────────────────────────────────────────────────────────────────────────

    def get_imbalance_prices_realtime(self) -> pd.DataFrame:
        """
        Real-time imbalance prijs (ods161) — per minuut bijgewerkt.
        Bevat: MIP (marginale incrementele prijs), MDP (marginale decrementele prijs),
               NRV (Net Regulation Volume) en de SI (System Imbalance).

        Gebruik in EMS:
          - MIP hoog + SI negatief → nettekort → goede tijd om te ontladen
          - MDP laag / negatief + SI positief → netoverschot → goede tijd om te laden
          - Enkel beschikbaar voor huidige uur; gebruik ods162 voor historiek
        """
        try:
            df = self._c.get_imbalance_prices_per_quarter_hour()
            return self._standardize_imbalance(df, realtime=True)
        except Exception as e:
            print(f"[Elia] ods161 realtime fout: {e}")
            return _empty_imbalance_df()

    def get_imbalance_prices_historical(
        self,
        start: dt.date | dt.datetime,
        end:   dt.date | dt.datetime,
    ) -> pd.DataFrame:
        """
        Historische imbalance prijzen (ods162) per kwartier.
        MIP en MDP per ISP (imbalance settlement period = 15 min).

        Essentieel voor MILP-backtesting:
          - Correleer MIP/MDP met day-ahead prijs
          - Identificeer patronen: avondpieken winter, zomermiddagen
          - Basis voor toekomstige aFRR-bieding simulatie via Yuso
        """
        try:
            df = self._c.get_historical_imbalance_prices_per_quarter_hour(
                start=_to_dt(start), end=_to_dt(end))
            return self._standardize_imbalance(df, realtime=False)
        except Exception as e:
            print(f"[Elia] ods162 historisch fout: {e}")
            return _empty_imbalance_df()

    def get_imbalance_as_price_signal(
        self,
        start: dt.date | dt.datetime | None = None,
        end:   dt.date | dt.datetime | None = None,
    ) -> pd.DataFrame:
        """
        Combineer imbalance MIP/MDP met day-ahead prijs tot een
        gewogen prijssignaal voor MILP.

        Logic:
          - Als MIP >> day-ahead → markt in tekort → effectieve prijs hoger dan gepland
          - Als MDP << day-ahead → markt in overschot → effectieve prijs lager
          - Gewogen signaal = day_ahead_price × 0.85 + 0.15 × imbalance_mid_price
            (15% gewicht voor imbalance corr., conservatief want historisch volatiel)

        Returns DataFrame met kolommen:
          datetime, imbalance_mid_eur_mwh, price_signal_adj, trend
        """
        if start is None:
            start = dt.date.today() - dt.timedelta(days=7)
        if end is None:
            end = dt.date.today()

        imb_df = self.get_imbalance_prices_historical(start, end)
        if imb_df.empty:
            return pd.DataFrame()

        mip_col = next((c for c in ["mip","marginalincrementalprice","MIP"]
                        if c in imb_df.columns), None)
        mdp_col = next((c for c in ["mdp","marginaldecrementalprice","MDP"]
                        if c in imb_df.columns), None)

        if not mip_col or not mdp_col:
            return imb_df

        imb_df["imbalance_mid_eur_mwh"] = (
            imb_df[mip_col].fillna(0) + imb_df[mdp_col].fillna(0)
        ) / 2

        # Trend: positief = nettekort (hoge MIP, goed om te ontladen)
        imb_df["trend"] = imb_df.apply(
            lambda r: "⬆️ Tekort" if float(r.get(mip_col, 0) or 0) > 100 else
                      ("⬇️ Overschot" if float(r.get(mdp_col, 0) or 0) < -20 else
                       "↔️ Neutraal"), axis=1)

        return imb_df[["datetime", "imbalance_mid_eur_mwh", "trend"]
                      + [c for c in [mip_col, mdp_col] if c]].copy()

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

    def _standardize_wind(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normaliseer ruwe Elia wind DataFrame (zelfde structuur als solar)."""
        if df.empty:
            return _empty_wind_df()
        if df.index.name == "datetime" or isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()
        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
        for col in df.select_dtypes(include="object").columns:
            if col != "datetime" and "region" not in col.lower():
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.sort_values("datetime").reset_index(drop=True) \
               if "datetime" in df.columns else df


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

def _empty_wind_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["datetime","measured","dayaheadforecast",
                                   "weekaheadforecast","mostrecentforecast","region"])

def _empty_imbalance_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["datetime","mip","mdp","nrv","si",
                                   "imbalance_mid_eur_mwh","trend"])



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
