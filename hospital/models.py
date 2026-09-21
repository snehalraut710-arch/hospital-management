"""SQLAlchemy models.

Design notes worth remembering:

* One `User` table carries all three roles; doctor- and patient-specific fields
  live in satellite tables so the login path stays identical for everyone.
* `ClinicSession` models one doctor's one sitting on one date and owns the queue
  state, so "now serving #4" is a single row read.
* Double-booking is prevented by unique indexes, not by application checks.
* Timestamps are local naive datetimes. The rest of the application compares
  them against `datetime.now()` (slot times, queue timings), so storing some
  columns in UTC would make those comparisons silently wrong and would display
  notification times in the wrong zone.
"""
from datetime import date, datetime, time, timedelta

from flask_login import UserMixin
from sqlalchemy import Index, UniqueConstraint
from werkzeug.security import check_password_hash, generate_password_hash

from hospital.extensions import db, login_manager


# --------------------------------------------------------------------------
# Enumerations (plain string constants: SQLite stores them as TEXT, and they
# stay readable when the examiner opens the .db file in DB Browser)
# --------------------------------------------------------------------------
class Role:
    ADMIN = "admin"
    DOCTOR = "doctor"
    PATIENT = "patient"
    ALL = (ADMIN, DOCTOR, PATIENT)


class ApptStatus:
    BOOKED = "booked"
    CHECKED_IN = "checked_in"
    IN_CONSULT = "in_consult"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"

    #: statuses that still occupy a slot / a place in the queue
    LIVE = (BOOKED, CHECKED_IN, IN_CONSULT)
    #: statuses the patient can no longer act on
    CLOSED = (COMPLETED, CANCELLED, NO_SHOW)

    LABELS = {
        BOOKED: "Booked",
        CHECKED_IN: "Checked in",
        IN_CONSULT: "In consultation",
        COMPLETED: "Completed",
        CANCELLED: "Cancelled",
        NO_SHOW: "No show",
    }


class SessionStatus:
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"


