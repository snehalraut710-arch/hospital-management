"""Shared fixtures.

Tests run against an in-memory SQLite database built fresh for each test, so
they never touch the developer's hospital.db and cannot leak state into each
other.
"""
from datetime import date, datetime, time, timedelta

import pytest

from config import TestConfig
from hospital import create_app
from hospital.extensions import db as _db
from hospital.models import (
    Appointment, ApptStatus, ClinicSession, Department, Doctor, DoctorSchedule,
    PatientProfile, Role, SymptomKB, User,
)


@pytest.fixture
def app():
    application = create_app(TestConfig)
    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def db(app):
    return _db


@pytest.fixture
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def make_user(email, role=Role.PATIENT, name=None, password="password123"):
    user = User(name=name or email.split("@")[0].title(), email=email, role=role)
    user.set_password(password)
    _db.session.add(user)
    _db.session.flush()
    if role == Role.PATIENT:
        _db.session.add(PatientProfile(user_id=user.id))
        _db.session.flush()
    return user


def make_department(name="Cardiology", slug=None, with_kb=True):
    """Create a department, and by default load its curated triage rules.

    The knowledge base is loaded because the chatbot's red-flag behaviour lives
    there: a test hospital without it would silently exercise a different
    triage engine than the real one.
    """
    department = Department(name=name, slug=slug or name.lower().replace(" ", "-"))
    _db.session.add(department)
    _db.session.flush()

    if with_kb:
        from hospital.data.knowledge_base import SYMPTOM_KB
        for dept_name, phrase, weight, is_red_flag, advice in SYMPTOM_KB:
            if dept_name == name:
                _db.session.add(SymptomKB(
                    department_id=department.id, phrase=phrase, weight=weight,
                    is_red_flag=is_red_flag, advice=advice,
                ))
        _db.session.flush()
    return department


def make_doctor(department, name="Arun Mehta", email=None, **kwargs):
    user = make_user(email or f"{name.lower().replace(' ', '.')}@hospital.test",
                     role=Role.DOCTOR, name=name)
    doctor = Doctor(
        user_id=user.id, department_id=department.id,
        qualification=kwargs.get("qualification", "MBBS, MD"),
        experience_years=kwargs.get("experience_years", 10),
        consultation_fee=kwargs.get("consultation_fee", 500),
        room_no=kwargs.get("room_no", "101"),
    )
    _db.session.add(doctor)
    _db.session.flush()
    return doctor


def make_schedule(doctor, weekday, start=time(9, 0), end=time(12, 0),
                  slot_minutes=15, max_tokens=12):
    schedule = DoctorSchedule(
        doctor_id=doctor.id, weekday=weekday, start_time=start, end_time=end,
        slot_minutes=slot_minutes, max_tokens=max_tokens,
    )
    _db.session.add(schedule)
    _db.session.flush()
    return schedule


@pytest.fixture
def tomorrow():
    """A weekday in the future -- avoids 'the slot has passed' on same-day tests."""
    day = date.today() + timedelta(days=1)
    while day.weekday() == 6:
        day += timedelta(days=1)
    return day


@pytest.fixture
def clinic(db, tomorrow):
    """A doctor with a sitting tomorrow, and two registered patients."""
    department = make_department()
    doctor = make_doctor(department)
    schedule = make_schedule(doctor, tomorrow.weekday())
    patient_a = make_user("patient.a@test.local")
    patient_b = make_user("patient.b@test.local")
    db.session.commit()
    return {
        "department": department, "doctor": doctor, "schedule": schedule,
        "patient_a": patient_a, "patient_b": patient_b, "day": tomorrow,
    }


@pytest.fixture
def slot_at(clinic):
    """Helper: the nth slot of tomorrow's sitting."""
    def _slot(index=0):
        return datetime.combine(clinic["day"], clinic["schedule"].start_time) + timedelta(
            minutes=clinic["schedule"].slot_minutes * index
        )
    return _slot
