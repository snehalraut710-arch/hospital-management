"""Turns a doctor's weekly pattern into actual bookable slots.

The pattern is in doctor_schedule and schedule_exception removes days or times
for leave. Nothing here creates appointments, it only works out what is free.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date, datetime, timedelta

from hospital.extensions import db
from hospital.models import (
    Appointment, ApptStatus, ClinicSession, Doctor, DoctorSchedule, ScheduleException,
)


@dataclass
class Slot:
    """One bookable (or unbookable) time on a doctor's day."""
    start: datetime
    end: datetime
    schedule_id: int
    is_available: bool
    reason: str | None = None  # why not, when unavailable

    @property
    def label(self) -> str:
        return self.start.strftime("%I:%M %p").lstrip("0")


def schedules_for(doctor: Doctor, day: Date) -> list[DoctorSchedule]:
    """The doctor's sittings on that weekday, earliest first."""
    return sorted(
        (s for s in doctor.schedules if s.is_active and s.weekday == day.weekday()),
        key=lambda s: s.start_time,
    )


def exceptions_for(doctor: Doctor, day: Date) -> list[ScheduleException]:
    return [e for e in doctor.exceptions if e.date == day]


def _taken_slot_starts(doctor_id: int, day: Date) -> set[datetime]:
    """Slot times already held by a live appointment.

    Cancelled and no-show appointments are excluded, so their slots return to
    the pool -- which matches the partial unique index on the table.
    """
    rows = (
        db.session.query(Appointment.slot_start)
        .filter(
            Appointment.doctor_id == doctor_id,
            Appointment.appt_date == day,
            Appointment.status.in_(ApptStatus.LIVE),
        )
        .all()
    )
    return {r[0] for r in rows}


def generate_slots(
    doctor: Doctor, day: Date, *, include_unavailable: bool = False, now: datetime | None = None
) -> list[Slot]:
    """Every slot on this doctor's day, marked available or not.

    `include_unavailable` is what the doctor's own schedule view wants (it needs
    to show the full day); patients only ever see the available ones.
    """
    now = now or datetime.now()
    taken = _taken_slot_starts(doctor.id, day)
    blocks = exceptions_for(doctor, day)
    slots: list[Slot] = []

    for schedule in schedules_for(doctor, day):
        cursor = datetime.combine(day, schedule.start_time)
        finish = datetime.combine(day, schedule.end_time)
        step = timedelta(minutes=schedule.slot_minutes)
        issued = 0

        while cursor + step <= finish and issued < schedule.max_tokens:
            slot_end = cursor + step
            reason = None

            if any(b.blocks(cursor.time(), slot_end.time()) for b in blocks):
                reason = "On leave"
            elif cursor in taken:
                reason = "Booked"
            elif cursor <= now:
                reason = "Passed"

            available = reason is None
            if available or include_unavailable:
                slots.append(Slot(
                    start=cursor, end=slot_end, schedule_id=schedule.id,
                    is_available=available, reason=reason,
                ))
            cursor = slot_end
            issued += 1

    return slots


def available_slots(doctor: Doctor, day: Date, now: datetime | None = None) -> list[Slot]:
    return [s for s in generate_slots(doctor, day, now=now) if s.is_available]


def next_available_days(
    doctor: Doctor, horizon_days: int = 14, start: Date | None = None
) -> list[tuple[Date, int]]:
    """(date, free slot count) for each day in the booking window with capacity."""
    start = start or Date.today()
    out = []
    for offset in range(horizon_days):
        day = start + timedelta(days=offset)
        count = len(available_slots(doctor, day))
        if count:
            out.append((day, count))
    return out


def free_slots_today(doctor: Doctor) -> int:
    return len(available_slots(doctor, Date.today()))


def get_or_create_session(doctor: Doctor, day: Date, schedule: DoctorSchedule) -> ClinicSession:
    """Find the sitting a slot belongs to, creating it if needed.

    Created on demand rather than generated in advance for every doctor and
    date. Most of those rows would never be used, and they would go stale if an
    admin edited the schedule.
    """
    session = ClinicSession.query.filter_by(
        doctor_id=doctor.id, date=day, start_time=schedule.start_time
    ).first()
    if session:
        return session

    session = ClinicSession(
        doctor_id=doctor.id,
        schedule_id=schedule.id,
        date=day,
        start_time=schedule.start_time,
        end_time=schedule.end_time,
        slot_minutes=schedule.slot_minutes,
        max_tokens=schedule.max_tokens,
        avg_service_seconds=schedule.slot_minutes * 60,
    )
    db.session.add(session)
    db.session.flush()
    return session


def session_for_slot(doctor: Doctor, slot_start: datetime) -> ClinicSession | None:
    """Find (creating if needed) the sitting that contains this slot time."""
    day = slot_start.date()
    for schedule in schedules_for(doctor, day):
        start = datetime.combine(day, schedule.start_time)
        end = datetime.combine(day, schedule.end_time)
        if start <= slot_start < end:
            return get_or_create_session(doctor, day, schedule)
    return None


def todays_sessions(doctor: Doctor, day: Date | None = None) -> list[ClinicSession]:
    day = day or Date.today()
    for schedule in schedules_for(doctor, day):
        get_or_create_session(doctor, day, schedule)
    db.session.commit()
    return (
        ClinicSession.query
        .filter_by(doctor_id=doctor.id, date=day)
        .order_by(ClinicSession.start_time)
        .all()
    )
