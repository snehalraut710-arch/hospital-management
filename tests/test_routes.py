"""Route-level tests: access control, and the full journey through HTTP."""
from datetime import datetime, timedelta

import pytest

from hospital.models import Appointment, ApptStatus, Role
from hospital.services import booking, queue
from tests.conftest import make_department, make_doctor, make_schedule, make_user


@pytest.fixture
def site(db, tomorrow):
    """A hospital with an admin, a doctor and a patient, all able to sign in."""
    department = make_department("Cardiology")
    doctor = make_doctor(department, name="Arun Mehta", email="doctor@test.local")
    make_schedule(doctor, tomorrow.weekday())
    admin = make_user("admin@test.local", role=Role.ADMIN)
    patient = make_user("patient@test.local")
    db.session.commit()
    return {"department": department, "doctor": doctor, "admin": admin,
            "patient": patient, "day": tomorrow}


def login(client, email, password="password123"):
    return client.post("/auth/login",
                       data={"email": email, "password": password},
                       follow_redirects=True)


class TestPublicPages:
    @pytest.mark.parametrize("path", ["/", "/departments", "/doctors", "/about", "/chat/",
                                      "/auth/login", "/auth/register"])
    def test_public_pages_load_without_an_account(self, client, site, path):
        assert client.get(path).status_code == 200

    def test_department_page_lists_its_doctors(self, client, site):
        response = client.get(f"/departments/{site['department'].slug}")
        assert b"Arun Mehta" in response.data

    def test_unknown_department_is_a_404(self, client, site):
        assert client.get("/departments/nonexistent").status_code == 404


class TestAuthentication:
    def test_registration_creates_a_patient_and_signs_them_in(self, client, db, site):
        response = client.post("/auth/register", data={
            "name": "New Patient", "email": "new@test.local",
            "password": "secret123", "confirm": "secret123",
        }, follow_redirects=True)
        assert response.status_code == 200

        from hospital.models import User
        user = User.query.filter_by(email="new@test.local").first()
        assert user is not None
        assert user.role == Role.PATIENT
        assert user.patient_profile is not None

    def test_registration_cannot_grant_a_privileged_role(self, client, db, site):
        """Posting role=admin must be ignored -- self-registration is patients only."""
        client.post("/auth/register", data={
            "name": "Sneaky", "email": "sneaky@test.local",
            "password": "secret123", "confirm": "secret123",
            "role": "admin",
        }, follow_redirects=True)

        from hospital.models import User
        assert User.query.filter_by(email="sneaky@test.local").first().role == Role.PATIENT

    def test_mismatched_passwords_are_rejected(self, client, db, site):
        client.post("/auth/register", data={
            "name": "Nope", "email": "nope@test.local",
            "password": "secret123", "confirm": "different",
        })
        from hospital.models import User
        assert User.query.filter_by(email="nope@test.local").first() is None

    def test_duplicate_email_is_rejected(self, client, db, site):
        response = client.post("/auth/register", data={
            "name": "Clash", "email": "patient@test.local",
            "password": "secret123", "confirm": "secret123",
        })
        assert b"already exists" in response.data

    def test_wrong_password_does_not_sign_in(self, client, site):
        response = login(client, "patient@test.local", "wrongpassword")
        assert b"Incorrect email or password" in response.data

    def test_login_does_not_reveal_whether_an_email_exists(self, client, site):
        """Different messages here would let anyone enumerate registered users."""
        unknown = login(client, "nobody@test.local", "whatever")
        wrong = login(client, "patient@test.local", "wrongpassword")
        assert b"Incorrect email or password" in unknown.data
        assert b"Incorrect email or password" in wrong.data

    def test_a_deactivated_account_cannot_sign_in(self, client, db, site):
        site["patient"].is_active_flag = False
        db.session.commit()
        response = login(client, "patient@test.local")
        assert b"deactivated" in response.data

    def test_each_role_lands_on_its_own_workspace(self, client, site):
        assert b"Queue console" in login(client, "doctor@test.local").data
        client.get("/auth/logout")
        assert b"Hospital overview" in login(client, "admin@test.local").data


