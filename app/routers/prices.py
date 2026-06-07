"""Prijsdata endpoints — /api/prices/day-ahead  en  /api/prices/history"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import PriceRecord, PricesResponse, PriceSource

logger = logging.getLogger(__name__)
router = APIRouter(tags=["prices"])


def _get_entsoe_client():
    api_key = os.getenv("ENTSOE_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="ENTSOE_API_KEY niet geconfigureerd. Stel in via Render dashboard → Environment.",
        )
    try:
        from entsoe_client import EntsoeClient
        return EntsoeClient(api_key=api_key)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"ENTSO-E client init mislukt: {exc}")


def _df_to_records(df) -> list:
    records = []
    for _, row in df.iterrows():
        records.append(PriceRecord(
            timestamp=str(row["datetime"]),
            price_eur_mwh=float(row["price_eur_mwh"]),
        ))
    return records


# ---------------------------------------------------------------------------
# GET /api/prices/day-ahead
# ---------------------------------------------------------------------------

@router.get("/prices/day-ahead", response_model=PricesResponse)
async def get_day_ahead_prices(
    days: int = Query(7, ge=1, le=30, description="Aantal dagen historiek + vandaag + morgen"),
):
    """
    Dag-vooruit elektriciteitsprijzen (ENTSO-E A44) voor België.
    `days` bepaalt hoeveel dagen historiek getoond worden (+ vandaag + morgen als beschikbaar).
    """
    today = date.today()
    start = today - timedelta(days=days - 1)
    end   = today + timedelta(days=2)   # ENTSO-E end is exclusief

    client = _get_entsoe_client()
    try:
        df = client.get_day_ahead_prices(start=start, end=end)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("ENTSO-E day-ahead fetch mislukt")
        raise HTTPException(status_code=502, detail=f"ENTSO-E fetch mislukt: {exc}")

    if df is None or df.empty:
        raise HTTPException(status_code=404, detail="Geen prijsdata beschikbaar voor dit bereik.")

    records = _df_to_records(df)
    return PricesResponse(
        source=PriceSource.entsoe,
        start=str(start),
        end=str(today + timedelta(days=1)),
        records=records,
        count=len(records),
    )


# ---------------------------------------------------------------------------
# GET /api/prices/history
# ---------------------------------------------------------------------------

@router.get("/prices/history", response_model=PricesResponse)
async def get_price_history(
    start: date = Query(..., description="Startdatum (YYYY-MM-DD)"),
    end:   date = Query(..., description="Einddatum exclusief (YYYY-MM-DD)"),
):
    """Historische dag-vooruit elektriciteitsprijzen. Maximaal 365 dagen."""
    if start >= end:
        raise HTTPException(status_code=422, detail="`start` moet voor `end` liggen.")
    if (end - start).days > 365:
        raise HTTPException(status_code=422, detail="Maximaal bereik is 365 dagen.")

    client = _get_entsoe_client()
    try:
        df = client.get_day_ahead_prices(start=start, end=end)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("ENTSO-E historische fetch mislukt")
        raise HTTPException(status_code=502, detail=f"ENTSO-E fetch mislukt: {exc}")

    if df is None or df.empty:
        raise HTTPException(status_code=404, detail="Geen historische prijsdata beschikbaar.")

    records = _df_to_records(df)
    return PricesResponse(
        source=PriceSource.entsoe,
        start=str(start),
        end=str(end),
        records=records,
        count=len(records),
    )
