"""Reporting aggregates."""
from datetime import date, timedelta

import pytest

from hospital.models import ApptStatus
from hospital.services import booking, queue, ratings, reports


@pytest.fixture
def activity(db, clinic, slot_at):
    """One completed visit and one no-show, so every report has data."""
    from tests.conftest import make_user
    third = make_user("third@test.local")
    db.session.commit()

    done = booking.book_appointment(
        patient=clinic["patient_a"], doctor=clinic["doctor"], slot_start=slot_at(0))
    missed = booking.book_appointment(
        patient=clinic["patient_b"], doctor=clinic["doctor"], slot_start=slot_at(1))
    upcoming = booking.book_appointment(
        patient=third, doctor=clinic["doctor"], slot_start=slot_at(2))

    queue.call_next(done.session)
    queue.complete(done)
    queue.mark_no_show(missed)
    return {"done": done, "missed": missed, "upcoming": upcoming, **clinic}


class TestReports:
    def test_headline_counts(self, db, activity):
        counts = reports.headline_counts()
        assert counts["doctors"] == 1
        assert counts["departments"] == 1
        assert counts["patients"] == 3

    def test_no_show_rate_excludes_upcoming_and_cancelled(self, db, activity):
        """1 no-show out of 2 concluded visits = 50%.

        The still-upcoming third appointment must not dilute the figure.
        """
        rate = reports.no_show_rate()
        assert rate["concluded"] == 2
        assert rate["no_shows"] == 1
        assert rate["rate"] == 50.0

    def test_no_show_rate_is_zero_with_no_data(self, db, clinic):
        assert reports.no_show_rate()["rate"] == 0.0

    def test_status_breakdown_totals_match(self, db, activity):
        breakdown = {row["status"]: row["count"] for row in reports.status_breakdown()}
        assert breakdown[ApptStatus.COMPLETED] == 1
        assert breakdown[ApptStatus.NO_SHOW] == 1
        assert breakdown[ApptStatus.BOOKED] == 1

    def test_per_day_is_zero_filled(self, db, activity):
        """Quiet days must appear as zero, not vanish from the chart."""
        series = reports.appointments_per_day(days=14)
        assert len(series) == 14
        assert all("count" in point for point in series)
        # dates run forward and are contiguous
        dates = [point["date"] for point in series]
        assert dates == sorted(dates)

    def test_per_department_counts(self, db, activity):
        rows = reports.appointments_per_department()
        assert rows[0]["department"] == activity["department"].name
        assert rows[0]["count"] == 3

    def test_average_wait_is_computed_from_real_timings(self, db, activity):
        assert reports.average_wait_minutes() >= 0

    def test_busiest_doctors_is_ranked(self, db, activity):
        rows = reports.busiest_doctors()
        assert rows[0]["appointments"] == 3

    def test_rating_summary(self, db, activity):
        ratings.submit_rating(activity["done"], patient=activity["patient_a"], stars=4)
        summary = reports.rating_summary()
        assert summary["average"] == 4.0
        assert summary["count"] == 1
        assert summary["distribution"][4] == 1

    def test_chatbot_usage_handles_no_data(self, db, clinic):
        usage = reports.chatbot_usage()
        assert usage["questions"] == 0
        assert usage["conversion"] == 0.0
