"""Notifications must never be able to break a booking."""
import pytest

from hospital.models import Notification
from hospital.services import booking, notify


class TestNotifications:
    def test_notification_is_stored(self, db, clinic):
        record = notify.notify(clinic["patient_a"], "Test", "Body text")
        assert record.id is not None
        assert record.delivery_status == "stored"
        assert record.channel == "in_app"

    def test_unread_count_tracks_reads(self, db, clinic):
        notify.notify(clinic["patient_a"], "One")
        notify.notify(clinic["patient_a"], "Two")
        assert notify.unread_count(clinic["patient_a"]) == 2
        notify.mark_all_read(clinic["patient_a"])
        assert notify.unread_count(clinic["patient_a"]) == 0

    def test_booking_notifies_both_parties(self, db, clinic, slot_at):
        appointment = booking.book_appointment(
            patient=clinic["patient_a"], doctor=clinic["doctor"], slot_start=slot_at(0))
        notify.appointment_booked(appointment)

        patient_note = Notification.query.filter_by(user_id=clinic["patient_a"].id).first()
        doctor_note = Notification.query.filter_by(
            user_id=clinic["doctor"].user_id).first()
        assert f"#{appointment.token_number}" in patient_note.title
        assert doctor_note is not None

    def test_a_failing_backend_does_not_raise(self, db, clinic, monkeypatch):
        """The safety property the whole module exists for."""
        class Exploding:
            name = "email"
            def send(self, *args, **kwargs):
                raise RuntimeError("SMTP is down")

        monkeypatch.setattr(notify, "get_backend", lambda: Exploding())
        record = notify.notify(clinic["patient_a"], "Still recorded")

        assert record.id is not None                 # the row survived
        assert record.delivery_status == "failed"    # and the failure is visible

    def test_a_failing_backend_does_not_break_booking(self, db, clinic, slot_at, monkeypatch):
        class Exploding:
            name = "email"
            def send(self, *args, **kwargs):
                raise RuntimeError("SMTP is down")

        monkeypatch.setattr(notify, "get_backend", lambda: Exploding())
        appointment = booking.book_appointment(
            patient=clinic["patient_a"], doctor=clinic["doctor"], slot_start=slot_at(0))
        notify.appointment_booked(appointment)
        assert appointment.id is not None
