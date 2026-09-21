"""Notifications.

Split into two steps so a broken mail server cannot take down a booking:

1. Write the notification row. This always works, and that row is the in-app
   inbox, so the user still gets told.
2. Try to send it somewhere else, but only if credentials are set. Any failure
   is recorded on the row and otherwise ignored.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from flask import current_app

from hospital.extensions import db
from hospital.models import Appointment, Notification, User

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------
class InAppBackend:
    """Always available. The stored row is the delivery."""
    name = "in_app"

    def send(self, user: User, title: str, body: str) -> str:
        return "stored"


class SMTPBackend:
    """Real email. Only selected when MAIL_SERVER and credentials are present."""
    name = "email"

    def __init__(self, server, port, username, password, sender):
        self.server, self.port = server, port
        self.username, self.password, self.sender = username, password, sender

    def send(self, user: User, title: str, body: str) -> str:
        if not user.email:
            return "failed"
        message = EmailMessage()
        message["Subject"] = title
        message["From"] = self.sender
        message["To"] = user.email
        message.set_content(body)
        with smtplib.SMTP(self.server, self.port, timeout=10) as smtp:
            smtp.starttls()
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(message)
        return "sent"


def get_backend():
    config = current_app.config
    if config.get("MAIL_SERVER"):
        return SMTPBackend(
            config["MAIL_SERVER"], config["MAIL_PORT"],
            config.get("MAIL_USERNAME"), config.get("MAIL_PASSWORD"),
            config.get("MAIL_SENDER"),
        )
    return InAppBackend()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def notify(user: User, title: str, body: str = "", category: str = "info") -> Notification:
    """Save a notification and try to deliver it. Does not raise."""
    backend = get_backend()
    record = Notification(
        user_id=user.id, title=title, body=body,
        category=category, channel=backend.name,
    )
    db.session.add(record)
    db.session.commit()

    try:
        record.delivery_status = backend.send(user, title, body)
    except Exception as exc:  # noqa: BLE001 - broad on purpose, see module docstring
        log.warning("Notification delivery failed for %s: %s", user.email, exc)
        record.delivery_status = "failed"
    db.session.commit()
    return record


def unread_count(user: User) -> int:
    return Notification.query.filter_by(user_id=user.id, is_read=False).count()


def mark_all_read(user: User) -> None:
    Notification.query.filter_by(user_id=user.id, is_read=False).update({"is_read": True})
    db.session.commit()


# ---------------------------------------------------------------------------
# One helper per event, so the wording stays consistent
# ---------------------------------------------------------------------------
def _when(appointment: Appointment) -> str:
    return appointment.slot_start.strftime("%d %b %Y at %I:%M %p").replace(" 0", " ")


def appointment_booked(appointment: Appointment) -> None:
    notify(
        appointment.patient,
        f"Appointment confirmed -- token #{appointment.token_number}",
        (
            f"Your appointment with {appointment.doctor.display_name} "
            f"({appointment.doctor.department.name}) is confirmed for {_when(appointment)}.\n"
            f"Your token number is {appointment.token_number}. "
            f"Room {appointment.doctor.room_no or 'TBC'}.\n\n"
            "Please arrive 10 minutes early and check in at reception."
        ),
        category="success",
    )
    notify(
        appointment.doctor.user,
        f"New appointment -- token #{appointment.token_number}",
        f"{appointment.patient.name} booked {_when(appointment)}.",
    )


def appointment_cancelled(appointment: Appointment, by_role: str) -> None:
    notify(
        appointment.patient,
        "Appointment cancelled",
        f"Your appointment with {appointment.doctor.display_name} on "
        f"{_when(appointment)} was cancelled by the {by_role}.",
        category="warning",
    )
    if by_role != "doctor":
        notify(
            appointment.doctor.user,
            "Appointment cancelled",
            f"{appointment.patient.name}'s appointment on {_when(appointment)} was cancelled.",
        )


def patient_called(appointment: Appointment) -> None:
    notify(
        appointment.patient,
        f"You're being called -- token #{appointment.token_number}",
        f"Please proceed to room {appointment.doctor.room_no or 'reception'} to see "
        f"{appointment.doctor.display_name}.",
        category="success",
    )


def consultation_ready(appointment: Appointment) -> None:
    notify(
        appointment.patient,
        "Your prescription is ready",
        f"{appointment.doctor.display_name} has completed your consultation. "
        "Your notes and prescription are available in your visit history.",
        category="success",
    )
