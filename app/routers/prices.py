"""Prijsdata endpoints — /api/prices/day-ahead, /api/prices/history,
                         /api/prices/tomorrow/status, /api/prices/tomorrow/check"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import PriceRecord, PricesResponse, PriceSource

logger    = logging.getLogger(__name__)
router    = APIRouter(tags=["prices"])
_BRUSSELS = ZoneInfo("Europe/Brussels")

# ---------------------------------------------------------------------------
# D+1 in-memory cache (reset elke dag automatisch)
# ---------------------------------------------------------------------------
_tomorrow_cache: dict = {
    "available":          False,
    "first_available_at": None,   # ISO-8601 string, Brussels TZ
    "checked_at":         None,   # ISO-8601 string, Brussels TZ
    "prices_count":       0,
    "date":               None,   # "YYYY-MM-DD" van morgen
}


async def check_tomorrow_prices_task() -> None:
    """
    Achtergrondtaak: pollt ENTSO-E voor morgen's dag-ahead prijzen.
    Draait via APScheduler (12:00–17:00 CET, elke 5 min) én bij startup
    als we al binnen het publicatievenster vallen.
    """
    tomorrow = date.today() + timedelta(days=1)
    now_str  = datetime.now(_BRUSSELS).isoformat(timespec="seconds")

    # Reset cache als de dag veranderd is (middernacht)
    if _tomorrow_cache["date"] and _tomorrow_cache["date"] != str(tomorrow):
        _tomorrow_cache.update({
            "available": False, "first_available_at": None,
            "prices_count": 0,  "date": None,
        })

    api_key = os.getenv("ENTSOE_API_KEY", "")
    if not api_key:
        logger.warning("[D+1] ENTSOE_API_KEY niet ingesteld — check overgeslagen")
        _tomorrow_cache["checked_at"] = now_str
        return

    try:
        from entsoe_client import EntsoeClient
        client = EntsoeClient(api_key=api_key)
        df = client.get_day_ahead_prices(
            start=tomorrow, end=tomorrow + timedelta(days=1)
        )

        if df is not None and not df.empty and len(df) >= 4:
            count = len(df)
            if not _tomorrow_cache["available"]:
                logger.info("[D+1] ✅ Prijzen beschikbaar! %d records voor %s", count, tomorrow)
                _tomorrow_cache["first_available_at"] = now_str
            _tomorrow_cache["available"]    = True
            _tomorrow_cache["prices_count"] = count
        else:
            logger.info("[D+1] Nog niet beschikbaar voor %s", tomorrow)

        _tomorrow_cache["date"]       = str(tomorrow)
        _tomorrow_cache["checked_at"] = now_str

    except Exception as exc:
        logger.warning("[D+1] Check mislukt: %s", exc)
        _tomorrow_cache["checked_at"] = now_str


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
    days: int = Query(1, ge=1, le=365, description="Aantal dagen historiek + vandaag + morgen"),
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


# ---------------------------------------------------------------------------
# GET /api/prices/tomorrow/status  — live badge in de frontend
# ---------------------------------------------------------------------------

@router.get("/prices/tomorrow/status")
async def get_tomorrow_status():
    """
    Status van morgen's dag-ahead prijzen.
    Geeft terug of ze beschikbaar zijn, wanneer ze voor het eerst gezien werden,
    en wanneer de laatste check was. Frontend pollt dit elke 2 minuten.
    """
    tomorrow = str(date.today() + timedelta(days=1))
    # Reset bij datumovergang (middernacht)
    if _tomorrow_cache.get("date") and _tomorrow_cache["date"] != tomorrow:
        _tomorrow_cache.update({
            "available": False, "first_available_at": None,
            "prices_count": 0,  "date": None, "checked_at": None,
        })
    return {
        "available":          _tomorrow_cache["available"],
        "date":               tomorrow,
        "first_available_at": _tomorrow_cache["first_available_at"],
        "checked_at":         _tomorrow_cache["checked_at"],
        "prices_count":       _tomorrow_cache["prices_count"],
    }


# ---------------------------------------------------------------------------
# POST /api/prices/tomorrow/check  — handmatige / geforceerde controle
# ---------------------------------------------------------------------------

@router.post("/prices/tomorrow/check")
async def trigger_tomorrow_check():
    """Forceert een onmiddellijke controle van morgen's prijzen."""
    await check_tomorrow_prices_task()
    return {
        "triggered":  True,
        "available":  _tomorrow_cache["available"],
        "checked_at": _tomorrow_cache["checked_at"],
    }
