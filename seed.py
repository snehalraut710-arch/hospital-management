"""Realistic demo data.

Deliberately not random noise: the seeded hospital has a *history* (four weeks of
past appointments with consultations and ratings) and a *present* (today's
clinics, one of them part-way through its queue). That means every screen --
dashboards, charts, the live board, no-show statistics -- has something real to
show the moment the app starts.

Run with:  flask --app wsgi seed --reset
"""
from datetime import date, datetime, time, timedelta
import random

from sqlalchemy import func

from hospital.extensions import db
from hospital.data.knowledge_base import DEPARTMENTS, SYMPTOM_KB
from hospital.models import (
    Appointment, ApptStatus, ChatMessage, ClinicSession, Consultation, Department,
    Doctor, DoctorSchedule, PatientProfile, PrescriptionItem, Rating, Role,
    ScheduleException, SessionStatus, SymptomKB, User,
)

# A fixed seed keeps the demo reproducible: the same charts and the same queue
# every time, so a walkthrough you rehearsed still matches what appears.
RNG = random.Random(20260822)


def next_token(session) -> int:
    """MAX+1 from the database, not from the in-memory relationship.

    The relationship is cached per identity map and does not see rows added
    earlier in this same transaction, which silently reissues token 1.
    """
    highest = (
        db.session.query(func.max(Appointment.token_number))
        .filter(Appointment.session_id == session.id)
        .scalar()
    )
    return (highest or 0) + 1