class TestAccessControl:
    def test_anonymous_visitors_are_sent_to_sign_in(self, client, site):
        response = client.get("/patient/", follow_redirects=True)
        assert b"Sign in" in response.data

    @pytest.mark.parametrize("path", ["/admin/", "/admin/doctors", "/doctor/queue"])
    def test_a_patient_cannot_reach_staff_pages(self, client, site, path):
        login(client, "patient@test.local")
        assert client.get(path).status_code == 403

    @pytest.mark.parametrize("path", ["/admin/", "/patient/"])
    def test_a_doctor_cannot_reach_other_workspaces(self, client, site, path):
        login(client, "doctor@test.local")
        assert client.get(path).status_code == 403

    def test_an_admin_cannot_reach_the_patient_workspace(self, client, site):
        login(client, "admin@test.local")
        assert client.get("/patient/").status_code == 403

    def test_a_patient_cannot_open_another_patients_appointment(self, client, db, site, tomorrow):
        from datetime import time
        other = make_user("other@test.local")
        db.session.commit()
        appointment = booking.book_appointment(
            patient=other, doctor=site["doctor"],
            slot_start=datetime.combine(tomorrow, time(9, 0)))

        login(client, "patient@test.local")
        assert client.get(f"/patient/appointments/{appointment.id}").status_code == 403

    def test_a_doctor_cannot_open_another_doctors_consultation(self, client, db, site, tomorrow):
        from datetime import time
        other_doctor = make_doctor(site["department"], name="Priya Nair",
                                   email="other.doctor@test.local")
        make_schedule(other_doctor, tomorrow.weekday())
        db.session.commit()
        appointment = booking.book_appointment(
            patient=site["patient"], doctor=other_doctor,
            slot_start=datetime.combine(tomorrow, time(9, 0)))

        login(client, "doctor@test.local")
        assert client.get(f"/doctor/consult/{appointment.id}").status_code == 403


class TestBookingJourney:
    def test_a_patient_can_book_end_to_end(self, client, db, site, tomorrow):
        from datetime import time
        login(client, "patient@test.local")

        # the slot list is offered
        response = client.get(f"/patient/book/doctor/{site['doctor'].id}"
                              f"?date={tomorrow.isoformat()}")
        assert response.status_code == 200

        slot = datetime.combine(tomorrow, time(9, 0))
        response = client.post("/patient/book/confirm", data={
            "doctor_id": site["doctor"].id,
            "slot_start": slot.strftime("%Y-%m-%d %H:%M"),
            "symptoms": "chest pain when climbing stairs",
        }, follow_redirects=True)
        assert response.status_code == 200

        appointment = Appointment.query.filter_by(patient_id=site["patient"].id).one()
        assert appointment.token_number == 1
        assert appointment.symptoms_text == "chest pain when climbing stairs"
        # the symptoms went through triage on the way in
        assert appointment.triage_specialty is not None

    def test_booking_a_taken_slot_shows_an_error(self, client, db, site, tomorrow):
        from datetime import time
        slot = datetime.combine(tomorrow, time(9, 0))
        other = make_user("other@test.local")
        db.session.commit()
        booking.book_appointment(patient=other, doctor=site["doctor"], slot_start=slot)

        login(client, "patient@test.local")
        response = client.post("/patient/book/confirm", data={
            "doctor_id": site["doctor"].id,
            "slot_start": slot.strftime("%Y-%m-%d %H:%M"),
        }, follow_redirects=True)
        assert b"just taken" in response.data or b"already" in response.data

    def test_a_malformed_slot_is_handled(self, client, site):
        login(client, "patient@test.local")
        response = client.post("/patient/book/confirm", data={
            "doctor_id": site["doctor"].id, "slot_start": "not-a-date",
        }, follow_redirects=True)
        assert response.status_code == 200
        assert b"not understood" in response.data

    def test_a_patient_can_cancel(self, client, db, site, tomorrow):
        from datetime import time
        appointment = booking.book_appointment(
            patient=site["patient"], doctor=site["doctor"],
            slot_start=datetime.combine(tomorrow, time(9, 0)))

        login(client, "patient@test.local")
        client.post(f"/patient/appointments/{appointment.id}/cancel",
                    data={"reason": "Cannot make it"}, follow_redirects=True)
        assert appointment.status == ApptStatus.CANCELLED