# --------------------------------------------------------------------------
# People
# --------------------------------------------------------------------------
class User(UserMixin, db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(20))
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(16), nullable=False, default=Role.PATIENT, index=True)
    is_active_flag = db.Column("is_active", db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    patient_profile = db.relationship(
        "PatientProfile", back_populates="user", uselist=False,
        cascade="all, delete-orphan",
    )
    doctor = db.relationship(
        "Doctor", back_populates="user", uselist=False,
        cascade="all, delete-orphan",
    )
    notifications = db.relationship(
        "Notification", back_populates="user", cascade="all, delete-orphan",
        order_by="Notification.created_at.desc()",
    )

    # -- password handling ---------------------------------------------
    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    # Flask-Login consults `is_active` to allow a session; we deactivate
    # users rather than deleting them so their appointment history survives.
    @property
    def is_active(self) -> bool:  # type: ignore[override]
        return bool(self.is_active_flag)

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def is_doctor(self) -> bool:
        return self.role == Role.DOCTOR

    @property
    def is_patient(self) -> bool:
        return self.role == Role.PATIENT

    @property
    def initials(self) -> str:
        parts = [p for p in self.name.split() if p]
        return "".join(p[0] for p in parts[:2]).upper() or "?"

    def __repr__(self) -> str:
        return f"<User {self.email} ({self.role})>"


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


class PatientProfile(db.Model):
    """Medical background shown to the doctor when the patient's token is called."""
    __tablename__ = "patient_profile"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    date_of_birth = db.Column(db.Date)
    gender = db.Column(db.String(20))
    blood_group = db.Column(db.String(8))
    allergies = db.Column(db.Text)
    chronic_conditions = db.Column(db.Text)
    address = db.Column(db.Text)
    emergency_contact = db.Column(db.String(120))

    user = db.relationship("User", back_populates="patient_profile")

    @property
    def age(self):
        if not self.date_of_birth:
            return None
        today = date.today()
        years = today.year - self.date_of_birth.year
        if (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day):
            years -= 1
        return years


# --------------------------------------------------------------------------
# Clinical structure
# --------------------------------------------------------------------------
class Department(db.Model):
    __tablename__ = "department"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    slug = db.Column(db.String(80), unique=True, nullable=False, index=True)
    description = db.Column(db.Text)
    icon = db.Column(db.String(16), default="+")
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    doctors = db.relationship("Doctor", back_populates="department")
    symptoms = db.relationship(
        "SymptomKB", back_populates="department", cascade="all, delete-orphan"
    )

    @property
    def active_doctors(self):
        return [d for d in self.doctors if d.is_active and d.user.is_active]

    def __repr__(self) -> str:
        return f"<Department {self.name}>"


class Doctor(db.Model):
    __tablename__ = "doctor"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("department.id"), nullable=False)
    qualification = db.Column(db.String(160))
    experience_years = db.Column(db.Integer, default=0)
    consultation_fee = db.Column(db.Float, default=0.0)
    room_no = db.Column(db.String(16))
    bio = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    # Denormalised rating roll-up, recomputed whenever a rating is written.
    # Storing it avoids an aggregate query on every doctor-list render.
    avg_rating = db.Column(db.Float, default=0.0, nullable=False)
    rating_count = db.Column(db.Integer, default=0, nullable=False)

    user = db.relationship("User", back_populates="doctor")
    department = db.relationship("Department", back_populates="doctors")
    schedules = db.relationship(
        "DoctorSchedule", back_populates="doctor", cascade="all, delete-orphan"
    )
    exceptions = db.relationship(
        "ScheduleException", back_populates="doctor", cascade="all, delete-orphan"
    )
    sessions = db.relationship("ClinicSession", back_populates="doctor")
    appointments = db.relationship("Appointment", back_populates="doctor")

    @property
    def name(self) -> str:
        return self.user.name

    @property
    def display_name(self) -> str:
        return self.user.name if self.user.name.startswith("Dr") else f"Dr. {self.user.name}"

    def __repr__(self) -> str:
        return f"<Doctor {self.display_name}>"