def slugify(value: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


DOCTORS = [
    # (name, department, qualification, years, fee, room)
    ("Arun Mehta",       "Cardiology",       "MBBS, MD, DM (Cardiology)",       18, 900, "201"),
    ("Priya Nair",       "Cardiology",       "MBBS, MD (Medicine)",              9, 700, "202"),
    ("Kavita Rao",       "Dermatology",      "MBBS, MD (Dermatology)",          12, 600, "105"),
    ("Sanjay Gupta",     "Orthopaedics",     "MBBS, MS (Orthopaedics)",         20, 800, "301"),
    ("Neha Sharma",      "Orthopaedics",     "MBBS, DNB (Orthopaedics)",         7, 650, "302"),
    ("Rakesh Iyer",      "Neurology",        "MBBS, MD, DM (Neurology)",        15, 950, "401"),
    ("Meera Krishnan",   "Gastroenterology", "MBBS, MD, DM (Gastro)",           11, 850, "403"),
    ("Vikram Desai",     "ENT",              "MBBS, MS (ENT)",                  14, 550, "106"),
    ("Anjali Verma",     "Ophthalmology",    "MBBS, MS (Ophthalmology)",        10, 500, "107"),
    ("Suresh Patel",     "Pulmonology",      "MBBS, MD (Pulmonary Medicine)",   16, 750, "404"),
    ("Divya Menon",      "Paediatrics",      "MBBS, MD (Paediatrics)",           8, 600, "108"),
    ("Rohit Bansal",     "Psychiatry",       "MBBS, MD (Psychiatry)",           13, 800, "501"),
    ("Lakshmi Pillai",   "Gynaecology",      "MBBS, MS (Obs & Gynae)",          17, 750, "203"),
    ("Imran Sheikh",     "Urology",          "MBBS, MS, MCh (Urology)",         12, 900, "405"),
    ("Ananya Bose",      "General Medicine", "MBBS, MD (General Medicine)",      6, 400, "101"),
    ("Harish Kulkarni",  "General Medicine", "MBBS, MD (General Medicine)",     22, 500, "102"),
]

PATIENTS = [
    ("Ravi Kumar",      "ravi.kumar@example.com",      "Male",   "O+",  1991, "Penicillin", "Type 2 diabetes"),
    ("Sneha Joshi",     "sneha.joshi@example.com",     "Female", "A+",  1996, None, None),
    ("Mohammed Ali",    "mohammed.ali@example.com",    "Male",   "B+",  1978, "Sulfa drugs", "Hypertension"),
    ("Deepa Reddy",     "deepa.reddy@example.com",     "Female", "AB+", 1988, None, "Asthma"),
    ("Karan Malhotra",  "karan.malhotra@example.com",  "Male",   "O-",  2001, None, None),
    ("Fatima Khan",     "fatima.khan@example.com",     "Female", "A-",  1969, "Aspirin", "Hypothyroidism"),
    ("Arjun Pillai",    "arjun.pillai@example.com",    "Male",   "B-",  1994, None, None),
    ("Ritu Agarwal",    "ritu.agarwal@example.com",    "Female", "O+",  1983, "Latex", None),
    ("Vijay Chandran",  "vijay.chandran@example.com",  "Male",   "AB-", 1957, None, "Coronary artery disease"),
    ("Pooja Shetty",    "pooja.shetty@example.com",    "Female", "A+",  1999, None, None),
    ("Nikhil Saxena",   "nikhil.saxena@example.com",   "Male",   "O+",  1986, None, "Migraine"),
    ("Ayesha Siddiqui", "ayesha.siddiqui@example.com", "Female", "B+",  1992, "Dust mites", None),
    ("Gopal Krishnan",  "gopal.krishnan@example.com",  "Male",   "A+",  1949, None, "Arthritis, hypertension"),
    ("Tanvi Deshmukh",  "tanvi.deshmukh@example.com",  "Female", "O-",  2004, None, None),
]

SYMPTOM_SAMPLES = [
    ("chest pain when i climb stairs and breathlessness", "Cardiology"),
    ("itchy red rash on my arms that will not clear", "Dermatology"),
    ("severe lower back pain radiating down my leg", "Orthopaedics"),
    ("throbbing headache for three days with light sensitivity", "Neurology"),
    ("acidity and burning in my chest after every meal", "Gastroenterology"),
    ("sore throat and ear pain for a week", "ENT"),
    ("blurred vision when reading and eye strain", "Ophthalmology"),
    ("persistent dry cough and wheezing at night", "Pulmonology"),
    ("my child has high fever and is not eating", "Paediatrics"),
    ("feeling low and unable to sleep for weeks", "Psychiatry"),
    ("irregular periods and severe cramps", "Gynaecology"),
    ("burning sensation while urinating and frequency", "Urology"),
    ("fever with body ache and weakness for four days", "General Medicine"),
]

DIAGNOSES = {
    "Cardiology": ("Stable angina", [("Aspirin 75mg", "1 tablet", "Once daily", "30 days", "After breakfast"),
                                     ("Atorvastatin 20mg", "1 tablet", "At night", "30 days", "After dinner")]),
    "Dermatology": ("Atopic dermatitis", [("Cetirizine 10mg", "1 tablet", "At night", "7 days", "May cause drowsiness"),
                                          ("Mometasone cream", "Thin layer", "Twice daily", "14 days", "Apply to affected area")]),
    "Orthopaedics": ("Lumbar disc prolapse", [("Aceclofenac 100mg", "1 tablet", "Twice daily", "5 days", "After food"),
                                              ("Methylcobalamin", "1 tablet", "Once daily", "30 days", "")]),
    "Neurology": ("Migraine without aura", [("Sumatriptan 50mg", "1 tablet", "As needed", "PRN", "At onset of headache"),
                                            ("Propranolol 20mg", "1 tablet", "Twice daily", "30 days", "")]),
    "Gastroenterology": ("Gastro-oesophageal reflux", [("Pantoprazole 40mg", "1 tablet", "Once daily", "14 days", "30 min before breakfast")]),
    "ENT": ("Acute tonsillitis", [("Amoxicillin 500mg", "1 capsule", "Three times daily", "5 days", "After food"),
                                  ("Paracetamol 500mg", "1 tablet", "As needed", "3 days", "Max 4 per day")]),
    "Ophthalmology": ("Refractive error", [("Carboxymethylcellulose drops", "1 drop", "Four times daily", "30 days", "Both eyes")]),
    "Pulmonology": ("Mild persistent asthma", [("Budesonide inhaler", "2 puffs", "Twice daily", "30 days", "Rinse mouth after use")]),
    "Paediatrics": ("Acute otitis media", [("Amoxicillin syrup", "5 ml", "Three times daily", "5 days", "After food"),
                                           ("Paracetamol syrup", "5 ml", "As needed", "3 days", "For fever above 38C")]),
    "Psychiatry": ("Moderate depressive episode", [("Sertraline 50mg", "1 tablet", "Once daily", "30 days", "In the morning")]),
    "Gynaecology": ("Polycystic ovary syndrome", [("Metformin 500mg", "1 tablet", "Twice daily", "30 days", "After food")]),
    "Urology": ("Lower urinary tract infection", [("Nitrofurantoin 100mg", "1 capsule", "Twice daily", "5 days", "After food")]),
    "General Medicine": ("Viral fever", [("Paracetamol 650mg", "1 tablet", "Three times daily", "3 days", "After food"),
                                         ("ORS solution", "1 sachet", "Twice daily", "3 days", "Dissolve in 1L water")]),
}

REVIEW_COMMENTS = [
    "Very patient and explained everything clearly.",
    "Short wait and a thorough examination. Recommended.",
    "Good consultation, though the clinic was running late.",
    "Listened carefully and did not rush me at all.",
    "Professional and reassuring. The treatment worked.",
    "Helpful advice, would visit again.",
    None, None, None,
]


def seed_database():
    """Populate the database. Returns a list of summary lines for the CLI."""
    summary = []

    # ---- departments -------------------------------------------------
    departments = {}
    for name, icon, description in DEPARTMENTS:
        department = Department.query.filter_by(name=name).first()
        if not department:
            department = Department(
                name=name, slug=slugify(name), description=description, icon=icon
            )
            db.session.add(department)
        departments[name] = department
    db.session.flush()
    summary.append(f"{len(departments)} departments")

    # ---- knowledge base ----------------------------------------------
    if SymptomKB.query.count() == 0:
        for dept_name, phrase, weight, is_red_flag, advice in SYMPTOM_KB:
            db.session.add(SymptomKB(
                department_id=departments[dept_name].id,
                phrase=phrase, weight=weight, is_red_flag=is_red_flag, advice=advice,
            ))
        summary.append(f"{len(SYMPTOM_KB)} knowledge-base rules")
    db.session.flush()

    # ---- admin --------------------------------------------------------
    if not User.query.filter_by(email="admin@mediqueue.local").first():
        admin = User(name="Hospital Administrator", email="admin@mediqueue.local",
                     role=Role.ADMIN, phone="+91 98000 00000")
        admin.set_password("admin123")
        db.session.add(admin)
        summary.append("1 admin (admin@mediqueue.local / admin123)")
    db.session.flush()

    # ---- doctors and their weekly sittings ----------------------------
    doctors = []
    for name, dept_name, qualification, years, fee, room in DOCTORS:
        email = name.lower().replace(" ", ".") + "@mediqueue.local"
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(name=name, email=email, role=Role.DOCTOR,
                        phone=f"+91 98{RNG.randint(100000000, 999999999)}"[:16])
            user.set_password("doctor123")
            db.session.add(user)
            db.session.flush()

            doctor = Doctor(
                user_id=user.id, department_id=departments[dept_name].id,
                qualification=qualification, experience_years=years,
                consultation_fee=fee, room_no=room,
                bio=f"{name} is a {dept_name.lower()} specialist with {years} years of "
                    f"clinical experience, with a particular interest in patient education "
                    f"and preventive care.",
            )
            db.session.add(doctor)
            db.session.flush()

            # Every doctor sits every day of the week, including Sunday. Not
            # very realistic, but it means the demo always has clinics running
            # and bookable slots whatever day it is run on. A real hospital
            # would set these per doctor through the admin screens.
            for weekday in range(0, 7):
                morning = DoctorSchedule(
                    doctor_id=doctor.id, weekday=weekday,
                    start_time=time(9, 0), end_time=time(13, 0),
                    slot_minutes=15, max_tokens=16,
                )
                db.session.add(morning)
                if weekday % 2 == 0:
                    db.session.add(DoctorSchedule(
                        doctor_id=doctor.id, weekday=weekday,
                        start_time=time(15, 0), end_time=time(18, 0),
                        slot_minutes=20, max_tokens=9,
                    ))
        else:
            doctor = user.doctor
        doctors.append(doctor)
    db.session.flush()
    summary.append(f"{len(doctors)} doctors (password: doctor123)")

    # one doctor on leave next week, so the leave path is visible in the demo
    if not ScheduleException.query.count():
        db.session.add(ScheduleException(
            doctor_id=doctors[0].id, date=date.today() + timedelta(days=5),
            is_full_day=True, reason="Conference leave",
        ))
    db.session.flush()

    # ---- patients ------------------------------------------------------
    patients = []
    for name, email, gender, blood, birth_year, allergies, chronic in PATIENTS:
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(name=name, email=email, role=Role.PATIENT,
                        phone=f"+91 97{RNG.randint(100000000, 999999999)}"[:16])
            user.set_password("patient123")
            db.session.add(user)
            db.session.flush()
            db.session.add(PatientProfile(
                user_id=user.id, gender=gender, blood_group=blood,
                date_of_birth=date(birth_year, RNG.randint(1, 12), RNG.randint(1, 28)),
                allergies=allergies, chronic_conditions=chronic,
                address=f"{RNG.randint(1, 200)} MG Road, Bengaluru 560001",
                emergency_contact=f"+91 96{RNG.randint(100000000, 999999999)}"[:16],
            ))
        patients.append(user)
    db.session.flush()
    summary.append(f"{len(patients)} patients (password: patient123)")

    if Appointment.query.count():
        db.session.commit()
        summary.append("appointments already present, left untouched")
        return summary

    # ---- four weeks of history -----------------------------------------
    created = 0
    completed = 0
    for days_ago in range(28, 0, -1):
        day = date.today() - timedelta(days=days_ago)
        # Weekends are quieter, which makes the daily chart look like a real one.
        volume = RNG.randint(2, 4) if day.weekday() >= 5 else RNG.randint(5, 11)

        for _ in range(volume):
            doctor = RNG.choice(doctors)
            schedule = next(
                (s for s in doctor.schedules if s.weekday == day.weekday()), None
            )
            if not schedule:
                continue

            session = ClinicSession.query.filter_by(
                doctor_id=doctor.id, date=day, start_time=schedule.start_time
            ).first()
            if not session:
                session = ClinicSession(
                    doctor_id=doctor.id, schedule_id=schedule.id, date=day,
                    start_time=schedule.start_time, end_time=schedule.end_time,
                    slot_minutes=schedule.slot_minutes, max_tokens=schedule.max_tokens,
                    avg_service_seconds=schedule.slot_minutes * 60,
                    status=SessionStatus.CLOSED,
                    closed_at=datetime.combine(day, schedule.end_time),
                )
                db.session.add(session)
                db.session.flush()

            token = next_token(session)
            if token > session.max_tokens:
                continue

            slot_start = datetime.combine(day, schedule.start_time) + timedelta(
                minutes=schedule.slot_minutes * (token - 1)
            )
            symptoms, specialty = RNG.choice(SYMPTOM_SAMPLES)

            # Roughly one in eight past appointments was a no-show, which gives
            # the no-show report a believable number to display.
            outcome = ApptStatus.NO_SHOW if RNG.random() < 0.12 else ApptStatus.COMPLETED

            appointment = Appointment(
                patient_id=RNG.choice(patients).id,
                doctor_id=doctor.id,
                session_id=session.id,
                appt_date=day,
                slot_start=slot_start,
                slot_end=slot_start + timedelta(minutes=schedule.slot_minutes),
                token_number=token,
                status=outcome,
                symptoms_text=symptoms,
                triage_specialty=specialty,
                triage_confidence=round(RNG.uniform(0.55, 0.95), 2),
                booked_via=RNG.choice(["web", "web", "chatbot"]),
                created_at=datetime.combine(day, time(8, 0)) - timedelta(days=RNG.randint(1, 6)),
            )

            if outcome == ApptStatus.COMPLETED:
                minutes = RNG.randint(8, 22)
                appointment.checked_in_at = slot_start - timedelta(minutes=RNG.randint(5, 20))
                appointment.called_at = slot_start + timedelta(minutes=RNG.randint(0, 12))
                appointment.completed_at = appointment.called_at + timedelta(minutes=minutes)
                session.served_count += 1
                session.current_token = token
                completed += 1

            db.session.add(appointment)
            db.session.flush()
            created += 1

            if outcome == ApptStatus.COMPLETED:
                diagnosis, medicines = DIAGNOSES.get(
                    doctor.department.name, DIAGNOSES["General Medicine"]
                )
                consultation = Consultation(
                    appointment_id=appointment.id,
                    diagnosis=diagnosis,
                    notes="Patient examined. Vitals stable. Symptoms reviewed and "
                          "investigations discussed. Treatment plan explained.",
                    advice="Take medicines as prescribed. Return sooner if symptoms worsen.",
                    follow_up_date=day + timedelta(days=RNG.choice([7, 14, 30]))
                        if RNG.random() < 0.4 else None,
                    created_at=appointment.completed_at,
                )
                db.session.add(consultation)
                db.session.flush()
                for drug, dosage, frequency, duration, instructions in medicines:
                    db.session.add(PrescriptionItem(
                        consultation_id=consultation.id, drug=drug, dosage=dosage,
                        frequency=frequency, duration=duration,
                        instructions=instructions or None,
                    ))

                # About 55% of completed visits get rated -- a realistic response rate.
                if RNG.random() < 0.55:
                    db.session.add(Rating(
                        appointment_id=appointment.id,
                        doctor_id=doctor.id,
                        patient_id=appointment.patient_id,
                        stars=RNG.choices([5, 4, 3, 2], weights=[52, 30, 13, 5])[0],
                        comment=RNG.choice(REVIEW_COMMENTS),
                        created_at=appointment.completed_at + timedelta(hours=RNG.randint(1, 48)),
                    ))

    db.session.flush()
    summary.append(f"{created} past appointments ({completed} completed)")

    # ---- today: live clinics, one part-way through -----------------------
    today = date.today()
    live_created = 0
    for index, doctor in enumerate(doctors[:6]):
        schedule = next((s for s in doctor.schedules if s.weekday == today.weekday()), None)
        if not schedule:
            continue

        session = ClinicSession.query.filter_by(
            doctor_id=doctor.id, date=today, start_time=schedule.start_time
        ).first()
        if not session:
            session = ClinicSession(
                doctor_id=doctor.id, schedule_id=schedule.id, date=today,
                start_time=schedule.start_time, end_time=schedule.end_time,
                slot_minutes=schedule.slot_minutes, max_tokens=schedule.max_tokens,
                avg_service_seconds=schedule.slot_minutes * 60,
            )
            db.session.add(session)
            db.session.flush()

        count = RNG.randint(4, 7)
        # The first two clinics are already running, so the live board and the
        # "now serving" figures have something to show immediately.
        running = index < 2
        served_upto = 2 if running else 0

        if running:
            session.status = SessionStatus.IN_PROGRESS
            session.started_at = datetime.combine(today, schedule.start_time)

        for token in range(1, count + 1):
            slot_start = datetime.combine(today, schedule.start_time) + timedelta(
                minutes=schedule.slot_minutes * (token - 1)
            )
            symptoms, specialty = RNG.choice(SYMPTOM_SAMPLES)

            if running and token < served_upto:
                status = ApptStatus.COMPLETED
            elif running and token == served_upto:
                status = ApptStatus.IN_CONSULT
            elif running and token == served_upto + 1:
                status = ApptStatus.CHECKED_IN
            else:
                status = ApptStatus.BOOKED

            appointment = Appointment(
                patient_id=patients[(index * 3 + token) % len(patients)].id,
                doctor_id=doctor.id,
                session_id=session.id,
                appt_date=today,
                slot_start=slot_start,
                slot_end=slot_start + timedelta(minutes=schedule.slot_minutes),
                token_number=token,
                status=status,
                symptoms_text=symptoms,
                triage_specialty=specialty,
                triage_confidence=round(RNG.uniform(0.55, 0.95), 2),
                booked_via=RNG.choice(["web", "chatbot"]),
            )
            if status in (ApptStatus.COMPLETED, ApptStatus.IN_CONSULT, ApptStatus.CHECKED_IN):
                appointment.checked_in_at = datetime.now() - timedelta(minutes=RNG.randint(10, 60))
            if status in (ApptStatus.COMPLETED, ApptStatus.IN_CONSULT):
                appointment.called_at = datetime.now() - timedelta(minutes=RNG.randint(5, 40))
                session.current_token = token
            if status == ApptStatus.COMPLETED:
                appointment.completed_at = appointment.called_at + timedelta(minutes=12)
                session.served_count += 1

            db.session.add(appointment)
            db.session.flush()
            live_created += 1

            if status == ApptStatus.COMPLETED:
                diagnosis, medicines = DIAGNOSES.get(
                    doctor.department.name, DIAGNOSES["General Medicine"]
                )
                consultation = Consultation(
                    appointment_id=appointment.id, diagnosis=diagnosis,
                    notes="Examined today. Treatment plan discussed with the patient.",
                    advice="Complete the full course of medication.",
                )
                db.session.add(consultation)
                db.session.flush()
                for drug, dosage, frequency, duration, instructions in medicines:
                    db.session.add(PrescriptionItem(
                        consultation_id=consultation.id, drug=drug, dosage=dosage,
                        frequency=frequency, duration=duration,
                        instructions=instructions or None,
                    ))

    summary.append(f"{live_created} appointments today (2 clinics already running)")

    # ---- chatbot transcripts, so the AI usage report is not empty ---------
    for index in range(40):
        symptoms, specialty = RNG.choice(SYMPTOM_SAMPLES)
        moment = datetime.now() - timedelta(days=RNG.randint(0, 20), hours=RNG.randint(0, 23))
        key = f"seed-session-{index // 2}"
        db.session.add(ChatMessage(
            session_key=key, sender="user", text=symptoms,
            user_id=RNG.choice(patients).id if RNG.random() < 0.6 else None,
            predicted_specialty=specialty,
            confidence=round(RNG.uniform(0.5, 0.95), 2),
            created_at=moment,
        ))
        db.session.add(ChatMessage(
            session_key=key, sender="bot",
            text=f"Based on what you've described, **{specialty}** looks like the right department.",
            created_at=moment,
        ))
    summary.append("40 chatbot conversations")

    # ---- roll up the ratings we just created ------------------------------
    from hospital.services import ratings as rating_service
    for doctor in doctors:
        rating_service.recalculate(doctor)

    db.session.commit()
    return summary
