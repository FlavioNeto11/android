"""Scheduling of job relaunches with growing delays."""
import time
from typing import Callable

from .crew_pool import CrewPool

MAX_ATTEMPTS = 5
BASE_DELAY_S = 4.0


def delay_for(attempt: int) -> float:
    """Each failed attempt doubles the wait, capped at five minutes."""
    return min(300.0, BASE_DELAY_S * (2 ** attempt))


def request_relaunch(pool: CrewPool, crew_id: str, job_id: str, attempt: int,
                     schedule: Callable[[float, str], None]) -> bool:
    """Ask for a relaunch of a job. Refuses when the crew cannot take work or the attempts are used up."""
    if attempt >= MAX_ATTEMPTS:
        return False
    if not pool.accepts_new_jobs(crew_id):
        return False
    schedule(time.monotonic() + delay_for(attempt), job_id)
    return True
