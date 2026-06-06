"""MILP optimalisatie endpoint — POST /api/optimize/run"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.models.schemas import JobResponse, JobStatus, OptimizeRequest
from app.services.job_manager import job_manager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["optimization"])


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
        common = dict(
            battery_kwh      = req.battery_kwh,
            max_power_kw     = req.charge_power_kw,
            charge_power_kw  = req.charge_power_kw,
            efficiency       = req.efficiency,
            initial_soc      = req.initial_soc,
            min_soc          = req.min_soc,
            min_end_soc      = req.min_soc,  # gebruik min_soc als ondergrens eindstatus
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

@router.post("/optimize/run", response_model=JobResponse, status_code=202)
async def run_optimization(req: OptimizeRequest, background_tasks: BackgroundTasks):
    """
    Start een MILP-optimalisatieberekening als achtergrondtaak.

    Geeft onmiddellijk een `job_id` terug. Peil de status en het resultaat via
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
