"""Booking rules, including the concurrency case the database has to win."""
from datetime import datetime, timedelta

import pytest

from hospital.extensions import db as _db
from hospital.models import Appointment, ApptStatus
from hospital.services import booking, scheduling
from hospital.services.errors import NotPermitted, SessionFull, SlotUnavailable
from tests.conftest import make_schedule, make_user


class TestBooking:
    def test_booking_issues_the_first_token(self, db, clinic, slot_at):
        appointment = booking.book_appointment(
            patient=clinic["patient_a"], doctor=clinic["doctor"], slot_start=slot_at(0),
        )
        assert appointment.token_number == 1
        assert appointment.status == ApptStatus.BOOKED

    def test_tokens_increment_within_a_sitting(self, db, clinic, slot_at):
        first = booking.book_appointment(
            patient=clinic["patient_a"], doctor=clinic["doctor"], slot_start=slot_at(0))
        second = booking.book_appointment(
            patient=clinic["patient_b"], doctor=clinic["doctor"], slot_start=slot_at(1))
        assert (first.token_number, second.token_number) == (1, 2)
        assert first.session_id == second.session_id

    def test_symptoms_and_triage_are_stored(self, db, clinic, slot_at):
        appointment = booking.book_appointment(
            patient=clinic["patient_a"], doctor=clinic["doctor"], slot_start=slot_at(0),
            symptoms_text="chest pain", triage_specialty="Cardiology",
            triage_confidence=0.87, booked_via="chatbot",
        )
        assert appointment.symptoms_text == "chest pain"
        assert appointment.triage_specialty == "Cardiology"
        assert appointment.booked_via == "chatbot"

    def test_a_booked_slot_disappears_from_availability(self, db, clinic, slot_at):
        booking.book_appointment(
            patient=clinic["patient_a"], doctor=clinic["doctor"], slot_start=slot_at(0))
        remaining = scheduling.available_slots(clinic["doctor"], clinic["day"])
        assert slot_at(0) not in [s.start for s in remaining]
        assert len(remaining) == 11