class TestDoctorConsole:
    def test_the_doctor_can_run_the_queue(self, client, db, site, tomorrow):
        from datetime import time
        appointment = booking.book_appointment(
            patient=site["patient"], doctor=site["doctor"],
            slot_start=datetime.combine(tomorrow, time(9, 0)))
        session_id = appointment.session_id

        login(client, "doctor@test.local")
        client.post(f"/doctor/session/{session_id}/call-next", follow_redirects=True)
        assert appointment.status == ApptStatus.IN_CONSULT

        client.post(f"/doctor/consult/{appointment.id}", data={
            "diagnosis": "Stable angina",
            "notes": "Examined, vitals stable.",
            "drug": ["Aspirin 75mg"], "dosage": ["1 tablet"],
            "frequency": ["Once daily"], "duration": ["30 days"],
            "instructions": ["After breakfast"],
        }, follow_redirects=True)

        assert appointment.status == ApptStatus.COMPLETED
        assert appointment.consultation.diagnosis == "Stable angina"
        assert len(appointment.consultation.prescription_items) == 1
        assert appointment.consultation.prescription_items[0].drug == "Aspirin 75mg"

    def test_blank_prescription_rows_are_discarded(self, client, db, site, tomorrow):
        """The form always posts spare empty rows; they must not become records."""
        from datetime import time
        appointment = booking.book_appointment(
            patient=site["patient"], doctor=site["doctor"],
            slot_start=datetime.combine(tomorrow, time(9, 0)))
        queue.call_next(appointment.session)

        login(client, "doctor@test.local")
        client.post(f"/doctor/consult/{appointment.id}", data={
            "diagnosis": "Viral fever",
            "drug": ["Paracetamol", "", "  "],
            "dosage": ["1 tablet", "", ""],
            "frequency": ["Twice daily", "", ""],
            "duration": ["3 days", "", ""],
            "instructions": ["After food", "", ""],
        }, follow_redirects=True)
        assert len(appointment.consultation.prescription_items) == 1

    def test_closing_the_clinic_marks_no_shows(self, client, db, site, tomorrow):
        from datetime import time
        appointment = booking.book_appointment(
            patient=site["patient"], doctor=site["doctor"],
            slot_start=datetime.combine(tomorrow, time(9, 0)))

        login(client, "doctor@test.local")
        client.post(f"/doctor/session/{appointment.session_id}/close", follow_redirects=True)
        assert appointment.status == ApptStatus.NO_SHOW


class TestAdmin:
    def test_admin_can_add_a_doctor(self, client, db, site):
        login(client, "admin@test.local")
        client.post("/admin/doctors/new", data={
            "name": "Kavita Rao", "email": "kavita@test.local", "password": "secret123",
            "department_id": site["department"].id, "qualification": "MBBS, MD",
            "experience_years": "12", "consultation_fee": "600", "room_no": "105",
        }, follow_redirects=True)

        from hospital.models import User
        user = User.query.filter_by(email="kavita@test.local").first()
        assert user is not None and user.role == Role.DOCTOR
        assert user.doctor.room_no == "105"

    def test_admin_can_add_a_department(self, client, db, site):
        login(client, "admin@test.local")
        client.post("/admin/departments",
                    data={"name": "Nephrology", "icon": "KD"}, follow_redirects=True)
        from hospital.models import Department
        created = Department.query.filter_by(name="Nephrology").first()
        assert created is not None
        assert created.slug == "nephrology"

    def test_admin_can_add_a_sitting(self, client, db, site):
        login(client, "admin@test.local")
        before = len(site["doctor"].schedules)
        client.post(f"/admin/doctors/{site['doctor'].id}/schedule", data={
            "weekday": "3", "start_time": "14:00", "end_time": "17:00",
            "slot_minutes": "20", "max_tokens": "9",
        }, follow_redirects=True)
        assert len(site["doctor"].schedules) == before + 1

    def test_a_sitting_that_ends_before_it_starts_is_rejected(self, client, db, site):
        login(client, "admin@test.local")
        before = len(site["doctor"].schedules)
        response = client.post(f"/admin/doctors/{site['doctor'].id}/schedule", data={
            "weekday": "3", "start_time": "17:00", "end_time": "14:00",
        }, follow_redirects=True)
        assert b"after the start time" in response.data
        assert len(site["doctor"].schedules) == before

    def test_recording_leave_notifies_affected_patients(self, client, db, site, tomorrow):
        from datetime import time
        from hospital.models import Notification
        booking.book_appointment(
            patient=site["patient"], doctor=site["doctor"],
            slot_start=datetime.combine(tomorrow, time(9, 0)))

        login(client, "admin@test.local")
        client.post(f"/admin/doctors/{site['doctor'].id}/leave", data={
            "date": tomorrow.isoformat(), "reason": "Conference",
        }, follow_redirects=True)

        note = Notification.query.filter_by(user_id=site["patient"].id).first()
        assert note is not None
        assert "rebooking" in note.title.lower()

    def test_deactivating_a_doctor_hides_them_from_the_public_list(self, client, db, site):
        login(client, "admin@test.local")
        client.post(f"/admin/doctors/{site['doctor'].id}/toggle", follow_redirects=True)
        client.get("/auth/logout")
        assert b"Arun Mehta" not in client.get("/doctors").data


