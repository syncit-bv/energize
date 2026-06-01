"""In-memory job tracking voor achtergrondtaken (MILP optimalisaties).

MVP-implementatie: jobs leven enkel in RAM.
Voor productie: vervang door Redis + Celery.
"""
from __future__ import annotations

import threading
import uuid
from typing import Any, Dict, Optional

from app.models.schemas import JobStatus


class JobManager:
    """Thread-safe in-memory job store met TTL-loos geheugen (herstart = leeg)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        # Semaphore om gelijktijdige MILP-runs te serialiseren (PuLP/CBC is niet thread-safe)
        self.milp_semaphore = threading.Semaphore(1)

    def create_job(self) -> str:
        job_id = str(uuid.uuid4())
        with self._lock:
            self._jobs[job_id] = {
                "status": JobStatus.pending,
                "progress": 0,
                "result": None,
                "error": None,
            }
        return job_id

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return {"status": JobStatus.not_found}
            return dict(job)

    def set_running(self, job_id: str) -> None:
        self._update(job_id, status=JobStatus.running)

    def set_progress(self, job_id: str, progress: int) -> None:
        self._update(job_id, progress=progress)

    def set_completed(self, job_id: str, result: Any) -> None:
        self._update(job_id, status=JobStatus.completed, progress=100, result=result)

    def set_failed(self, job_id: str, error: str) -> None:
        self._update(job_id, status=JobStatus.failed, error=error)

    def _update(self, job_id: str, **kwargs: Any) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(kwargs)


# Singleton — gedeeld door alle request handlers
job_manager = JobManager()