class TestBookingRejections:
    def test_double_booking_the_same_slot_is_rejected(self, db, clinic, slot_at):
        booking.book_appointment(
            patient=clinic["patient_a"], doctor=clinic["doctor"], slot_start=slot_at(0))
        with pytest.raises(SlotUnavailable):
            booking.book_appointment(
                patient=clinic["patient_b"], doctor=clinic["doctor"], slot_start=slot_at(0))

    def test_the_database_constraint_is_what_stops_it(self, db, clinic, slot_at):
        """Bypass the service checks entirely and insert straight into the table.

        This is the test that proves the guarantee is in the schema and not in
        application logic that a concurrent request could race past.
        """
        from sqlalchemy.exc import IntegrityError

        first = booking.book_appointment(
            patient=clinic["patient_a"], doctor=clinic["doctor"], slot_start=slot_at(0))

        duplicate = Appointment(
            patient_id=clinic["patient_b"].id,
            doctor_id=clinic["doctor"].id,
            session_id=first.session_id,
            appt_date=clinic["day"],
            slot_start=slot_at(0),
            slot_end=slot_at(0) + timedelta(minutes=15),
            token_number=99,                     # different token, same slot
            status=ApptStatus.BOOKED,
        )
        _db.session.add(duplicate)
        with pytest.raises(IntegrityError):
            _db.session.commit()
        _db.session.rollback()

    def test_duplicate_token_numbers_are_rejected(self, db, clinic, slot_at):
        from sqlalchemy.exc import IntegrityError

        first = booking.book_appointment(
            patient=clinic["patient_a"], doctor=clinic["doctor"], slot_start=slot_at(0))
        clash = Appointment(
            patient_id=clinic["patient_b"].id,
            doctor_id=clinic["doctor"].id,
            session_id=first.session_id,
            appt_date=clinic["day"],
            slot_start=slot_at(5),               # different slot, same token
            slot_end=slot_at(5) + timedelta(minutes=15),
            token_number=first.token_number,
            status=ApptStatus.BOOKED,
        )
        _db.session.add(clash)
        with pytest.raises(IntegrityError):
            _db.session.commit()
        _db.session.rollback()

    def test_booking_in_the_past_is_rejected(self, db, clinic):
        past = datetime.now() - timedelta(days=1)
        with pytest.raises(SlotUnavailable):
            booking.book_appointment(
                patient=clinic["patient_a"], doctor=clinic["doctor"], slot_start=past)

    def test_booking_outside_the_sitting_is_rejected(self, db, clinic):
        from datetime import time
        outside = datetime.combine(clinic["day"], time(22, 0))
        with pytest.raises(SlotUnavailable):
            booking.book_appointment(
                patient=clinic["patient_a"], doctor=clinic["doctor"], slot_start=outside)

    def test_booking_on_a_leave_day_is_rejected(self, db, clinic, slot_at):
        from hospital.models import ScheduleException
        db.session.add(ScheduleException(
            doctor_id=clinic["doctor"].id, date=clinic["day"], is_full_day=True))
        db.session.commit()
        with pytest.raises(SlotUnavailable):
            booking.book_appointment(
                patient=clinic["patient_a"], doctor=clinic["doctor"], slot_start=slot_at(0))

    def test_a_full_sitting_is_rejected(self, db, clinic, tomorrow, slot_at):
        """Fill every token, then try one more."""
        from datetime import time
        doctor = clinic["doctor"]
        for schedule in list(doctor.schedules):
            db.session.delete(schedule)
        db.session.flush()   # the delete must land before the replacement insert
        make_schedule(doctor, tomorrow.weekday(), time(9, 0), time(12, 0),
                      slot_minutes=15, max_tokens=2)
        db.session.commit()

        base = datetime.combine(tomorrow, time(9, 0))
        booking.book_appointment(patient=clinic["patient_a"], doctor=doctor, slot_start=base)
        booking.book_appointment(
            patient=clinic["patient_b"], doctor=doctor,
            slot_start=base + timedelta(minutes=15))

        third = make_user("third@test.local")
        db.session.commit()
        with pytest.raises(SessionFull):
            booking.book_appointment(
                patient=third, doctor=doctor, slot_start=base + timedelta(minutes=30))


class TestCancellation:
    def test_patient_can_cancel_their_own(self, db, clinic, slot_at):
        appointment = booking.book_appointment(
            patient=clinic["patient_a"], doctor=clinic["doctor"], slot_start=slot_at(0))
        booking.cancel_appointment(appointment, by_user=clinic["patient_a"], reason="Busy")
        assert appointment.status == ApptStatus.CANCELLED
        assert appointment.cancel_reason == "Busy"

    def test_another_patient_cannot_cancel_it(self, db, clinic, slot_at):
        appointment = booking.book_appointment(
            patient=clinic["patient_a"], doctor=clinic["doctor"], slot_start=slot_at(0))
        with pytest.raises(NotPermitted):
            booking.cancel_appointment(appointment, by_user=clinic["patient_b"])

    def test_cancelling_frees_the_slot(self, db, clinic, slot_at):
        """The partial index excludes cancelled rows, so the time is bookable again."""
        appointment = booking.book_appointment(
            patient=clinic["patient_a"], doctor=clinic["doctor"], slot_start=slot_at(0))
        booking.cancel_appointment(appointment, by_user=clinic["patient_a"])

        rebooked = booking.book_appointment(
            patient=clinic["patient_b"], doctor=clinic["doctor"], slot_start=slot_at(0))
        assert rebooked.status == ApptStatus.BOOKED
        # the freed token number is not reused -- MAX+1, not count+1
        assert rebooked.token_number == 2

    def test_a_completed_appointment_cannot_be_cancelled(self, db, clinic, slot_at):
        appointment = booking.book_appointment(
            patient=clinic["patient_a"], doctor=clinic["doctor"], slot_start=slot_at(0))
        appointment.status = ApptStatus.COMPLETED
        db.session.commit()
        with pytest.raises(SlotUnavailable):
            booking.cancel_appointment(appointment, by_user=clinic["patient_a"])
