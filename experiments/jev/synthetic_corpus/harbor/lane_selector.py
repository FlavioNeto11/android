"""Chooses a processing lane for a job before it is handed to a crew."""

LANES = ("express", "standard", "manual")


def choose_lane(kind: str, impact: int, has_template: bool, previous_failures: int) -> str:
    """Rules first: a known template with no failures goes express, anything doubtful goes manual."""
    if impact >= 8 or kind in {"purge", "rebuild", "publish"}:
        return "manual"
    if has_template and previous_failures == 0:
        return "express"
    if previous_failures >= 3:
        return "manual"
    return "standard"
