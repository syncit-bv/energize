"""Elia Open Data endpoints — /api/elia/imbalance  en  /api/elia/solar-wind"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter(tags=["elia"])


def _client():
    from elia_client import EliaClient
    return EliaClient()


# ---------------------------------------------------------------------------
# GET /api/elia/imbalance
# ---------------------------------------------------------------------------

@router.get("/elia/imbalance")
async def get_imbalance(
    date_: date = Query(None, alias="date", description="Datum (YYYY-MM-DD). Standaard: gisteren."),
):
    """
    Belgische systeemonbalans per kwartier (Elia ods047).
    Velden: timestamp, system_imbalance (MW), nrv (MW), alpha (€/MWh).
    """
    target = date_ if date_ is not None else (date.today() - timedelta(days=1))
    try:
        rows = _client().get_imbalance(target)
    except Exception as exc:
        logger.exception("Elia imbalance fetch mislukt voor %s", target)
        raise HTTPException(status_code=502, detail=f"Elia fetch mislukt: {exc}")

    return {"date": str(target), "count": len(rows), "data": rows}


# ---------------------------------------------------------------------------
# GET /api/elia/solar-wind
# ---------------------------------------------------------------------------

@router.get("/elia/solar-wind")
async def get_solar_wind(
    date_: date = Query(None, alias="date", description="Datum (YYYY-MM-DD). Standaard: gisteren."),
):
    """
    Belgische zon- en windproductie per kwartier (Elia ods087 + ods086).
    Velden: timestamp, solar_mw, wind_onshore_mw, wind_offshore_mw.
    """
    target = date_ if date_ is not None else (date.today() - timedelta(days=1))
    try:
        rows = _client().get_solar_wind(target)
    except Exception as exc:
        logger.exception("Elia solar-wind fetch mislukt voor %s", target)
        raise HTTPException(status_code=502, detail=f"Elia fetch mislukt: {exc}")

    return {"date": str(target), "count": len(rows), "data": rows}
