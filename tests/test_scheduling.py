"""Slot generation from the weekly pattern."""
from datetime import date, datetime, time, timedelta

import pytest

from hospital.models import ScheduleException
from hospital.services import scheduling
from tests.conftest import make_schedule


class TestSlotGeneration:
    def test_slots_fill_the_sitting(self, db, clinic):
        # 09:00-12:00 in 15 minute slots = 12 slots, and max_tokens is 12
        slots = scheduling.available_slots(clinic["doctor"], clinic["day"])
        assert len(slots) == 12
        assert slots[0].start.time() == time(9, 0)
        assert slots[-1].start.time() == time(11, 45)

    def test_max_tokens_caps_the_sitting(self, db, clinic, tomorrow):
        """A four-hour sitting capped at 5 tokens must yield 5 slots, not 16."""
        doctor = clinic["doctor"]
        for schedule in list(doctor.schedules):
            db.session.delete(schedule)
        db.session.flush()   # the delete must land before the replacement insert
        make_schedule(doctor, tomorrow.weekday(), time(9, 0), time(13, 0),
                      slot_minutes=15, max_tokens=5)
        db.session.commit()
        assert len(scheduling.available_slots(doctor, tomorrow)) == 5

    def test_no_slots_on_a_day_without_a_sitting(self, db, clinic):
        other_day = clinic["day"] + timedelta(days=1)
        while other_day.weekday() == clinic["day"].weekday():
            other_day += timedelta(days=1)
        assert scheduling.available_slots(clinic["doctor"], other_day) == []

    def test_slot_length_is_respected(self, db, clinic):
        slots = scheduling.available_slots(clinic["doctor"], clinic["day"])
        gap = slots[1].start - slots[0].start
        assert gap == timedelta(minutes=15)

    def test_past_slots_are_excluded_today(self, db, clinic, tomorrow):
        """Booking a time that has already passed must not be offered."""
        doctor = clinic["doctor"]
        today = date.today()
        for schedule in list(doctor.schedules):
            db.session.delete(schedule)
        db.session.flush()   # the delete must land before the replacement insert
        make_schedule(doctor, today.weekday(), time(9, 0), time(17, 0), 60, 8)
        db.session.commit()

        # Pretend it is 14:00: only the 14:00-onward slots may remain, and the
        # 14:00 slot itself has started, so the first offered is 15:00.
        noon_ish = datetime.combine(today, time(14, 0))
        slots = scheduling.available_slots(doctor, today, now=noon_ish)
        assert all(s.start > noon_ish for s in slots)


class TestLeave:
    def test_full_day_leave_removes_every_slot(self, db, clinic):
        db.session.add(ScheduleException(
            doctor_id=clinic["doctor"].id, date=clinic["day"],
            is_full_day=True, reason="Conference",
        ))
        db.session.commit()
        assert scheduling.available_slots(clinic["doctor"], clinic["day"]) == []

    def test_partial_leave_removes_only_the_covered_slots(self, db, clinic):
        db.session.add(ScheduleException(
            doctor_id=clinic["doctor"].id, date=clinic["day"],
            is_full_day=False, start_time=time(10, 0), end_time=time(11, 0),
            reason="Ward round",
        ))
        db.session.commit()

        slots = scheduling.available_slots(clinic["doctor"], clinic["day"])
        times = [s.start.time() for s in slots]
        assert time(9, 30) in times          # before the block
        assert time(10, 0) not in times      # inside it
        assert time(10, 45) not in times     # still inside it
        assert time(11, 0) in times          # the block ends at 11:00

    def test_leave_on_another_day_is_ignored(self, db, clinic):
        db.session.add(ScheduleException(
            doctor_id=clinic["doctor"].id,
            date=clinic["day"] + timedelta(days=7), is_full_day=True,
        ))
        db.session.commit()
        assert len(scheduling.available_slots(clinic["doctor"], clinic["day"])) == 12


class TestSessions:
    def test_session_is_created_once_and_reused(self, db, clinic, slot_at):
        first = scheduling.session_for_slot(clinic["doctor"], slot_at(0))
        db.session.commit()
        second = scheduling.session_for_slot(clinic["doctor"], slot_at(3))
        assert first.id == second.id

    def test_session_inherits_the_schedule_settings(self, db, clinic, slot_at):
        session = scheduling.session_for_slot(clinic["doctor"], slot_at(0))
        db.session.commit()
        assert session.slot_minutes == 15
        assert session.max_tokens == 12
        assert session.avg_service_seconds == 15 * 60

    def test_a_time_outside_the_sitting_has_no_session(self, db, clinic):
        far_off = datetime.combine(clinic["day"], time(20, 0))
        assert scheduling.session_for_slot(clinic["doctor"], far_off) is None
