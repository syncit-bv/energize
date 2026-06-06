"""Job polling endpoint — GET /api/jobs/{job_id}"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.schemas import JobResponse, JobStatus
from app.services.job_manager import job_manager

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    """
    Peil de status en het resultaat van een achtergrondtaak (bv. MILP optimalisatie).

    Mogelijke statussen:
    - `pending`   — taak aangemaakt, nog niet gestart
    - `running`   — berekening loopt
    - `completed` — resultaat beschikbaar in `result`
    - `failed`    — fout beschikbaar in `error`
    """
    data = job_manager.get_job(job_id)
    if data is None or data.get("status") == JobStatus.not_found:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' niet gevonden.")
    return JobResponse(job_id=job_id, **data)
