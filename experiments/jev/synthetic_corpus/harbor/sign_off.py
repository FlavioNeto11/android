"""Human sign-off for jobs that could not be undone."""
from dataclasses import dataclass, field

RISKY_KINDS = {"purge", "rebuild", "publish"}


@dataclass
class SignOffRequest:
    job_id: str
    kind: str
    status: str = "waiting"
    notes: list[str] = field(default_factory=list)


def needs_sign_off(kind: str, impact: int) -> bool:
    """High impact or an irreversible kind always goes to a person, whatever the automation says."""
    return kind in RISKY_KINDS or impact >= 8


def open_request(job_id: str, kind: str, impact: int) -> SignOffRequest | None:
    if not needs_sign_off(kind, impact):
        return None
    return SignOffRequest(job_id, kind)


def resolve(request: SignOffRequest, approved: bool, note: str = "") -> SignOffRequest:
    request.status = "approved" if approved else "declined"
    if note:
        request.notes.append(note)
    return request