class TestChatbotEndpoint:
    def test_it_answers_without_an_account(self, client, site):
        response = client.post("/chat/message", json={"message": "itchy rash on my arms"})
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["result"]["department"]
        assert payload["reply"]

    def test_an_emergency_is_flagged(self, client, site):
        response = client.post(
            "/chat/message", json={"message": "chest pain radiating to my left arm"})
        payload = response.get_json()
        assert payload["result"]["is_red_flag"] is True

    def test_empty_input_is_handled(self, client, site):
        payload = client.post("/chat/message", json={"message": "   "}).get_json()
        assert payload["result"] is None
        assert payload["reply"]

    def test_it_only_recommends_staffed_departments(self, client, site):
        """Only Cardiology is staffed here, so nothing else may be suggested."""
        payload = client.post(
            "/chat/message", json={"message": "itchy red rash on my skin"}).get_json()
        assert payload["result"]["department"] == "Cardiology"

    def test_the_conversation_is_recorded(self, client, db, site):
        from hospital.models import ChatMessage
        client.post("/chat/message", json={"message": "persistent cough"})
        assert ChatMessage.query.filter_by(sender="user").count() == 1
        assert ChatMessage.query.filter_by(sender="bot").count() == 1

    def test_overlong_input_is_truncated_not_rejected(self, client, site):
        response = client.post("/chat/message", json={"message": "chest pain " * 500})
        assert response.status_code == 200


class TestApi:
    def test_the_queue_board_is_public(self, client, db, site, tomorrow):
        from datetime import time
        appointment = booking.book_appointment(
            patient=site["patient"], doctor=site["doctor"],
            slot_start=datetime.combine(tomorrow, time(9, 0)))
        payload = client.get(f"/api/queue/{appointment.session_id}").get_json()
        assert payload["tokens"][0]["token"] == 1

    def test_patient_names_are_hidden_from_the_public_board(self, client, db, site, tomorrow):
        """A waiting-room display shows token numbers, not who people are."""
        from datetime import time
        appointment = booking.book_appointment(
            patient=site["patient"], doctor=site["doctor"],
            slot_start=datetime.combine(tomorrow, time(9, 0)))
        payload = client.get(f"/api/queue/{appointment.session_id}").get_json()
        assert payload["tokens"][0]["patient"] is None

    def test_staff_see_patient_names(self, client, db, site, tomorrow):
        from datetime import time
        appointment = booking.book_appointment(
            patient=site["patient"], doctor=site["doctor"],
            slot_start=datetime.combine(tomorrow, time(9, 0)))
        login(client, "doctor@test.local")
        payload = client.get(f"/api/queue/{appointment.session_id}").get_json()
        assert payload["tokens"][0]["patient"] == site["patient"].name

    def test_position_requires_ownership(self, client, db, site, tomorrow):
        from datetime import time
        other = make_user("other@test.local")
        db.session.commit()
        appointment = booking.book_appointment(
            patient=other, doctor=site["doctor"],
            slot_start=datetime.combine(tomorrow, time(9, 0)))

        login(client, "patient@test.local")
        assert client.get(f"/api/my-position/{appointment.id}").status_code == 403

    def test_slots_endpoint_rejects_a_bad_date(self, client, site):
        assert client.get(f"/api/slots/{site['doctor'].id}?date=garbage").status_code == 400