class DoctorSchedule(db.Model):
    """A recurring weekly sitting, e.g. 'Mondays 10:00-13:00, 15 min slots'."""
    __tablename__ = "doctor_schedule"

    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctor.id"), nullable=False)
    weekday = db.Column(db.Integer, nullable=False)  # 0 = Monday .. 6 = Sunday
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    slot_minutes = db.Column(db.Integer, default=15, nullable=False)
    max_tokens = db.Column(db.Integer, default=20, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    doctor = db.relationship("Doctor", back_populates="schedules")

    __table_args__ = (
        UniqueConstraint("doctor_id", "weekday", "start_time", name="uq_schedule_slot"),
    )

    WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

    @property
    def weekday_name(self) -> str:
        return self.WEEKDAYS[self.weekday]

    @property
    def capacity(self) -> int:
        """How many slots this sitting yields, capped by max_tokens."""
        start = datetime.combine(date.min, self.start_time)
        end = datetime.combine(date.min, self.end_time)
        minutes = int((end - start).total_seconds() // 60)
        return min(max(minutes // self.slot_minutes, 0), self.max_tokens)


class ScheduleException(db.Model):
    """Leave or a holiday: punches a hole in the recurring pattern."""
    __tablename__ = "schedule_exception"

    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctor.id"), nullable=False)
    date = db.Column(db.Date, nullable=False, index=True)
    is_full_day = db.Column(db.Boolean, default=True, nullable=False)
    start_time = db.Column(db.Time)
    end_time = db.Column(db.Time)
    reason = db.Column(db.String(160))

    doctor = db.relationship("Doctor", back_populates="exceptions")

    def blocks(self, slot_start: time, slot_end: time) -> bool:
        """Does this exception cover the given slot?"""
        if self.is_full_day:
            return True
        if not self.start_time or not self.end_time:
            return True
        return slot_start < self.end_time and slot_end > self.start_time


# --------------------------------------------------------------------------
# Queue
# --------------------------------------------------------------------------
class ClinicSession(db.Model):
    """One doctor, one date, one sitting -- and the live queue state for it."""
    __tablename__ = "clinic_session"

    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctor.id"), nullable=False)
    schedule_id = db.Column(db.Integer, db.ForeignKey("doctor_schedule.id"))
    date = db.Column(db.Date, nullable=False, index=True)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    slot_minutes = db.Column(db.Integer, default=15, nullable=False)
    max_tokens = db.Column(db.Integer, default=20, nullable=False)

    status = db.Column(db.String(20), default=SessionStatus.NOT_STARTED, nullable=False)
    current_token = db.Column(db.Integer, default=0, nullable=False)
    served_count = db.Column(db.Integer, default=0, nullable=False)
    #: rolling average consultation length, seeded from the slot length
    avg_service_seconds = db.Column(db.Integer, default=900, nullable=False)
    started_at = db.Column(db.DateTime)
    closed_at = db.Column(db.DateTime)

    doctor = db.relationship("Doctor", back_populates="sessions")
    appointments = db.relationship(
        "Appointment", back_populates="session", order_by="Appointment.token_number"
    )

    __table_args__ = (
        UniqueConstraint("doctor_id", "date", "start_time", name="uq_session_sitting"),
    )

    @property
    def live_appointments(self):
        return [a for a in self.appointments if a.status in ApptStatus.LIVE]

    @property
    def waiting_count(self) -> int:
        return len([a for a in self.appointments if a.status in (ApptStatus.BOOKED, ApptStatus.CHECKED_IN)])

    @property
    def booked_count(self) -> int:
        return len([a for a in self.appointments if a.status != ApptStatus.CANCELLED])

    @property
    def seats_left(self) -> int:
        return max(self.max_tokens - self.booked_count, 0)

    @property
    def now_serving(self):
        for a in self.appointments:
            if a.status == ApptStatus.IN_CONSULT:
                return a
        return None

    def __repr__(self) -> str:
        return f"<ClinicSession doctor={self.doctor_id} {self.date} {self.start_time}>"


class Appointment(db.Model):
    __tablename__ = "appointment"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctor.id"), nullable=False, index=True)
    session_id = db.Column(db.Integer, db.ForeignKey("clinic_session.id"), nullable=False)

    appt_date = db.Column(db.Date, nullable=False, index=True)
    slot_start = db.Column(db.DateTime, nullable=False)
    slot_end = db.Column(db.DateTime, nullable=False)
    token_number = db.Column(db.Integer, nullable=False)

    status = db.Column(db.String(20), default=ApptStatus.BOOKED, nullable=False, index=True)
    symptoms_text = db.Column(db.Text)
    triage_specialty = db.Column(db.String(80))
    triage_confidence = db.Column(db.Float)
    booked_via = db.Column(db.String(20), default="web")  # web | chatbot | admin

    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    checked_in_at = db.Column(db.DateTime)
    called_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    cancel_reason = db.Column(db.String(200))

    patient = db.relationship("User", foreign_keys=[patient_id])
    doctor = db.relationship("Doctor", back_populates="appointments")
    session = db.relationship("ClinicSession", back_populates="appointments")
    consultation = db.relationship(
        "Consultation", back_populates="appointment", uselist=False,
        cascade="all, delete-orphan",
    )
    rating = db.relationship(
        "Rating", back_populates="appointment", uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # One token number per session. The database is the authority.
        UniqueConstraint("session_id", "token_number", name="uq_session_token"),
        # A doctor cannot hold two live appointments in the same slot. Partial
        # index: cancelled/no-show rows are excluded so the slot frees up.
        Index(
            "uq_doctor_slot_live",
            "doctor_id", "slot_start",
            unique=True,
            sqlite_where=db.text(
                "status IN ('booked','checked_in','in_consult')"
            ),
        ),
    )

    @property
    def status_label(self) -> str:
        return ApptStatus.LABELS.get(self.status, self.status)

    @property
    def is_live(self) -> bool:
        return self.status in ApptStatus.LIVE

    @property
    def can_cancel(self) -> bool:
        return self.status in (ApptStatus.BOOKED, ApptStatus.CHECKED_IN)

    @property
    def can_rate(self) -> bool:
        return self.status == ApptStatus.COMPLETED and self.rating is None

    def __repr__(self) -> str:
        return f"<Appointment #{self.id} token={self.token_number} {self.status}>"


# --------------------------------------------------------------------------
# Clinical record
# --------------------------------------------------------------------------
class Consultation(db.Model):
    __tablename__ = "consultation"

    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(
        db.Integer, db.ForeignKey("appointment.id"), unique=True, nullable=False
    )
    diagnosis = db.Column(db.String(300))
    notes = db.Column(db.Text)
    advice = db.Column(db.Text)
    follow_up_date = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    appointment = db.relationship("Appointment", back_populates="consultation")
    prescription_items = db.relationship(
        "PrescriptionItem", back_populates="consultation",
        cascade="all, delete-orphan",
    )


class PrescriptionItem(db.Model):
    """One line of a prescription. Line items rather than a text blob, so the
    prescription renders as a real table and could be queried later."""
    __tablename__ = "prescription_item"

    id = db.Column(db.Integer, primary_key=True)
    consultation_id = db.Column(db.Integer, db.ForeignKey("consultation.id"), nullable=False)
    drug = db.Column(db.String(120), nullable=False)
    dosage = db.Column(db.String(80))
    frequency = db.Column(db.String(80))
    duration = db.Column(db.String(80))
    instructions = db.Column(db.String(200))

    consultation = db.relationship("Consultation", back_populates="prescription_items")


class Rating(db.Model):
    __tablename__ = "rating"

    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(
        db.Integer, db.ForeignKey("appointment.id"), unique=True, nullable=False
    )
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctor.id"), nullable=False, index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    stars = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    appointment = db.relationship("Appointment", back_populates="rating")
    doctor = db.relationship("Doctor")
    patient = db.relationship("User", foreign_keys=[patient_id])


# --------------------------------------------------------------------------
# Messaging / AI
# --------------------------------------------------------------------------
class Notification(db.Model):
    __tablename__ = "notification"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False)
    body = db.Column(db.Text)
    category = db.Column(db.String(30), default="info")
    channel = db.Column(db.String(20), default="in_app")   # in_app | email
    delivery_status = db.Column(db.String(20), default="stored")  # stored | sent | failed
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    user = db.relationship("User", back_populates="notifications")


class ChatMessage(db.Model):
    """Chatbot transcript. Also the source for the admin's 'top symptoms' report."""
    __tablename__ = "chat_message"

    id = db.Column(db.Integer, primary_key=True)
    session_key = db.Column(db.String(64), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))  # null: not signed in
    sender = db.Column(db.String(10), nullable=False)  # user | bot
    text = db.Column(db.Text, nullable=False)
    predicted_specialty = db.Column(db.String(80))
    confidence = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    user = db.relationship("User")


class SymptomKB(db.Model):
    """Curated symptom knowledge base: the deterministic layer that backs, and
    where necessary overrides, the statistical model."""
    __tablename__ = "symptom_kb"

    id = db.Column(db.Integer, primary_key=True)
    department_id = db.Column(db.Integer, db.ForeignKey("department.id"), nullable=False)
    phrase = db.Column(db.String(160), nullable=False, index=True)
    weight = db.Column(db.Float, default=1.0, nullable=False)
    is_red_flag = db.Column(db.Boolean, default=False, nullable=False)
    advice = db.Column(db.String(300))

    department = db.relationship("Department", back_populates="symptoms")

    def __repr__(self) -> str:
        return f"<SymptomKB '{self.phrase}' -> {self.department_id}>"
