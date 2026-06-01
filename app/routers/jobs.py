"""Job polling endpoint — GET /api/jobs/{job_id}"""
from fastapi import APIRouter, HTTPException
from app.models.schemas import JobResponse
from app.services.job_manager import job_manager

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    """Peil de status van een achtergrondtaak (bv. MILP optimalisatie)."""
    data = job_manager.get_job(job_id)
    if data["status"] == "not_found":
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' niet gevonden")
    return JobResponse(job_id=job_id, **data)
