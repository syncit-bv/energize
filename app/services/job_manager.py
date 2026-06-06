"""
In-memory job tracker voor achtergrondtaken (MILP optimalisaties).

Thread-safe via threading.Lock. Jobs leven enkel in RAM (herstart = leeg).
Voor productie: vervang door Redis + Celery of equivalent.

Statusflow:
  pending --> running --> completed
                      \-> failed
"""
from __future__ import annotations

import threading
import uuid
from typing import Any, Dict, Optional

from app.models.schemas import JobStatus


class JobManager:
    """Thread-safe in-memory job store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        # Semaphore om gelijktijdige MILP-runs te serialiseren
        # (PuLP/CBC is niet thread-safe bij meerdere gelijktijdige instanties)
        self.milp_semaphore = threading.Semaphore(1)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_job(self) -> str:
        """Maak een nieuwe job aan en geef de UUID terug."""
        job_id = str(uuid.uuid4())
        with self._lock:
            self._jobs[job_id] = {
                "status":   JobStatus.pending,
                "progress": 0,
                "result":   None,
                "error":    None,
            }
        return job_id

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Geef de huidige toestand van een job terug, of None als niet gevonden."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return dict(job)

    # ------------------------------------------------------------------
    # Statusupdates (aangeroepen vanuit achtergrondthreads)
    # ------------------------------------------------------------------

    def set_running(self, job_id: str) -> None:
        self._update(job_id, status=JobStatus.running)

    def set_progress(self, job_id: str, progress: int) -> None:
        """Stel voortgang in (0–100). Zet status automatisch op 'running' als nog pending."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if job["status"] == JobStatus.pending:
                job["status"] = JobStatus.running
            job["progress"] = max(0, min(100, progress))

    def set_completed(self, job_id: str, result: Any) -> None:
        self._update(job_id, status=JobStatus.completed, progress=100, result=result)

    def set_failed(self, job_id: str, error: str) -> None:
        self._update(job_id, status=JobStatus.failed, error=error)

    # ------------------------------------------------------------------
    # Intern
    # ------------------------------------------------------------------

    def _update(self, job_id: str, **kwargs: Any) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(kwargs)


# Singleton — gedeeld door alle request handlers in hetzelfde proces
job_manager = JobManager()
