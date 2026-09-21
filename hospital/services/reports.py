"""Aggregate queries for the admin dashboard.

These return plain dicts and lists so the templates stay simple and the numbers
can be checked in tests without rendering any HTML.
"""
from __future__ import annotations

from collections import Counter
from datetime import date as Date, datetime, timedelta

from sqlalchemy import case, func

from hospital.extensions import db
from hospital.models import (
    Appointment, ApptStatus, ChatMessage, ClinicSession, Department, Doctor, Rating,
    Role, User,
)


def headline_counts() -> dict:
    today = Date.today()
    return {
        "patients": User.query.filter_by(role=Role.PATIENT).count(),
        "doctors": Doctor.query.filter_by(is_active=True).count(),
        "departments": Department.query.filter_by(is_active=True).count(),
        "appointments_today": Appointment.query.filter(
            Appointment.appt_date == today,
            Appointment.status != ApptStatus.CANCELLED,
        ).count(),
        "waiting_now": Appointment.query.filter(
            Appointment.appt_date == today,
            Appointment.status.in_((ApptStatus.BOOKED, ApptStatus.CHECKED_IN)),
        ).count(),
        "completed_today": Appointment.query.filter(
            Appointment.appt_date == today,
            Appointment.status == ApptStatus.COMPLETED,
        ).count(),
    }


def appointments_per_day(days: int = 14) -> list[dict]:
    """Booking counts for the last `days` days, with zeros filled in.

    Without the zero-fill a quiet day just disappears from the chart, which
    makes the graph look busier than it was.
    """
    start = Date.today() - timedelta(days=days - 1)
    rows = (
        db.session.query(Appointment.appt_date, func.count(Appointment.id))
        .filter(Appointment.appt_date >= start)
        .group_by(Appointment.appt_date)
        .all()
    )
    counts = {row[0]: row[1] for row in rows}
    return [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            "label": (start + timedelta(days=i)).strftime("%d %b"),
            "count": counts.get(start + timedelta(days=i), 0),
        }
        for i in range(days)
    ]


def appointments_per_department() -> list[dict]:
    rows = (
        db.session.query(Department.name, func.count(Appointment.id))
        .join(Doctor, Doctor.department_id == Department.id)
        .outerjoin(Appointment, Appointment.doctor_id == Doctor.id)
        .group_by(Department.name)
        .order_by(func.count(Appointment.id).desc())
        .all()
    )
    return [{"department": name, "count": count} for name, count in rows]


def status_breakdown() -> list[dict]:
    rows = (
        db.session.query(Appointment.status, func.count(Appointment.id))
        .group_by(Appointment.status)
        .all()
    )
    return [
        {"status": status, "label": ApptStatus.LABELS.get(status, status), "count": count}
        for status, count in rows
    ]


def no_show_rate() -> dict:
    """No-shows as a share of appointments that actually reached a conclusion.

    Cancelled and still-upcoming appointments are excluded from the denominator:
    a cancellation is not a no-show, and counting future bookings would make the
    rate drift downward simply because more people booked ahead.
    """
    concluded = Appointment.query.filter(
        Appointment.status.in_((ApptStatus.COMPLETED, ApptStatus.NO_SHOW))
    ).count()
    missed = Appointment.query.filter_by(status=ApptStatus.NO_SHOW).count()
    return {
        "concluded": concluded,
        "no_shows": missed,
        "rate": round((missed / concluded) * 100, 1) if concluded else 0.0,
    }


def average_wait_minutes() -> float:
    """Mean time from being called to finishing, across completed consultations."""
    rows = (
        db.session.query(Appointment.called_at, Appointment.completed_at)
        .filter(
            Appointment.status == ApptStatus.COMPLETED,
            Appointment.called_at.isnot(None),
            Appointment.completed_at.isnot(None),
        )
        .all()
    )
    if not rows:
        return 0.0
    total = sum((c - s).total_seconds() for s, c in rows)
    return round(total / len(rows) / 60, 1)


def busiest_doctors(limit: int = 5) -> list[dict]:
    rows = (
        db.session.query(
            User.name, Department.name, func.count(Appointment.id), Doctor.avg_rating
        )
        .select_from(Doctor)
        .join(User, Doctor.user_id == User.id)
        .join(Department, Doctor.department_id == Department.id)
        .outerjoin(Appointment, Appointment.doctor_id == Doctor.id)
        .group_by(Doctor.id)
        .order_by(func.count(Appointment.id).desc())
        .limit(limit)
        .all()
    )
    return [
        {"doctor": name, "department": dept, "appointments": count, "rating": rating}
        for name, dept, count, rating in rows
    ]


def top_symptoms(limit: int = 8) -> list[dict]:
    """What the chatbot is being asked about, and where it routed people.

    Counting the bot's own predictions rather than the raw patient text: free
    text is too varied to group usefully, while the predicted department is a
    clean category and is the thing an administrator would act on.
    """
    rows = (
        db.session.query(ChatMessage.predicted_specialty, func.count(ChatMessage.id))
        .filter(ChatMessage.sender == "user", ChatMessage.predicted_specialty.isnot(None))
        .group_by(ChatMessage.predicted_specialty)
        .order_by(func.count(ChatMessage.id).desc())
        .limit(limit)
        .all()
    )
    return [{"department": dept, "count": count} for dept, count in rows]


def rating_summary() -> dict:
    average, count = db.session.query(func.avg(Rating.stars), func.count(Rating.id)).one()
    distribution = dict(
        db.session.query(Rating.stars, func.count(Rating.id)).group_by(Rating.stars).all()
    )
    return {
        "average": round(float(average), 2) if average else 0.0,
        "count": int(count or 0),
        "distribution": {star: distribution.get(star, 0) for star in range(5, 0, -1)},
    }


def chatbot_usage() -> dict:
    total = ChatMessage.query.filter_by(sender="user").count()
    signed_in = ChatMessage.query.filter(
        ChatMessage.sender == "user", ChatMessage.user_id.isnot(None)
    ).count()
    booked_via_bot = Appointment.query.filter_by(booked_via="chatbot").count()
    return {
        "questions": total,
        "from_signed_in": signed_in,
        "appointments_from_bot": booked_via_bot,
        "conversion": round((booked_via_bot / total) * 100, 1) if total else 0.0,
    }
