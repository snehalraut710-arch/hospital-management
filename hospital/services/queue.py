"""The live queue: a state machine over one clinic sitting.

    BOOKED -> CHECKED_IN -> IN_CONSULT -> COMPLETED
        |          |             |
    CANCELLED   NO_SHOW    (consultation recorded)

Every transition goes through a function here that checks the move is allowed
first. Bad moves raise instead of being ignored: if the queue loses track of who
is being seen, the wrong patient ends up in the room.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date, datetime

from hospital.extensions import db
from hospital.models import Appointment, ApptStatus, ClinicSession, SessionStatus
from hospital.services.errors import InvalidTransition

#: Which statuses each status may move to.
ALLOWED_TRANSITIONS = {
    ApptStatus.BOOKED: {ApptStatus.CHECKED_IN, ApptStatus.IN_CONSULT,
                        ApptStatus.CANCELLED, ApptStatus.NO_SHOW},
    ApptStatus.CHECKED_IN: {ApptStatus.IN_CONSULT, ApptStatus.CANCELLED, ApptStatus.NO_SHOW},
    ApptStatus.IN_CONSULT: {ApptStatus.COMPLETED, ApptStatus.NO_SHOW},
    ApptStatus.COMPLETED: set(),
    ApptStatus.CANCELLED: set(),
    ApptStatus.NO_SHOW: set(),
}


def _transition(appointment: Appointment, target: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(appointment.status, set())
    if target not in allowed:
        raise InvalidTransition(
            f"Cannot move token #{appointment.token_number} from "
            f"{appointment.status_label.lower()} to {ApptStatus.LABELS[target].lower()}."
        )
    appointment.status = target


# ---------------------------------------------------------------------------
# Session control
# ---------------------------------------------------------------------------
def open_session(session: ClinicSession, now: datetime | None = None) -> ClinicSession:
    if session.status == SessionStatus.CLOSED:
        raise InvalidTransition("This clinic has already been closed.")
    if session.status == SessionStatus.NOT_STARTED:
        session.status = SessionStatus.IN_PROGRESS
        session.started_at = now or datetime.now()
        db.session.commit()
    return session


def close_session(session: ClinicSession, now: datetime | None = None) -> ClinicSession:
    """Close the sitting, marking anyone still waiting as a no-show."""
    if session.status == SessionStatus.CLOSED:
        return session
    for appointment in session.appointments:
        if appointment.status in (ApptStatus.BOOKED, ApptStatus.CHECKED_IN):
            appointment.status = ApptStatus.NO_SHOW
        elif appointment.status == ApptStatus.IN_CONSULT:
            # A consultation left open when the doctor closes up is treated as
            # completed; the notes may be filled in afterwards.
            appointment.status = ApptStatus.COMPLETED
            appointment.completed_at = now or datetime.now()
    session.status = SessionStatus.CLOSED
    session.closed_at = now or datetime.now()
    db.session.commit()
    return session


# ---------------------------------------------------------------------------
# Appointment transitions
# ---------------------------------------------------------------------------
def check_in(appointment: Appointment, now: datetime | None = None) -> Appointment:
    """Patient has arrived at the hospital."""
    _transition(appointment, ApptStatus.CHECKED_IN)
    appointment.checked_in_at = now or datetime.now()
    db.session.commit()
    return appointment


def call_next(session: ClinicSession, now: datetime | None = None) -> Appointment | None:
    """Call the next waiting token in.

    Patients who have checked in come before ones who have not, since we know
    they are actually here.
    """
    now = now or datetime.now()
    open_session(session, now)

    if session.now_serving is not None:
        raise InvalidTransition(
            "Finish the current consultation before calling the next patient."
        )

    waiting = [
        a for a in session.appointments
        if a.status in (ApptStatus.BOOKED, ApptStatus.CHECKED_IN)
    ]
    if not waiting:
        return None

    waiting.sort(key=lambda a: (a.status != ApptStatus.CHECKED_IN, a.token_number))
    nxt = waiting[0]

    _transition(nxt, ApptStatus.IN_CONSULT)
    nxt.called_at = now
    session.current_token = nxt.token_number
    db.session.commit()
    return nxt


def complete(appointment: Appointment, now: datetime | None = None) -> Appointment:
    """Finish the consultation and fold its duration into the rolling average."""
    now = now or datetime.now()
    _transition(appointment, ApptStatus.COMPLETED)
    appointment.completed_at = now

    session = appointment.session
    if appointment.called_at:
        duration = max(int((now - appointment.called_at).total_seconds()), 60)
        _update_average(session, duration)

    session.served_count += 1
    db.session.commit()
    return appointment


def mark_no_show(appointment: Appointment) -> Appointment:
    _transition(appointment, ApptStatus.NO_SHOW)
    db.session.commit()
    return appointment


def _update_average(session: ClinicSession, duration_seconds: int) -> None:
    """Exponential moving average of consultation length.

    alpha=0.3 weights recent consultations more heavily, so one long case early
    on does not skew the estimate for the rest of the session.
    """
    alpha = 0.3
    current = session.avg_service_seconds or (session.slot_minutes * 60)
    session.avg_service_seconds = int(alpha * duration_seconds + (1 - alpha) * current)


# ---------------------------------------------------------------------------
# Reading the queue
# ---------------------------------------------------------------------------
@dataclass
class QueuePosition:
    token: int
    now_serving: int
    people_ahead: int
    estimated_wait_minutes: int
    status: str
    status_label: str
    session_status: str

    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "now_serving": self.now_serving,
            "people_ahead": self.people_ahead,
            "estimated_wait_minutes": self.estimated_wait_minutes,
            "status": self.status,
            "status_label": self.status_label,
            "session_status": self.session_status,
        }


def position_of(appointment: Appointment) -> QueuePosition:
    """Token position and estimated wait for one patient."""
    session = appointment.session
    ahead = [
        a for a in session.appointments
        if a.status in (ApptStatus.BOOKED, ApptStatus.CHECKED_IN, ApptStatus.IN_CONSULT)
        and a.token_number < appointment.token_number
    ]
    people_ahead = len(ahead) if appointment.is_live else 0

    per_patient = (session.avg_service_seconds or session.slot_minutes * 60) / 60.0
    if appointment.status == ApptStatus.IN_CONSULT:
        wait = 0
    elif not appointment.is_live:
        wait = 0
    else:
        wait = int(round(people_ahead * per_patient))

    return QueuePosition(
        token=appointment.token_number,
        now_serving=session.current_token,
        people_ahead=people_ahead,
        estimated_wait_minutes=wait,
        status=appointment.status,
        status_label=appointment.status_label,
        session_status=session.status,
    )


def snapshot(session: ClinicSession) -> dict:
    """Everything the live board needs, in one JSON-serialisable dict."""
    serving = session.now_serving
    return {
        "session_id": session.id,
        "doctor": session.doctor.display_name,
        "department": session.doctor.department.name,
        "room": session.doctor.room_no,
        "date": session.date.isoformat(),
        "status": session.status,
        "now_serving": session.current_token,
        "serving_patient": serving.patient.name if serving else None,
        "waiting": session.waiting_count,
        "served": session.served_count,
        "avg_minutes": round((session.avg_service_seconds or 900) / 60),
        "tokens": [
            {
                "token": a.token_number,
                "patient": a.patient.name,
                "status": a.status,
                "status_label": a.status_label,
                "time": a.slot_start.strftime("%I:%M %p").lstrip("0"),
            }
            for a in session.appointments
            if a.status != ApptStatus.CANCELLED
        ],
    }


def live_sessions(day: Date | None = None) -> list[ClinicSession]:
    day = day or Date.today()
    return (
        ClinicSession.query
        .filter(ClinicSession.date == day)
        .order_by(ClinicSession.start_time)
        .all()
    )
