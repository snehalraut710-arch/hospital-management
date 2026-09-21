"""Creating and cancelling appointments.

The important bit: the database decides whether a slot is free, not this
module. Checking first and then inserting leaves a gap where two patients can
both pass the check, so instead we just try the insert and let the unique index
on (doctor_id, slot_start) reject the second one.
"""
from __future__ import annotations

from datetime import date as Date, datetime

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from hospital.extensions import db
from hospital.models import Appointment, ApptStatus, ClinicSession, Doctor, User
from hospital.services import scheduling
from hospital.services.errors import NotPermitted, SessionFull, SlotUnavailable


def _next_token(session: ClinicSession) -> int:
    """Next token number for this sitting.

    MAX+1, not count+1. Cancelled appointments keep their token, so counting
    live rows would hand out a number that is already in use.
    """
    highest = (
        db.session.query(func.max(Appointment.token_number))
        .filter(Appointment.session_id == session.id)
        .scalar()
    )
    return (highest or 0) + 1


def book_appointment(
    *,
    patient: User,
    doctor: Doctor,
    slot_start: datetime,
    symptoms_text: str | None = None,
    triage_specialty: str | None = None,
    triage_confidence: float | None = None,
    booked_via: str = "web",
    now: datetime | None = None,
) -> Appointment:
    """Book a slot and issue a token. Raises SlotUnavailable / SessionFull."""
    now = now or datetime.now()
    day = slot_start.date()

    if slot_start <= now:
        raise SlotUnavailable("That time has already passed.")

    session = scheduling.session_for_slot(doctor, slot_start)
    if session is None:
        raise SlotUnavailable("The doctor does not hold a clinic at that time.")

    # These checks just give a nicer error message. The real guard is the
    # unique constraint further down.
    if any(b.blocks(slot_start.time(), (slot_start + _step(session)).time())
           for b in scheduling.exceptions_for(doctor, day)):
        raise SlotUnavailable("The doctor is on leave at that time.")

    if session.booked_count >= session.max_tokens:
        raise SessionFull("That clinic is fully booked. Please choose another time.")

    slot_end = slot_start + _step(session)
    appointment = Appointment(
        patient_id=patient.id,
        doctor_id=doctor.id,
        session_id=session.id,
        appt_date=day,
        slot_start=slot_start,
        slot_end=slot_end,
        token_number=_next_token(session),
        status=ApptStatus.BOOKED,
        symptoms_text=symptoms_text,
        triage_specialty=triage_specialty,
        triage_confidence=triage_confidence,
        booked_via=booked_via,
    )
    db.session.add(appointment)
    try:
        db.session.commit()
    except IntegrityError:
        # Either someone else took the slot or two bookings picked the same
        # token. Either way the user should pick again.
        db.session.rollback()
        raise SlotUnavailable("That slot was just taken. Please pick another.")
    return appointment


def _step(session: ClinicSession):
    from datetime import timedelta
    return timedelta(minutes=session.slot_minutes)


def cancel_appointment(
    appointment: Appointment, *, by_user: User, reason: str | None = None
) -> Appointment:
    """Cancel, freeing the slot for someone else."""
    if not (
        by_user.is_admin
        or appointment.patient_id == by_user.id
        or (by_user.is_doctor and by_user.doctor and appointment.doctor_id == by_user.doctor.id)
    ):
        raise NotPermitted("You cannot cancel this appointment.")

    if not appointment.can_cancel:
        raise SlotUnavailable(
            f"A {appointment.status_label.lower()} appointment cannot be cancelled."
        )

    appointment.status = ApptStatus.CANCELLED
    appointment.cancel_reason = reason or f"Cancelled by {by_user.role}"
    db.session.commit()
    return appointment


def patient_appointments(patient: User, *, upcoming: bool | None = None) -> list[Appointment]:
    query = Appointment.query.filter_by(patient_id=patient.id)
    if upcoming is True:
        query = query.filter(
            Appointment.status.in_(ApptStatus.LIVE),
            Appointment.appt_date >= Date.today(),
        ).order_by(Appointment.slot_start)
    elif upcoming is False:
        query = query.filter(
            db.or_(
                Appointment.status.in_(ApptStatus.CLOSED),
                Appointment.appt_date < Date.today(),
            )
        ).order_by(Appointment.slot_start.desc())
    else:
        query = query.order_by(Appointment.slot_start.desc())
    return query.all()
