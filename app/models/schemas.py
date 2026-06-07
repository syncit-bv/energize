"""Pydantic request/response models voor de EMS API."""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    not_found = "not_found"


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: int = 0
    message: Optional[str] = None   # Voortgangslabel, bv. "Berekening 3/7: 20 kWh…"
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------

class PriceSource(str, Enum):
    entsoe = "entsoe"
    file = "file"


class PriceRecord(BaseModel):
    timestamp: str          # ISO-8601
    price_eur_mwh: float


class PricesResponse(BaseModel):
    source: PriceSource
    start: str
    end: str
    records: List[PriceRecord]
    count: int


# ---------------------------------------------------------------------------
# Elia
# ---------------------------------------------------------------------------

class EliaImbalanceRecord(BaseModel):
    timestamp: str
    nrv: Optional[float] = None          # Net Regulation Volume (MW)
    alpha: Optional[float] = None        # Imbalance price (€/MWh)


class EliaImbalanceResponse(BaseModel):
    date: str
    records: List[EliaImbalanceRecord]


class EliaSolarRecord(BaseModel):
    timestamp: str
    forecast_mw: Optional[float] = None
    measured_mw: Optional[float] = None


class EliaWindRecord(BaseModel):
    timestamp: str
    forecast_mw: Optional[float] = None
    measured_mw: Optional[float] = None


# ---------------------------------------------------------------------------
# Optimization / MILP
# ---------------------------------------------------------------------------

class OptimizeRequest(BaseModel):
    prices: List[float] = Field(..., description="Day-ahead prijzen in €/MWh (kwartier-resolutie)")
    battery_kwh: float = Field(10.0, gt=0, description="Batterijcapaciteit in kWh")
    charge_power_kw: float = Field(5.0, gt=0, description="Max laadvermogen in kW (afname net; bepaalt capaciteitstarief)")
    discharge_power_kw: Optional[float] = Field(None, gt=0, description="Max ontlaadvermogen in kW (injectie net); fallback = charge_power_kw")
    efficiency: float = Field(0.95, gt=0, le=1, description="Round-trip efficiëntie")
    initial_soc: float = Field(0.5, ge=0, le=1, description="Initiële state-of-charge (0–1)")
    min_soc: float = Field(0.1, ge=0, le=1, description="Minimum SOC (reserve)")
    min_end_soc: Optional[float] = Field(None, ge=0, le=1, description="Minimum eindstatus SOC; fallback = min_soc")
    max_soc: float = Field(0.9, ge=0, le=1, description="Maximum SOC")
    # Optioneel voor solar/wind scenario's
    solar_forecast: Optional[List[float]] = None   # kW per kwartier
    wind_forecast: Optional[List[float]] = None    # kW per kwartier
    imbalance_prices: Optional[List[float]] = None # €/MWh


class ScenarioResult(BaseModel):
    scenario: str
    total_revenue_eur: Optional[float] = None
    schedule: Optional[List[float]] = None   # kW per kwartier (+ = laden, - = ontladen)
    soc: Optional[List[float]] = None
    error: Optional[str] = None


class OptimizeResponse(BaseModel):
    scenario: str
    total_revenue_eur: float
    schedule: List[float]
    soc: List[float]


# ---------------------------------------------------------------------------
# Simulation (rule-based)
# ---------------------------------------------------------------------------

class SimulateRequest(BaseModel):
    prices: List[float] = Field(..., description="Day-ahead prijzen in €/MWh")
    battery_kwh: float = Field(10.0, gt=0)
    charge_power_kw: float = Field(5.0, gt=0)
    efficiency: float = Field(0.95, gt=0, le=1)
    charge_threshold: float = Field(-20.0, description="Laad als prijs onder deze waarde (€/MWh)")
    discharge_threshold: float = Field(100.0, description="Ontlaad als prijs boven deze waarde (€/MWh)")
    initial_soc: float = Field(0.5, ge=0, le=1)


class SimulateResponse(BaseModel):
    total_revenue_eur: float
    schedule: List[float]
    soc: List[float]
    num_charge_slots: int
    num_discharge_slots: int


# ---------------------------------------------------------------------------
# Battery sizing
# ---------------------------------------------------------------------------

class BatterySizingRequest(BaseModel):
    prices: List[float]
    power_kw: float = Field(5.0, gt=0)
    efficiency: float = Field(0.95, gt=0, le=1)
    sizes_kwh: List[float] = Field(
        default=[5, 10, 15, 20, 30, 50],
        description="Te evalueren batterijgroottes in kWh"
    )


class BatterySizingResult(BaseModel):
    battery_kwh: float
    total_revenue_eur: float
    revenue_per_kwh: float


class BatterySizingResponse(BaseModel):
    results: List[BatterySizingResult]
