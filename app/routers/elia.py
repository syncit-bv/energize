"""Elia Open Data endpoints — /api/elia/..."""
from __future__ import annotations

import logging
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import (
    EliaImbalanceRecord,
    EliaImbalanceResponse,
    EliaSolarRecord,
    EliaWindRecord,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["elia"])


def _get_client():
    try:
        from elia_client import EliaClient
        return EliaClient()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Elia client niet beschikbaar: {exc}")


@router.get("/elia/imbalance/latest")
async def get_imbalance_latest():
    """Meest recente imbalance snapshot van Elia."""
    client = _get_client()
    try:
        data = client.get_current_system_imbalance()
    except Exception as exc:
        logger.exception("Elia imbalance latest fetch mislukt")
        raise HTTPException(status_code=502, detail=str(exc))
    return data


@router.get("/elia/imbalance", response_model=EliaImbalanceResponse)
async def get_imbalance(
    date_: date = Query(..., alias="date", description="Datum (YYYY-MM-DD)"),
):
    """Elia imbalance profiel voor een specifieke dag."""
    client = _get_client()
    try:
        df = client.get_imbalance_prices_per_quarter_hour(str(date_), str(date_))
    except Exception as exc:
        logger.exception("Elia imbalance fetch mislukt")
        raise HTTPException(status_code=502, detail=str(exc))

    if df is None or df.empty:
        raise HTTPException(status_code=404, detail="Geen imbalancedata voor deze datum.")

    records = [
        EliaImbalanceRecord(
            timestamp=str(ts),
            nrv=row.get("nrv"),
            alpha=row.get("alpha"),
        )
        for ts, row in df.iterrows()
    ]
    return EliaImbalanceResponse(date=str(date_), records=records)


@router.get("/elia/solar/forecast")
async def get_solar_forecast():
    """Elia zonne-energie forecast voor vandaag en morgen."""
    client = _get_client()
    try:
        data = client.get_solar_power_estimation_and_forecast()
    except Exception as exc:
        logger.exception("Elia solar forecast mislukt")
        raise HTTPException(status_code=502, detail=str(exc))
    return data


@router.get("/elia/solar/history")
async def get_solar_history(
    start: date = Query(..., description="Startdatum (YYYY-MM-DD)"),
    end: date = Query(..., description="Einddatum (YYYY-MM-DD)"),
):
    """Historische Elia zonne-energieproductie."""
    client = _get_client()
    try:
        df = client.get_solar_power_estimation_and_forecast(str(start), str(end))
    except Exception as exc:
        logger.exception("Elia solar history mislukt")
        raise HTTPException(status_code=502, detail=str(exc))
    if df is None or (hasattr(df, "empty") and df.empty):
        raise HTTPException(status_code=404, detail="Geen zonnenergie data voor dit bereik.")
    return df if isinstance(df, dict) else df.to_dict(orient="records")


@router.get("/elia/wind/surplus")
async def get_wind_surplus():
    """Elia wind surplus data."""
    client = _get_client()
    try:
        data = client.get_wind_power_estimation_and_forecast()
    except Exception as exc:
        logger.exception("Elia wind surplus mislukt")
        raise HTTPException(status_code=502, detail=str(exc))
    return data
