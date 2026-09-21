"""Post-visit ratings, and the roll-up onto the doctor record."""
from __future__ import annotations

from sqlalchemy import func

from hospital.extensions import db
from hospital.models import Appointment, ApptStatus, Doctor, Rating, User
from hospital.services.errors import InvalidTransition, NotPermitted


def submit_rating(
    appointment: Appointment, *, patient: User, stars: int, comment: str | None = None
) -> Rating:
    if appointment.patient_id != patient.id:
        raise NotPermitted("You can only rate your own appointments.")
    if appointment.status != ApptStatus.COMPLETED:
        raise InvalidTransition("You can only rate a completed appointment.")
    if appointment.rating is not None:
        raise InvalidTransition("You have already rated this visit.")
    if not 1 <= int(stars) <= 5:
        raise InvalidTransition("A rating must be between 1 and 5 stars.")

    rating = Rating(
        appointment_id=appointment.id,
        doctor_id=appointment.doctor_id,
        patient_id=patient.id,
        stars=int(stars),
        comment=(comment or "").strip() or None,
    )
    db.session.add(rating)
    db.session.flush()
    recalculate(appointment.doctor)
    db.session.commit()
    return rating


def recalculate(doctor: Doctor) -> None:
    """Refresh the denormalised average on the doctor row.

    The average is stored rather than computed on read because the doctor list
    and the triage recommendations both sort by it on every page load.
    """
    average, count = (
        db.session.query(func.avg(Rating.stars), func.count(Rating.id))
        .filter(Rating.doctor_id == doctor.id)
        .one()
    )
    doctor.avg_rating = round(float(average), 2) if average else 0.0
    doctor.rating_count = int(count or 0)


def recent_for_doctor(doctor: Doctor, limit: int = 10) -> list[Rating]:
    return (
        Rating.query.filter_by(doctor_id=doctor.id)
        .order_by(Rating.created_at.desc())
        .limit(limit)
        .all()
    )
