"""MILP optimalisatie endpoint — POST /api/optimization/run
                                  GET  /api/optimization/yesterday-soc
                                  GET  /api/optimization/battery-sizing"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from app.models.schemas import (
    BatterySizingResponse, BatterySizingResult,
    JobResponse, JobStatus, OptimizeRequest,
)
from app.services.job_manager import job_manager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["optimization"])

# ---------------------------------------------------------------------------
# Gisteren's SOC cache (reset elke dag automatisch)
# ---------------------------------------------------------------------------
_yesterday_soc_cache: dict = {
    "date":          None,   # "YYYY-MM-DD" van gisteren
    "final_soc_pct": None,   # finale SOC in % (0–100)
    "computed_at":   None,   # ISO-8601 UTC
    "status":        None,   # "ok" of foutmelding
}

# ---------------------------------------------------------------------------
# Battery Sizing cache (reset elke dag automatisch; keyed op power_kw + days)
# ---------------------------------------------------------------------------
_sizing_cache: dict = {}   # key = (date_str, days, power_kw_rounded) → {"results": [...], "computed_at": ...}


def _build_prices_df(prices: List[float]) -> pd.DataFrame:
    """
    Zet een lijst van float-prijzen (EUR/MWh, kwartier-resolutie) om naar
    een DataFrame met kolommen `datetime` en `price_eur_mwh`, zoals verwacht
    door milp_optimizer.optimize_battery_schedule().
    Tijdstempels starten op vandaag 00:00 UTC, stap 15 minuten.
    """
    base = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    timestamps = pd.date_range(start=base, periods=len(prices), freq="15min", tz="UTC")
    return pd.DataFrame({"datetime": timestamps, "price_eur_mwh": prices})


def _milp_background(job_id: str, req: OptimizeRequest) -> None:
    """
    Achtergrondthread: voert MILP-optimalisatie uit en slaat resultaat op in JobManager.

    Keuze van optimizer (in volgorde van voorkeur):
      1. wind_solar — als wind_forecast EN solar_forecast aanwezig zijn
      2. solar      — als alleen solar_forecast aanwezig is
      3. imbalance  — als imbalance_prices aanwezig zijn
      4. basic      — fallback

    De MILP-functies zijn niet thread-safe (PuLP/CBC), vandaar het semaphore.
    """
    try:
        import milp_optimizer as mo
    except ImportError as exc:
        job_manager.set_failed(job_id, f"milp_optimizer import mislukt: {exc}")
        return

    job_manager.set_running(job_id)

    try:
        prices_df = _build_prices_df(req.prices)

        # Optionele invoer: solar als pd.Series met DatetimeIndex
        solar_series: Optional[pd.Series] = None
        if req.solar_forecast:
            idx = pd.date_range(
                start=prices_df["datetime"].iloc[0],
                periods=len(req.solar_forecast),
                freq="15min",
                tz="UTC",
            )
            # solar_forecast in kW per kwartier → kWh per kwartier (× 0.25)
            solar_series = pd.Series(
                [v * 0.25 for v in req.solar_forecast], index=idx, name="own_solar_kwh"
            )

        wind_adj_series: Optional[pd.Series] = None
        if req.wind_forecast:
            idx = pd.date_range(
                start=prices_df["datetime"].iloc[0],
                periods=len(req.wind_forecast),
                freq="15min",
                tz="UTC",
            )
            # wind_forecast bevat prijsaanpassingen in EUR/MWh
            wind_adj_series = pd.Series(req.wind_forecast, index=idx, name="wind_adj")

        imbalance_series: Optional[pd.Series] = None
        if req.imbalance_prices:
            idx = pd.date_range(
                start=prices_df["datetime"].iloc[0],
                periods=len(req.imbalance_prices),
                freq="15min",
                tz="UTC",
            )
            imbalance_series = pd.Series(req.imbalance_prices, index=idx, name="imbalance_mid")

        # Gemeenschappelijke MILP-parameters
        # discharge_power_kw = max ontlaadvermogen (injectie), fallback op charge_power_kw
        # min_end_soc = minimum eindstatus SOC, fallback op min_soc
        common = dict(
            battery_kwh      = req.battery_kwh,
            max_power_kw     = req.discharge_power_kw if req.discharge_power_kw is not None else req.charge_power_kw,
            charge_power_kw  = req.charge_power_kw,
            efficiency       = req.efficiency,
            initial_soc      = req.initial_soc,
            min_soc          = req.min_soc,
            min_end_soc      = req.min_end_soc if req.min_end_soc is not None else req.min_soc,
        )

        # Kies optimizer op basis van beschikbare data
        with job_manager.milp_semaphore:
            if wind_adj_series is not None and solar_series is not None:
                label = "MILP+Solar+Wind"
                result_df, summary = mo.optimize_battery_schedule_wind_solar(
                    prices_df            = prices_df,
                    solar_kwh_per_slot   = solar_series,
                    wind_price_adj_per_slot = wind_adj_series,
                    label                = label,
                    **common,
                )
            elif solar_series is not None:
                label = "MILP+Solar"
                result_df, summary = mo.optimize_battery_schedule_solar(
                    prices_df          = prices_df,
                    solar_kwh_per_slot = solar_series,
                    label              = label,
                    **common,
                )
            elif imbalance_series is not None:
                label = "MILP+Imbalance"
                result_df, summary = mo.optimize_battery_schedule_imbalance(
                    prices_df              = prices_df,
                    imbalance_adj_per_slot = imbalance_series,
                    label                  = label,
                    **common,
                )
            else:
                label = "MILP"
                result_df, summary = mo.optimize_battery_schedule(
                    prices_df = prices_df,
                    label     = label,
                    **common,
                )

        # Serialiseer schedule (datetime-kolom is niet JSON-serialiseerbaar)
        schedule_records: List[Dict[str, Any]] = []
        for _, row in result_df.iterrows():
            schedule_records.append({
                "datetime":         str(row["datetime"]),
                "price_eur_mwh":    round(float(row["price_eur_mwh"]), 4),
                "charge_kwh":       round(float(row.get("charge_kwh", 0)), 4),
                "charge_grid_kwh":  round(float(row.get("charge_grid_kwh", 0)), 4),
                "charge_solar_kwh": round(float(row.get("charge_solar_kwh", 0)), 4),
                "discharge_kwh":    round(float(row.get("discharge_kwh", 0)), 4),
                "soc_kwh":          round(float(row.get("soc_kwh", 0)), 4),
                "soc_pct":          round(float(row.get("soc_pct", 0)), 2),
                "net_revenue_eur":  round(float(row.get("net_revenue_eur", 0)), 6),
            })

        result_payload: Dict[str, Any] = {
            "label":   summary.get("label", label),
            "status":  summary.get("status", "Unknown"),
            "summary": {
                k: v for k, v in summary.items()
                if k not in ("solver_log",)  # log niet meesturen (kan groot zijn)
                and isinstance(v, (int, float, str, bool, type(None)))
            },
            "schedule": schedule_records,
        }

        job_manager.set_completed(job_id, result_payload)

    except Exception as exc:
        logger.exception("MILP achtergrondtaak mislukt (job=%s)", job_id)
        job_manager.set_failed(job_id, str(exc))


# ---------------------------------------------------------------------------
# POST /api/optimize/run
# ---------------------------------------------------------------------------

@router.post("/optimization/run", response_model=JobResponse, status_code=202)
async def run_optimization(req: OptimizeRequest, background_tasks: BackgroundTasks):
    """
    Start een MILP-optimalisatieberekening als achtergrondtaak.

    Geeft onmiddellijk een `job_id` terug. Peilen van status en resultaat via
    `GET /api/jobs/{job_id}`.

    **Verplicht:**
    - `prices`: lijst van day-ahead prijzen in EUR/MWh (kwartier-resolutie, minimaal 4 waarden)

    **Optioneel (activeren extra scenario):**
    - `solar_forecast`: eigen solar productie in kW per kwartier
    - `wind_forecast`: wind prijsaanpassingen in EUR/MWh per kwartier
    - `imbalance_prices`: imbalance MIP/MDP gemiddelde in EUR/MWh per kwartier

    **Optimizer-selectie (automatisch):**
    - wind + solar beschikbaar  → MILP+Solar+Wind
    - alleen solar              → MILP+Solar
    - alleen imbalance          → MILP+Imbalance
    - geen extra data           → MILP basis arbitrage
    """
    if len(req.prices) < 4:
        raise HTTPException(
            status_code=422,
            detail="Minimaal 4 prijspunten vereist (= 1 uur kwartierdata).",
        )

    job_id = job_manager.create_job()

    # Start thread via BackgroundTasks zodat FastAPI de response al kan sturen
    background_tasks.add_task(
        lambda: threading.Thread(
            target=_milp_background,
            args=(job_id, req),
            daemon=True,
            name=f"milp-{job_id[:8]}",
        ).start()
    )

    return JobResponse(job_id=job_id, status=JobStatus.pending, progress=0)


# ---------------------------------------------------------------------------
# GET /api/optimization/yesterday-soc
# ---------------------------------------------------------------------------

@router.get("/optimization/yesterday-soc")
async def get_yesterday_soc():
    """
    Berekent de optimale finale SOC van gisteren via MILP met standaard parameters.
    Geeft de aanbevolen start-SOC voor vandaag terug.
    Resultaat wordt gecached voor de rest van de dag (reset bij datumovergang).
    """
    yesterday = str(date.today() - timedelta(days=1))

    # Gebruik cache als die nog van gisteren is
    if _yesterday_soc_cache["date"] == yesterday and _yesterday_soc_cache["final_soc_pct"] is not None:
        logger.debug("[yesterday-soc] Cache hit voor %s", yesterday)
        return _yesterday_soc_cache

    api_key = os.getenv("ENTSOE_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="ENTSOE_API_KEY niet geconfigureerd.")

    # Haal gisteren's prijzen op
    try:
        from entsoe_client import EntsoeClient
        yesterday_date = date.today() - timedelta(days=1)
        client = EntsoeClient(api_key=api_key)
        df = client.get_day_ahead_prices(start=yesterday_date, end=date.today())
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("[yesterday-soc] ENTSO-E fetch mislukt: %s", exc)
        raise HTTPException(status_code=502, detail=f"ENTSO-E fetch mislukt: {exc}")

    if df is None or df.empty:
        raise HTTPException(status_code=404, detail="Geen prijsdata beschikbaar voor gisteren.")

    prices = list(df["price_eur_mwh"].astype(float))

    # Run MILP in threadpool executor (synchrone PuLP/HiGHS mag event loop niet blokkeren)
    def _run_milp_yesterday() -> tuple[float | None, str]:
        try:
            import milp_optimizer as mo
            import pandas as pd
            base = datetime.now(tz=timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) - timedelta(days=1)
            timestamps = pd.date_range(start=base, periods=len(prices), freq="15min", tz="UTC")
            prices_df = pd.DataFrame({"datetime": timestamps, "price_eur_mwh": prices})
            with job_manager.milp_semaphore:
                result_df, _ = mo.optimize_battery_schedule(
                    prices_df       = prices_df,
                    battery_kwh     = 10.0,
                    max_power_kw    = 5.0,
                    charge_power_kw = 5.0,
                    efficiency      = 0.95,
                    initial_soc     = 0.5,
                    min_soc         = 0.1,
                    min_end_soc     = 0.1,
                )
            final_soc = float(result_df["soc_pct"].iloc[-1])
            return round(final_soc, 1), "ok"
        except Exception as exc:
            logger.warning("[yesterday-soc] MILP mislukt: %s", exc)
            return None, str(exc)

    loop = asyncio.get_event_loop()
    final_soc_pct, status = await loop.run_in_executor(None, _run_milp_yesterday)

    now_str = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _yesterday_soc_cache.update({
        "date":          yesterday,
        "final_soc_pct": final_soc_pct,
        "computed_at":   now_str,
        "status":        status,
    })

    if final_soc_pct is None:
        raise HTTPException(status_code=500, detail=f"MILP berekening mislukt: {status}")

    logger.info("[yesterday-soc] ✅ Finale SOC gisteren: %.1f%% (berekend op %s)", final_soc_pct, now_str)
    return _yesterday_soc_cache


# ---------------------------------------------------------------------------
# GET /api/optimization/battery-sizing
# ---------------------------------------------------------------------------

_SIZING_SIZES_KWH = [2.0, 5.0, 10.0, 15.0, 20.0, 30.0, 50.0]


# ---------------------------------------------------------------------------
# POST /api/optimization/battery-sizing/start  — start achtergrondtaak
# ---------------------------------------------------------------------------

@router.post("/optimization/battery-sizing/start")
async def start_battery_sizing(
    background_tasks: BackgroundTasks,
    days:       int   = Query(365, ge=7,  le=365, description="Analyseer periode in dagen (max 365)"),
    power_kw:   float = Query(5.0, gt=0,  le=50,  description="Max ontlaadvermogen in kW"),
    efficiency: float = Query(0.95, gt=0, le=1,   description="Round-trip efficiëntie"),
):
    """
    Start Battery Sizing Advisor als achtergrondtaak. Geeft direct een job_id terug.
    Peil voortgang via GET /api/jobs/{job_id}.

    Als het resultaat al gecached is (zelfde dag + parameters), is de job meteen 'completed'.
    """
    today_str = str(date.today())
    cache_key = (today_str, days, round(power_kw, 1), round(efficiency, 3))

    job_id = job_manager.create_job()

    if cache_key in _sizing_cache:
        logger.debug("[sizing] Cache hit → instant completed job %s", job_id)
        job_manager.set_completed(job_id, _sizing_cache[cache_key])
        return {"job_id": job_id}

    background_tasks.add_task(_sizing_background, job_id, power_kw, efficiency, days, cache_key)
    logger.info("[sizing] Achtergrondtaak gestart: job=%s power=%.1f eff=%.2f days=%d",
                job_id, power_kw, efficiency, days)
    return {"job_id": job_id}


def _sizing_background(
    job_id: str, power_kw: float, efficiency: float, days: int, cache_key: tuple
) -> None:
    """
    Sync achtergrondtaak: haalt ENTSO-E data op en draait MILP voor elke batterijgrootte.
    Update job progress na elke grootte zodat de frontend live voortgang kan tonen.
    """
    from app.models.schemas import JobStatus   # lokale import (circular-safe)

    n = len(_SIZING_SIZES_KWH)

    def _progress(i: int, label: str) -> None:
        job_manager._update(job_id,
            status   = JobStatus.running,
            progress = round(i / n * 100),
            message  = label,
        )

    _progress(0, "⬇️ ENTSO-E data ophalen…")

    # -- 1. ENTSO-E prijzen ophalen --
    api_key = os.getenv("ENTSOE_API_KEY", "")
    if not api_key:
        job_manager.set_failed(job_id, "ENTSOE_API_KEY niet geconfigureerd.")
        return

    try:
        from entsoe_client import EntsoeClient
        start_date = date.today() - timedelta(days=days - 1)
        client     = EntsoeClient(api_key=api_key)
        df         = client.get_day_ahead_prices(start=start_date, end=date.today() + timedelta(days=1))
    except Exception as exc:
        job_manager.set_failed(job_id, f"ENTSO-E fetch mislukt: {exc}")
        return

    if df is None or df.empty:
        job_manager.set_failed(job_id, "Geen prijsdata beschikbaar voor de gevraagde periode.")
        return

    prices_df   = df[["datetime", "price_eur_mwh"]].copy()
    actual_days = max(1, len(prices_df) / 96)
    annualize   = 365.0 / actual_days

    logger.info("[sizing] %d slots geladen (≈%.0f dagen) · job=%s", len(prices_df), actual_days, job_id)

    # -- 2. MILP per grootte --
    try:
        import milp_optimizer as mo
    except ImportError as exc:
        job_manager.set_failed(job_id, f"milp_optimizer import mislukt: {exc}")
        return

    results: list[dict] = []

    for i, size_kwh in enumerate(_SIZING_SIZES_KWH):
        _progress(i, f"⚙️ Berekening {i + 1}/{n}: {size_kwh:.0f} kWh batterij…")

        try:
            with job_manager.milp_semaphore:
                _, summary = mo.optimize_battery_schedule(
                    prices_df       = prices_df,
                    battery_kwh     = size_kwh,
                    max_power_kw    = power_kw,
                    charge_power_kw = power_kw,
                    efficiency      = efficiency,
                    initial_soc     = 0.5,
                    min_soc         = 0.1,
                    min_end_soc     = 0.1,
                )
            revenue = float(summary.get("revenue_execute_eur", 0) or 0)
        except Exception as exc:
            logger.warning("[sizing] MILP mislukt voor %.1f kWh: %s", size_kwh, exc)
            revenue = 0.0

        ann_revenue = revenue * annualize
        results.append({
            "battery_kwh":            size_kwh,
            "total_revenue_eur":      round(revenue, 4),
            "annualized_revenue_eur": round(ann_revenue, 2),
            "revenue_per_kwh":        round(revenue / size_kwh, 6) if size_kwh > 0 else 0.0,
            "annualized_per_kwh":     round(ann_revenue / size_kwh, 4) if size_kwh > 0 else 0.0,
        })
        logger.debug("[sizing] %.0f kWh → €%.2f/jaar · job=%s", size_kwh, ann_revenue, job_id)

    payload = {
        "days_analyzed":  round(actual_days),
        "slots_analyzed": len(prices_df),
        "start_date":     str(start_date),
        "end_date":       str(date.today()),
        "power_kw":       power_kw,
        "efficiency":     efficiency,
        "computed_at":    datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "results":        results,
    }

    _sizing_cache[cache_key] = payload
    job_manager.set_completed(job_id, payload)
    logger.info("[sizing] ✅ Klaar · job=%s · %.0f dagen · %d groottes", job_id, actual_days, n)
