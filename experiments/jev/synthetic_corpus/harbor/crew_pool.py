"""A pool of crews that take on dispatched jobs. A crew can be put on hold while it is being serviced."""
from dataclasses import dataclass


@dataclass
class Crew:
    crew_id: str
    on_hold: bool = False
    busy_jobs: int = 0
    capacity: int = 2


class CrewPool:
    def __init__(self) -> None:
        self._crews: dict[str, Crew] = {}

    def add(self, crew: Crew) -> None:
        self._crews[crew.crew_id] = crew

    def accepts_new_jobs(self, crew_id: str) -> bool:
        """A crew that is on hold never accepts new jobs, even when it has spare capacity."""
        crew = self._crews[crew_id]
        if crew.on_hold:
            return False
        return crew.busy_jobs < crew.capacity

    def hold_reason(self, crew_id: str) -> str | None:
        crew = self._crews[crew_id]
        if crew.on_hold:
            return f"crew {crew.crew_id} is on hold for servicing: new assignments are paused"
        return None
