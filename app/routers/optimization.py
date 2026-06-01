"""MILP optimalisatie endpoints — /api/optimize/..."""
from __future__ import annotations

import logging
import threading
from typing import List

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.models.schemas import (
    BatterySizingRequest,
    BatterySizingResponse,
    BatterySizingResult,
    JobResponse,
    JobStatus,
    OptimizeRequest,
    OptimizeResponse,
    ScenarioResult,
    SimulateRequest,
    SimulateResponse,
)
from app.services.job_manager import job_manager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["optimization"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_optimizer():
    try:
        import milp_optimizer as mo
        return mo
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"MILP optimizer niet beschikbaar: {exc}")


# ---------------------------------------------------------------------------
# Synchrone basic optimalisatie (kortere periodes, < 5s)
# ---------------------------------------------------------------------------

@router.post("/optimize/basic", response_model=OptimizeResponse)
async def optimize_basic(req: OptimizeRequest):
    """
    MILP basisoptimalisatie (geen solar/wind/imbalance).
    Geschikt voor periodes tot ~7 dagen (672 kwartierslots).
    Blokkerende call — gebruik /optimize/scenarios voor langere periodes.
    """
    mo = _import_optimizer()
    try:
        result = mo.optimize_battery_schedule(
            prices=req.prices,
            battery_kwh=req.battery_kwh,
            charge_power_kw=req.charge_power_kw,
            efficiency=req.efficiency,
            initial_soc=req.initial_soc,
            min_soc=req.min_soc,
            max_soc=req.max_soc,
        )
    except Exception as exc:
        logger.exception("MILP basic optimalisatie mislukt")
        raise HTTPException(status_code=500, detail=str(exc))

    return OptimizeResponse(
        scenario="basic",
        total_revenue_eur=result["total_revenue"],
        schedule=result["schedule"],
        soc=result["soc"],
    )


# ---------------------------------------------------------------------------
# Async scenario optimalisatie (achtergrondtaak)
# ---------------------------------------------------------------------------

SCENARIOS = ["basic", "solar", "wind_solar", "imbalance"]


def _run_milp_scenarios(job_id: str, req: OptimizeRequest) -> None:
    """Achtergrondthread: voert alle MILP scenario's sequentieel uit."""
    mo_module = None
    try:
        import milp_optimizer as mo_module
    except ImportError as exc:
        job_manager.set_failed(job_id, f"milp_optimizer import error: {exc}")
        return

    job_manager.set_running(job_id)
    results: List[ScenarioResult] = []

    scenario_fns = {
        "basic": lambda: mo_module.optimize_battery_schedule(
            prices=req.prices,
            battery_kwh=req.battery_kwh,
            charge_power_kw=req.charge_power_kw,
            efficiency=req.efficiency,
            initial_soc=req.initial_soc,
            min_soc=req.min_soc,
            max_soc=req.max_soc,
        ),
        "solar": lambda: mo_module.optimize_battery_schedule_solar(
            prices=req.prices,
            solar_forecast=req.solar_forecast or [],
            battery_kwh=req.battery_kwh,
            charge_power_kw=req.charge_power_kw,
            efficiency=req.efficiency,
            initial_soc=req.initial_soc,
            min_soc=req.min_soc,
            max_soc=req.max_soc,
        ),
        "wind_solar": lambda: mo_module.optimize_battery_schedule_wind_solar(
            prices=req.prices,
            solar_forecast=req.solar_forecast or [],
            wind_forecast=req.wind_forecast or [],
            battery_kwh=req.battery_kwh,
            charge_power_kw=req.charge_power_kw,
            efficiency=req.efficiency,
            initial_soc=req.initial_soc,
            min_soc=req.min_soc,
            max_soc=req.max_soc,
        ),
        "imbalance": lambda: mo_module.optimize_battery_schedule_imbalance(
            prices=req.prices,
            imbalance_prices=req.imbalance_prices or req.prices,
            battery_kwh=req.battery_kwh,
            charge_power_kw=req.charge_power_kw,
            efficiency=req.efficiency,
            initial_soc=req.initial_soc,
            min_soc=req.min_soc,
            max_soc=req.max_soc,
        ),
    }

    with job_manager.milp_semaphore:
        for i, scenario in enumerate(SCENARIOS):
            fn = scenario_fns.get(scenario)
            if fn is None:
                continue
            try:
                r = fn()
                results.append(ScenarioResult(
                    scenario=scenario,
                    total_revenue_eur=r["total_revenue"],
                    schedule=r["schedule"],
                    soc=r["soc"],
                ))
            except Exception as exc:
                logger.exception("Scenario '%s' mislukt", scenario)
                results.append(ScenarioResult(scenario=scenario, error=str(exc)))

            job_manager.set_progress(job_id, int((i + 1) / len(SCENARIOS) * 100))

    job_manager.set_completed(job_id, [r.model_dump() for r in results])


