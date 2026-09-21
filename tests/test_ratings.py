"""Ratings and the denormalised roll-up onto the doctor record."""
import pytest

from hospital.models import ApptStatus
from hospital.services import booking, queue, ratings
from hospital.services.errors import InvalidTransition, NotPermitted


@pytest.fixture
def completed(db, clinic, slot_at):
    appointment = booking.book_appointment(
        patient=clinic["patient_a"], doctor=clinic["doctor"], slot_start=slot_at(0))
    queue.call_next(appointment.session)
    queue.complete(appointment)
    return appointment


class TestSubmitRating:
    def test_rating_a_completed_visit(self, db, clinic, completed):
        rating = ratings.submit_rating(
            completed, patient=clinic["patient_a"], stars=5, comment="Excellent")
        assert rating.stars == 5
        assert rating.comment == "Excellent"

    def test_rating_updates_the_doctor_average(self, db, clinic, completed):
        ratings.submit_rating(completed, patient=clinic["patient_a"], stars=4)
        assert clinic["doctor"].avg_rating == 4.0
        assert clinic["doctor"].rating_count == 1

    def test_average_is_the_mean_of_all_ratings(self, db, clinic, slot_at):
        from tests.conftest import make_user
        first = booking.book_appointment(
            patient=clinic["patient_a"], doctor=clinic["doctor"], slot_start=slot_at(0))
        second = booking.book_appointment(
            patient=clinic["patient_b"], doctor=clinic["doctor"], slot_start=slot_at(1))
        for appointment in (first, second):
            queue.call_next(appointment.session)
            queue.complete(appointment)

        ratings.submit_rating(first, patient=clinic["patient_a"], stars=5)
        ratings.submit_rating(second, patient=clinic["patient_b"], stars=2)
        assert clinic["doctor"].avg_rating == 3.5
        assert clinic["doctor"].rating_count == 2

    def test_blank_comment_is_stored_as_nothing(self, db, clinic, completed):
        rating = ratings.submit_rating(
            completed, patient=clinic["patient_a"], stars=3, comment="   ")
        assert rating.comment is None


class TestRatingRejections:
    def test_cannot_rate_an_appointment_that_is_not_finished(self, db, clinic, slot_at):
        appointment = booking.book_appointment(
            patient=clinic["patient_a"], doctor=clinic["doctor"], slot_start=slot_at(0))
        with pytest.raises(InvalidTransition):
            ratings.submit_rating(appointment, patient=clinic["patient_a"], stars=5)

    def test_cannot_rate_someone_elses_visit(self, db, clinic, completed):
        with pytest.raises(NotPermitted):
            ratings.submit_rating(completed, patient=clinic["patient_b"], stars=5)

    def test_cannot_rate_the_same_visit_twice(self, db, clinic, completed):
        ratings.submit_rating(completed, patient=clinic["patient_a"], stars=5)
        with pytest.raises(InvalidTransition):
            ratings.submit_rating(completed, patient=clinic["patient_a"], stars=1)

    @pytest.mark.parametrize("stars", [0, 6, -1, 100])
    def test_out_of_range_ratings_are_rejected(self, db, clinic, completed, stars):
        with pytest.raises(InvalidTransition):
            ratings.submit_rating(completed, patient=clinic["patient_a"], stars=stars)