class TestCheckIn:
    """Check-in is what makes call_next's 'prefer patients who have arrived'
    rule mean anything, so it needs to be reachable from the UI.

    These build appointment rows at fixed times rather than deriving slots from
    the wall clock: arithmetic like "now + 2 hours" silently rolls past midnight
    onto a day with no clinic, so such a test would pass all afternoon and fail
    late at night.
    """

    def _today_appointment(self, db, site, token=1, hour=9):
        from datetime import date, datetime, time, timedelta
        from tests.conftest import make_schedule
        from hospital.services import scheduling

        doctor = site["doctor"]
        today = date.today()
        if not any(s.weekday == today.weekday() for s in doctor.schedules):
            make_schedule(doctor, today.weekday(), time(8, 0), time(20, 0),
                          slot_minutes=30, max_tokens=20)
            db.session.commit()

        slot = datetime.combine(today, time(hour, 0))
        schedule = next(s for s in doctor.schedules if s.weekday == today.weekday())
        session = scheduling.get_or_create_session(doctor, today, schedule)
        db.session.commit()

        appointment = Appointment(
            patient_id=site["patient"].id, doctor_id=doctor.id,
            session_id=session.id, appt_date=today,
            slot_start=slot, slot_end=slot + timedelta(minutes=30),
            token_number=token, status=ApptStatus.BOOKED,
        )
        db.session.add(appointment)
        db.session.commit()
        return appointment

    def test_a_patient_can_check_in_on_the_day(self, client, db, site):
        appointment = self._today_appointment(db, site)
        login(client, "patient@test.local")
        client.post(f"/patient/appointments/{appointment.id}/check-in",
                    follow_redirects=True)
        assert appointment.status == ApptStatus.CHECKED_IN
        assert appointment.checked_in_at is not None

    def test_checking_in_early_is_refused(self, client, db, site, tomorrow):
        from datetime import time
        appointment = booking.book_appointment(
            patient=site["patient"], doctor=site["doctor"],
            slot_start=datetime.combine(tomorrow, time(9, 0)))
        login(client, "patient@test.local")
        response = client.post(f"/patient/appointments/{appointment.id}/check-in",
                               follow_redirects=True)
        assert b"on the day" in response.data
        assert appointment.status == ApptStatus.BOOKED

    def test_a_patient_cannot_check_someone_else_in(self, client, db, site):
        from tests.conftest import make_user
        appointment = self._today_appointment(db, site)
        make_user("stranger@test.local")
        db.session.commit()
        login(client, "stranger@test.local")
        assert client.post(
            f"/patient/appointments/{appointment.id}/check-in").status_code == 403

    def test_reception_can_check_a_patient_in(self, client, db, site):
        appointment = self._today_appointment(db, site)
        login(client, "doctor@test.local")
        client.post(f"/doctor/appointment/{appointment.id}/check-in",
                    follow_redirects=True)
        assert appointment.status == ApptStatus.CHECKED_IN

    def test_checked_in_patients_are_called_first(self, client, db, site):
        """The end-to-end payoff: arriving gets you seen before someone who hasn't."""
        from tests.conftest import make_user
        first = self._today_appointment(db, site, token=1, hour=9)
        later = make_user("later@test.local")
        db.session.commit()
        second = self._today_appointment(db, site, token=2, hour=10)
        second.patient_id = later.id
        db.session.commit()

        login(client, "doctor@test.local")
        client.post(f"/doctor/appointment/{second.id}/check-in", follow_redirects=True)
        client.post(f"/doctor/session/{first.session_id}/call-next", follow_redirects=True)

        assert second.status == ApptStatus.IN_CONSULT, "arrived patient should be called"
        assert first.status == ApptStatus.BOOKED, "absent patient should still be waiting"

    def test_cannot_check_in_twice(self, client, db, site):
        appointment = self._today_appointment(db, site)
        login(client, "patient@test.local")
        client.post(f"/patient/appointments/{appointment.id}/check-in", follow_redirects=True)
        response = client.post(f"/patient/appointments/{appointment.id}/check-in",
                               follow_redirects=True)
        assert b"Cannot move token" in response.data
        assert appointment.status == ApptStatus.CHECKED_IN
