"""Elia Open Data endpoints — /api/elia/imbalance  en  /api/elia/solar-wind"""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter(tags=["elia"])


def _get_client():
    """Lazy import van EliaClient (vereist elia-py)."""
    try:
        from elia_client import EliaClient
        return EliaClient()
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"elia-py niet geinstalleerd: {exc}. Voer 'pip install elia-py' uit.",
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Elia client niet beschikbaar: {exc}")


# ---------------------------------------------------------------------------
# GET /api/elia/imbalance
# ---------------------------------------------------------------------------

@router.get("/elia/imbalance")
async def get_imbalance(
    date_: date = Query(None, alias="date", description="Datum (YYYY-MM-DD). Zonder parameter: vandaag."),
):
    """
    Elia onbalansdata (NRV, MIP, MDP) per kwartier.

    Zonder `date` parameter: real-time snapshot van vandaag.
    Met `date`: historisch profiel via ods134/ods047 met slimme fallback.

    Velden per kwartier:
    - `datetime`: tijdstip (UTC)
    - `nrv_mw`: netto reguleringsvolume in MW (positief = tekort, negatief = overschot)
    - `mip_eur_mwh`: marginale incrementele prijs (€/MWh)
    - `mdp_eur_mwh`: marginale decrementele prijs (€/MWh)
    - `si_mw`: systeemonbalans in MW
    """
    client = _get_client()
    target = date_ if date_ is not None else date.today()

    try:
        df, source = client.get_imbalance_best_available(target)
    except Exception as exc:
        logger.exception("Elia imbalance fetch mislukt voor %s", target)
        raise HTTPException(status_code=502, detail=str(exc))

    if df is None or df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"Geen imbalancedata beschikbaar voor {target}. Bron: {source}",
        )

    # Converteer DataFrame naar lijst van dicts (serialiseerbaar)
    records = []
    for _, row in df.iterrows():
        record = {"datetime": str(row.get("datetime", ""))}
        for col in ["nrv_mw", "si_mw", "mip_eur_mwh", "mdp_eur_mwh", "alpha"]:
            val = row.get(col)
            if val is not None:
                try:
                    record[col] = round(float(val), 3)
                except (TypeError, ValueError):
                    record[col] = None
        records.append(record)

    return {
        "date":    str(target),
        "source":  source,
        "count":   len(records),
        "records": records,
    }


# ---------------------------------------------------------------------------
# GET /api/elia/solar-wind
# ---------------------------------------------------------------------------

@router.get("/elia/solar-wind")
async def get_solar_wind():
    """
    Elia zon- en windproductieforecast voor vandaag en morgen.

    Combineert:
    - Solar PV forecast (ods087): intraday + day-ahead + week-ahead
    - Wind forecast (ods086): intraday + day-ahead + week-ahead

    Bevat ook de gecombineerde hernieuwbaar-surplus index met verwacht
    prijseffect in EUR/MWh per kwartier (bruikbaar als MILP-signaalbron).

    EMS-advies:
    - `solar_advice`: laadaanbeveling op basis van solar piek
    - `wind_solar_advice`: gecombineerd zon+wind advies
    - `surplus_forecast`: kwartierlijkse surplus + prijsaanpassing
    """
    client = _get_client()

    # Solar forecast
    try:
        solar_df = client.get_solar_forecast()
    except Exception as exc:
        logger.warning("Solar forecast mislukt: %s", exc)
        solar_df = None

    # Wind forecast
    try:
        wind_df = client.get_wind_forecast()
    except Exception as exc:
        logger.warning("Wind forecast mislukt: %s", exc)
        wind_df = None

    # EMS-adviezen
    try:
        solar_advice = client.get_solar_ems_advice()
    except Exception as exc:
        solar_advice = {"status": f"Fout: {exc}"}

    try:
        wind_solar_advice = client.get_wind_solar_ems_advice()
    except Exception as exc:
        wind_solar_advice = {"status": f"Fout: {exc}"}

    # Surplus forecast per kwartier
    try:
        surplus_df = client.get_renewable_surplus_forecast()
        surplus_records = surplus_df.to_dict(orient="records") if surplus_df is not None and not surplus_df.empty else []
        # datetime objecten naar string
        for r in surplus_records:
            if "datetime" in r:
                r["datetime"] = str(r["datetime"])
    except Exception as exc:
        logger.warning("Surplus forecast mislukt: %s", exc)
        surplus_records = []

    def _df_to_list(df):
        if df is None or df.empty:
            return []
        recs = df.to_dict(orient="records")
        for r in recs:
            if "datetime" in r:
                r["datetime"] = str(r["datetime"])
        return recs

    return {
        "solar_forecast":    _df_to_list(solar_df),
        "wind_forecast":     _df_to_list(wind_df),
        "surplus_forecast":  surplus_records,
        "solar_advice":      solar_advice,
        "wind_solar_advice": wind_solar_advice,
    }
