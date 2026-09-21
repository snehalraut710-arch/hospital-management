"""The queue state machine, wait estimates, and session lifecycle."""
from datetime import datetime, timedelta

import pytest

from hospital.models import ApptStatus, SessionStatus
from hospital.services import booking, queue, scheduling
from hospital.services.errors import InvalidTransition


@pytest.fixture
def queued(db, clinic, slot_at):
    """Three patients booked into tomorrow's sitting, tokens 1-3."""
    from tests.conftest import make_user
    third = make_user("third@test.local")
    db.session.commit()

    appointments = [
        booking.book_appointment(patient=p, doctor=clinic["doctor"], slot_start=slot_at(i))
        for i, p in enumerate([clinic["patient_a"], clinic["patient_b"], third])
    ]
    return {"appointments": appointments, "session": appointments[0].session, **clinic}


class TestTransitions:
    def test_check_in_marks_arrival(self, db, queued):
        appointment = queued["appointments"][0]
        queue.check_in(appointment)
        assert appointment.status == ApptStatus.CHECKED_IN
        assert appointment.checked_in_at is not None

    def test_call_next_takes_the_lowest_token(self, db, queued):
        called = queue.call_next(queued["session"])
        assert called.token_number == 1
        assert called.status == ApptStatus.IN_CONSULT
        assert queued["session"].current_token == 1

    def test_call_next_prefers_a_checked_in_patient(self, db, queued):
        """Someone known to be in the building is seen before someone who may not be."""
        queue.check_in(queued["appointments"][2])   # token 3 has arrived
        called = queue.call_next(queued["session"])
        assert called.token_number == 3

    def test_calling_next_opens_the_clinic(self, db, queued):
        assert queued["session"].status == SessionStatus.NOT_STARTED
        queue.call_next(queued["session"])
        assert queued["session"].status == SessionStatus.IN_PROGRESS
        assert queued["session"].started_at is not None

    def test_cannot_call_two_patients_at_once(self, db, queued):
        queue.call_next(queued["session"])
        with pytest.raises(InvalidTransition):
            queue.call_next(queued["session"])

    def test_completing_frees_the_room(self, db, queued):
        first = queue.call_next(queued["session"])
        queue.complete(first)
        assert first.status == ApptStatus.COMPLETED
        assert first.completed_at is not None
        assert queued["session"].served_count == 1

        second = queue.call_next(queued["session"])
        assert second.token_number == 2

    def test_call_next_on_an_empty_queue_returns_none(self, db, queued):
        for _ in range(3):
            queue.complete(queue.call_next(queued["session"]))
        assert queue.call_next(queued["session"]) is None

    def test_no_show_removes_them_from_the_queue(self, db, queued):
        queue.mark_no_show(queued["appointments"][0])
        assert queued["appointments"][0].status == ApptStatus.NO_SHOW
        assert queue.call_next(queued["session"]).token_number == 2


class TestIllegalTransitions:
    """A queue that has quietly lost track of who is being seen is worse than
    one that refuses an operation."""

    def test_cannot_complete_before_being_called(self, db, queued):
        with pytest.raises(InvalidTransition):
            queue.complete(queued["appointments"][0])

    def test_cannot_complete_twice(self, db, queued):
        first = queue.call_next(queued["session"])
        queue.complete(first)
        with pytest.raises(InvalidTransition):
            queue.complete(first)

    def test_cannot_check_in_after_completion(self, db, queued):
        first = queue.call_next(queued["session"])
        queue.complete(first)
        with pytest.raises(InvalidTransition):
            queue.check_in(first)

    def test_cannot_no_show_a_completed_appointment(self, db, queued):
        first = queue.call_next(queued["session"])
        queue.complete(first)
        with pytest.raises(InvalidTransition):
            queue.mark_no_show(first)

    def test_cancelled_is_terminal(self, db, queued):
        appointment = queued["appointments"][0]
        booking.cancel_appointment(appointment, by_user=queued["patient_a"])
        with pytest.raises(InvalidTransition):
            queue.check_in(appointment)


class TestSessionLifecycle:
    def test_closing_marks_waiting_patients_as_no_shows(self, db, queued):
        queue.close_session(queued["session"])
        statuses = [a.status for a in queued["appointments"]]
        assert statuses == [ApptStatus.NO_SHOW] * 3
        assert queued["session"].status == SessionStatus.CLOSED

    def test_closing_completes_an_open_consultation(self, db, queued):
        """A doctor who closes up mid-consultation should not lose the record."""
        called = queue.call_next(queued["session"])
        queue.close_session(queued["session"])
        assert called.status == ApptStatus.COMPLETED
        assert called.completed_at is not None

    def test_closing_leaves_finished_appointments_alone(self, db, queued):
        first = queue.call_next(queued["session"])
        queue.complete(first)
        queue.close_session(queued["session"])
        assert first.status == ApptStatus.COMPLETED

    def test_closing_twice_is_harmless(self, db, queued):
        queue.close_session(queued["session"])
        queue.close_session(queued["session"])
        assert queued["session"].status == SessionStatus.CLOSED

    def test_cannot_reopen_a_closed_clinic(self, db, queued):
        queue.close_session(queued["session"])
        with pytest.raises(InvalidTransition):
            queue.open_session(queued["session"])


class TestWaitEstimates:
    def test_position_counts_only_those_ahead(self, db, queued):
        position = queue.position_of(queued["appointments"][2])   # token 3
        assert position.token == 3
        assert position.people_ahead == 2

    def test_first_in_line_waits_for_nobody(self, db, queued):
        position = queue.position_of(queued["appointments"][0])
        assert position.people_ahead == 0
        assert position.estimated_wait_minutes == 0

    def test_estimate_uses_the_slot_length_before_any_data(self, db, queued):
        # 2 people ahead * 15 minute default = 30
        assert queue.position_of(queued["appointments"][2]).estimated_wait_minutes == 30

    def test_estimate_tracks_the_doctor_running_fast(self, db, queued):
        """A doctor going quicker than scheduled should shorten the estimate."""
        session = queued["session"]
        called = queue.call_next(session)
        called.called_at = datetime.now() - timedelta(minutes=3)
        queue.complete(called)

        assert session.avg_service_seconds < 15 * 60
        assert queue.position_of(queued["appointments"][2]).estimated_wait_minutes < 30

    def test_being_seen_means_no_further_wait(self, db, queued):
        called = queue.call_next(queued["session"])
        position = queue.position_of(called)
        assert position.status == ApptStatus.IN_CONSULT
        assert position.estimated_wait_minutes == 0

    def test_a_cancelled_patient_stops_blocking_the_queue(self, db, queued):
        booking.cancel_appointment(queued["appointments"][0], by_user=queued["patient_a"])
        assert queue.position_of(queued["appointments"][2]).people_ahead == 1


class TestSnapshot:
    def test_snapshot_reports_the_queue(self, db, queued):
        called = queue.call_next(queued["session"])
        data = queue.snapshot(queued["session"])
        assert data["now_serving"] == called.token_number
        assert data["waiting"] == 2
        assert len(data["tokens"]) == 3
        assert data["department"] == queued["department"].name

    def test_cancelled_appointments_leave_the_board(self, db, queued):
        booking.cancel_appointment(queued["appointments"][0], by_user=queued["patient_a"])
        data = queue.snapshot(queued["session"])
        assert len(data["tokens"]) == 2
        assert 1 not in [t["token"] for t in data["tokens"]]

    def test_snapshot_is_json_serialisable(self, db, queued):
        import json
        json.dumps(queue.snapshot(queued["session"]))
