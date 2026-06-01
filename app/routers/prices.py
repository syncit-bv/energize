"""Prijsdata endpoints — /api/prices"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import PriceRecord, PricesResponse, PriceSource

logger = logging.getLogger(__name__)
router = APIRouter(tags=["prices"])


def _load_entsoe_client():
    """Lazy import om te vermijden dat ontbrekende env-vars de app crashen bij start."""
    try:
        from entsoe_client import EntsoeClient
        return EntsoeClient()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"ENTSO-E client niet beschikbaar: {exc}")


@router.get("/prices", response_model=PricesResponse)
async def get_prices(
    days: Optional[int] = Query(None, ge=1, le=365, description="Aantal dagen terug vanaf vandaag"),
    start: Optional[date] = Query(None, description="Startdatum (YYYY-MM-DD)"),
    end: Optional[date] = Query(None, description="Einddatum (YYYY-MM-DD)"),
    source: PriceSource = Query(PriceSource.entsoe),
):
    """
    Haal day-ahead elektriciteitsprijzen op.

    Gebruik `days=30` voor een rolling window, of geef `start`+`end` voor een vaste periode.
    """
    # Bepaal datumbereik
    if days is not None:
        end_dt = datetime.utcnow().date()
        start_dt = end_dt - timedelta(days=days)
    elif start and end:
        start_dt, end_dt = start, end
    else:
        raise HTTPException(
            status_code=422,
            detail="Geef `days` op, of beide `start` en `end`.",
        )

    if start_dt >= end_dt:
        raise HTTPException(status_code=422, detail="`start` moet voor `end` liggen.")

    client = _load_entsoe_client()

    try:
        df = client.fetch_prices(str(start_dt), str(end_dt))
    except Exception as exc:
        logger.exception("ENTSO-E fetch mislukt")
        raise HTTPException(status_code=502, detail=f"ENTSO-E fetch mislukt: {exc}")

    if df is None or df.empty:
        raise HTTPException(status_code=404, detail="Geen prijsdata beschikbaar voor dit bereik.")

    records = [
        PriceRecord(timestamp=str(ts), price_eur_mwh=float(price))
        for ts, price in zip(df.index, df.iloc[:, 0])
    ]

    return PricesResponse(
        source=source,
        start=str(start_dt),
        end=str(end_dt),
        records=records,
        count=len(records),
    )