@router.post("/optimize/scenarios", response_model=JobResponse, status_code=202)
async def start_scenario_optimization(req: OptimizeRequest, background_tasks: BackgroundTasks):
    """
    Start asynchrone MILP optimalisatie voor alle scenario's.
    Geeft direct een `job_id` terug; poll via `GET /api/jobs/{job_id}`.
    """
    job_id = job_manager.create_job()
    background_tasks.add_task(
        lambda: threading.Thread(
            target=_run_milp_scenarios,
            args=(job_id, req),
            daemon=True,
        ).start()
    )
    return JobResponse(job_id=job_id, status=JobStatus.pending)


# ---------------------------------------------------------------------------
# Rule-based simulatie
# ---------------------------------------------------------------------------

@router.post("/simulate/rule-based", response_model=SimulateResponse)
async def simulate_rule_based(req: SimulateRequest):
    """
    Snelle rule-based batterijsimulatie (geen MILP).
    Laad wanneer prijs < charge_threshold, ontlaad wanneer > discharge_threshold.
    """
    soc = req.initial_soc * req.battery_kwh  # kWh
    schedule: List[float] = []
    soc_track: List[float] = []
    revenue = 0.0
    dt_h = 0.25  # kwartierresolutie

    for price in req.prices:
        max_charge = min(req.charge_power_kw * dt_h, (req.battery_kwh - soc))
        max_discharge = min(req.charge_power_kw * dt_h, soc)

        if price <= req.charge_threshold and max_charge > 0:
            action_kwh = max_charge
            soc += action_kwh
            revenue -= price * action_kwh / 1000  # prijs is €/MWh
            schedule.append(action_kwh / dt_h)     # kW
        elif price >= req.discharge_threshold and max_discharge > 0:
            action_kwh = max_discharge * req.efficiency
            soc -= max_discharge
            revenue += price * action_kwh / 1000
            schedule.append(-max_discharge / dt_h)
        else:
            schedule.append(0.0)

        soc_track.append(round(soc / req.battery_kwh, 4))

    return SimulateResponse(
        total_revenue_eur=round(revenue, 4),
        schedule=schedule,
        soc=soc_track,
        num_charge_slots=sum(1 for s in schedule if s > 0),
        num_discharge_slots=sum(1 for s in schedule if s < 0),
    )


# ---------------------------------------------------------------------------
# Battery sizing analyse
# ---------------------------------------------------------------------------

@router.post("/analyze/battery-sizing", response_model=BatterySizingResponse)
async def analyze_battery_sizing(req: BatterySizingRequest):
    """
    Vergelijk revenue voor verschillende batterijgroottes via MILP.
    Voert één optimalisatie per opgegeven grootte uit.
    """
    mo = _import_optimizer()
    results: List[BatterySizingResult] = []

    with job_manager.milp_semaphore:
        for size_kwh in req.sizes_kwh:
            try:
                r = mo.optimize_battery_schedule(
                    prices=req.prices,
                    battery_kwh=size_kwh,
                    charge_power_kw=req.power_kw,
                    efficiency=req.efficiency,
                )
                rev = r["total_revenue"]
                results.append(BatterySizingResult(
                    battery_kwh=size_kwh,
                    total_revenue_eur=round(rev, 4),
                    revenue_per_kwh=round(rev / size_kwh, 4),
                ))
            except Exception as exc:
                logger.warning("Battery sizing voor %.0f kWh mislukt: %s", size_kwh, exc)

    return BatterySizingResponse(results=results)
