"""Administration: staff, schedules, oversight and reporting."""
import re
from datetime import date as Date, datetime, time

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from hospital.extensions import db
from hospital.models import (
    Appointment, ApptStatus, ClinicSession, Department, Doctor, DoctorSchedule,
    PatientProfile, Role, ScheduleException, SymptomKB, User,
)
from hospital.security import admin_required
from hospital.services import ai, booking, notify, queue as queue_service, reports
from hospital.services.errors import ServiceError

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
@login_required
@admin_required
def guard():
    """Administrators only."""


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _time(raw: str, fallback: time) -> time:
    try:
        return datetime.strptime(raw, "%H:%M").time()
    except (ValueError, TypeError):
        return fallback


# ---------------------------------------------------------------------------
# Dashboard and reports
# ---------------------------------------------------------------------------
@bp.route("/")
def dashboard():
    return render_template(
        "admin/dashboard.html",
        stats=reports.headline_counts(),
        per_day=reports.appointments_per_day(14),
        per_department=reports.appointments_per_department(),
        statuses=reports.status_breakdown(),
        no_show=reports.no_show_rate(),
        avg_wait=reports.average_wait_minutes(),
        busiest=reports.busiest_doctors(),
        live_sessions=queue_service.live_sessions(),
    )


@bp.route("/reports")
def reports_page():
    return render_template(
        "admin/reports.html",
        stats=reports.headline_counts(),
        per_day=reports.appointments_per_day(30),
        per_department=reports.appointments_per_department(),
        statuses=reports.status_breakdown(),
        no_show=reports.no_show_rate(),
        avg_wait=reports.average_wait_minutes(),
        busiest=reports.busiest_doctors(10),
        symptoms=reports.top_symptoms(),
        rating=reports.rating_summary(),
        chatbot=reports.chatbot_usage(),
    )


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------
@bp.route("/departments", methods=["GET", "POST"])
def departments():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("A department needs a name.", "danger")
        elif Department.query.filter_by(name=name).first():
            flash("That department already exists.", "warning")
        else:
            db.session.add(Department(
                name=name,
                slug=slugify(name),
                description=(request.form.get("description") or "").strip() or None,
                icon=(request.form.get("icon") or "+").strip()[:4],
            ))
            db.session.commit()
            flash(f"Department '{name}' added.", "success")
        return redirect(url_for("admin.departments"))

    return render_template(
        "admin/departments.html",
        departments=Department.query.order_by(Department.name).all(),
    )


@bp.route("/departments/<int:department_id>/toggle", methods=["POST"])
def toggle_department(department_id):
    department = Department.query.get_or_404(department_id)
    department.is_active = not department.is_active
    db.session.commit()
    flash(
        f"{department.name} is now {'active' if department.is_active else 'inactive'}.",
        "info",
    )
    return redirect(url_for("admin.departments"))


# ---------------------------------------------------------------------------
# Doctors
# ---------------------------------------------------------------------------
@bp.route("/doctors")
def doctors():
    return render_template(
        "admin/doctors.html",
        doctors=Doctor.query.join(User).order_by(User.name).all(),
        departments=Department.query.order_by(Department.name).all(),
    )


@bp.route("/doctors/new", methods=["GET", "POST"])
def new_doctor():
    departments = Department.query.order_by(Department.name).all()

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        department_id = request.form.get("department_id", type=int)

        errors = []
        if len(name) < 2:
            errors.append("Please enter the doctor's name.")
        if "@" not in email:
            errors.append("Please enter a valid email address.")
        if len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if not department_id:
            errors.append("Please choose a department.")
        if User.query.filter_by(email=email).first():
            errors.append("An account with that email already exists.")

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "admin/doctor_form.html", departments=departments,
                doctor=None, form=request.form,
            )

        user = User(name=name, email=email, role=Role.DOCTOR,
                    phone=(request.form.get("phone") or "").strip())
        user.set_password(password)
        db.session.add(user)
        db.session.flush()

        db.session.add(Doctor(
            user_id=user.id,
            department_id=department_id,
            qualification=(request.form.get("qualification") or "").strip() or None,
            experience_years=request.form.get("experience_years", type=int) or 0,
            consultation_fee=request.form.get("consultation_fee", type=float) or 0.0,
            room_no=(request.form.get("room_no") or "").strip() or None,
            bio=(request.form.get("bio") or "").strip() or None,
        ))
        db.session.commit()
        flash(f"Dr. {name} added.", "success")
        return redirect(url_for("admin.doctors"))

    return render_template(
        "admin/doctor_form.html", departments=departments, doctor=None, form={}
    )


@bp.route("/doctors/<int:doctor_id>/edit", methods=["GET", "POST"])
def edit_doctor(doctor_id):
    doctor = Doctor.query.get_or_404(doctor_id)
    departments = Department.query.order_by(Department.name).all()

    if request.method == "POST":
        doctor.user.name = (request.form.get("name") or doctor.user.name).strip()
        doctor.user.phone = (request.form.get("phone") or "").strip()
        doctor.department_id = request.form.get("department_id", type=int) or doctor.department_id
        doctor.qualification = (request.form.get("qualification") or "").strip() or None
        doctor.experience_years = request.form.get("experience_years", type=int) or 0
        doctor.consultation_fee = request.form.get("consultation_fee", type=float) or 0.0
        doctor.room_no = (request.form.get("room_no") or "").strip() or None
        doctor.bio = (request.form.get("bio") or "").strip() or None

        password = request.form.get("password") or ""
        if password:
            if len(password) < 6:
                flash("Password must be at least 6 characters.", "danger")
                return render_template(
                    "admin/doctor_form.html", departments=departments,
                    doctor=doctor, form=request.form,
                )
            doctor.user.set_password(password)

        db.session.commit()
        flash("Doctor updated.", "success")
        return redirect(url_for("admin.doctors"))

    return render_template(
        "admin/doctor_form.html", departments=departments, doctor=doctor, form={}
    )


@bp.route("/doctors/<int:doctor_id>/toggle", methods=["POST"])
def toggle_doctor(doctor_id):
    """Deactivate rather than delete, so appointment history survives."""
    doctor = Doctor.query.get_or_404(doctor_id)
    doctor.is_active = not doctor.is_active
    doctor.user.is_active_flag = doctor.is_active
    db.session.commit()
    flash(
        f"{doctor.display_name} is now {'active' if doctor.is_active else 'inactive'}.",
        "info",
    )
    return redirect(url_for("admin.doctors"))


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------
@bp.route("/doctors/<int:doctor_id>/schedule", methods=["GET", "POST"])
def doctor_schedule(doctor_id):
    doctor = Doctor.query.get_or_404(doctor_id)

    if request.method == "POST":
        weekday = request.form.get("weekday", type=int)
        start = _time(request.form.get("start_time"), time(9, 0))
        end = _time(request.form.get("end_time"), time(13, 0))

        if weekday is None or not 0 <= weekday <= 6:
            flash("Please choose a weekday.", "danger")
        elif start >= end:
            flash("The finish time must be after the start time.", "danger")
        elif DoctorSchedule.query.filter_by(
            doctor_id=doctor.id, weekday=weekday, start_time=start
        ).first():
            flash("That sitting already exists.", "warning")
        else:
            db.session.add(DoctorSchedule(
                doctor_id=doctor.id, weekday=weekday,
                start_time=start, end_time=end,
                slot_minutes=request.form.get("slot_minutes", type=int) or 15,
                max_tokens=request.form.get("max_tokens", type=int) or 20,
            ))
            db.session.commit()
            flash("Sitting added.", "success")
        return redirect(url_for("admin.doctor_schedule", doctor_id=doctor.id))

    return render_template(
        "admin/schedule.html",
        doctor=doctor,
        schedules=sorted(doctor.schedules, key=lambda s: (s.weekday, s.start_time)),
        exceptions=sorted(
            (e for e in doctor.exceptions if e.date >= Date.today()), key=lambda e: e.date
        ),
        weekdays=DoctorSchedule.WEEKDAYS,
    )


@bp.route("/schedule/<int:schedule_id>/delete", methods=["POST"])
def delete_schedule(schedule_id):
    schedule = DoctorSchedule.query.get_or_404(schedule_id)
    doctor_id = schedule.doctor_id
    db.session.delete(schedule)
    db.session.commit()
    flash("Sitting removed. Existing appointments are unaffected.", "info")
    return redirect(url_for("admin.doctor_schedule", doctor_id=doctor_id))


@bp.route("/doctors/<int:doctor_id>/leave", methods=["POST"])
def add_leave(doctor_id):
    doctor = Doctor.query.get_or_404(doctor_id)
    raw = (request.form.get("date") or "").strip()
    try:
        day = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        flash("Please choose a valid date.", "danger")
        return redirect(url_for("admin.doctor_schedule", doctor_id=doctor.id))

    db.session.add(ScheduleException(
        doctor_id=doctor.id, date=day, is_full_day=True,
        reason=(request.form.get("reason") or "Leave").strip(),
    ))
    db.session.commit()

    # Existing bookings on that day are not silently dropped -- the patients are
    # told, and an administrator can rebook them.
    affected = Appointment.query.filter(
        Appointment.doctor_id == doctor.id,
        Appointment.appt_date == day,
        Appointment.status.in_(ApptStatus.LIVE),
    ).all()
    for appointment in affected:
        notify.notify(
            appointment.patient,
            "Your appointment needs rebooking",
            f"{doctor.display_name} is unavailable on {day:%d %b %Y}. "
            "Please book another time.",
            category="warning",
        )

    flash(
        f"Leave recorded for {day:%d %b %Y}."
        + (f" {len(affected)} patient(s) notified." if affected else ""),
        "success",
    )
    return redirect(url_for("admin.doctor_schedule", doctor_id=doctor.id))


@bp.route("/leave/<int:exception_id>/delete", methods=["POST"])
def delete_leave(exception_id):
    exception = ScheduleException.query.get_or_404(exception_id)
    doctor_id = exception.doctor_id
    db.session.delete(exception)
    db.session.commit()
    flash("Leave removed.", "info")
    return redirect(url_for("admin.doctor_schedule", doctor_id=doctor_id))


# ---------------------------------------------------------------------------
# Patients and appointments
# ---------------------------------------------------------------------------
@bp.route("/patients")
def patients():
    search = (request.args.get("q") or "").strip()
    query = User.query.filter_by(role=Role.PATIENT)
    if search:
        query = query.filter(
            db.or_(User.name.ilike(f"%{search}%"), User.email.ilike(f"%{search}%"))
        )
    page = request.args.get("page", 1, type=int)
    return render_template(
        "admin/patients.html",
        pagination=query.order_by(User.name).paginate(page=page, per_page=25, error_out=False),
        search=search,
    )


@bp.route("/patients/<int:user_id>")
def patient_detail(user_id):
    patient = User.query.get_or_404(user_id)
    if not patient.is_patient:
        abort(404)
    return render_template(
        "admin/patient_detail.html",
        patient=patient,
        profile=patient.patient_profile,
        appointments=Appointment.query.filter_by(patient_id=patient.id)
                                      .order_by(Appointment.slot_start.desc()).all(),
    )


@bp.route("/patients/<int:user_id>/toggle", methods=["POST"])
def toggle_patient(user_id):
    patient = User.query.get_or_404(user_id)
    patient.is_active_flag = not patient.is_active_flag
    db.session.commit()
    flash(
        f"{patient.name} is now {'active' if patient.is_active_flag else 'deactivated'}.",
        "info",
    )
    return redirect(url_for("admin.patient_detail", user_id=patient.id))


@bp.route("/appointments")
def appointments():
    query = Appointment.query
    status = request.args.get("status")
    if status in ApptStatus.LABELS:
        query = query.filter(Appointment.status == status)

    day_raw = request.args.get("date")
    if day_raw:
        try:
            query = query.filter(Appointment.appt_date == datetime.strptime(day_raw, "%Y-%m-%d").date())
        except ValueError:
            flash("That date was not understood.", "warning")

    department_id = request.args.get("department", type=int)
    if department_id:
        query = query.join(Doctor).filter(Doctor.department_id == department_id)

    page = request.args.get("page", 1, type=int)
    return render_template(
        "admin/appointments.html",
        pagination=query.order_by(Appointment.slot_start.desc())
                        .paginate(page=page, per_page=25, error_out=False),
        departments=Department.query.order_by(Department.name).all(),
        statuses=ApptStatus.LABELS,
        selected={"status": status, "date": day_raw, "department": department_id},
    )


@bp.route("/appointments/<int:appointment_id>/cancel", methods=["POST"])
def cancel_appointment(appointment_id):
    from flask_login import current_user
    appointment = Appointment.query.get_or_404(appointment_id)
    try:
        booking.cancel_appointment(
            appointment, by_user=current_user,
            reason=(request.form.get("reason") or "Cancelled by administrator").strip(),
        )
        notify.appointment_cancelled(appointment, by_role="hospital")
        flash("Appointment cancelled and the patient notified.", "info")
    except ServiceError as exc:
        flash(str(exc), "danger")
    return redirect(request.referrer or url_for("admin.appointments"))


@bp.route("/queues")
def queues():
    sessions = queue_service.live_sessions()
    return render_template(
        "admin/queues.html",
        sessions=sessions,
        snapshots={s.id: queue_service.snapshot(s) for s in sessions},
    )


@bp.route("/session/<int:session_id>/close", methods=["POST"])
def force_close(session_id):
    """Close a clinic a doctor forgot to close, so tomorrow's board is clean."""
    session = ClinicSession.query.get_or_404(session_id)
    queue_service.close_session(session)
    flash(f"Clinic closed for {session.doctor.display_name}.", "info")
    return redirect(url_for("admin.queues"))


# ---------------------------------------------------------------------------
# Knowledge base
# ---------------------------------------------------------------------------
@bp.route("/knowledge-base", methods=["GET", "POST"])
def knowledge_base():
    """Editing the curated triage rules.

    This is in the UI because it is the part of the AI you can correct by
    hand, without retraining anything.
    """
    if request.method == "POST":
        phrase = (request.form.get("phrase") or "").strip()
        department_id = request.form.get("department_id", type=int)
        if not phrase or not department_id:
            flash("A rule needs a phrase and a department.", "danger")
        else:
            db.session.add(SymptomKB(
                department_id=department_id,
                phrase=phrase.lower(),
                weight=request.form.get("weight", type=float) or 1.0,
                is_red_flag=bool(request.form.get("is_red_flag")),
                advice=(request.form.get("advice") or "").strip() or None,
            ))
            db.session.commit()
            ai.invalidate_rules_cache()
            flash("Rule added.", "success")
        return redirect(url_for("admin.knowledge_base"))

    return render_template(
        "admin/knowledge_base.html",
        rules=SymptomKB.query.join(Department).order_by(
            SymptomKB.is_red_flag.desc(), Department.name, SymptomKB.phrase
        ).all(),
        departments=Department.query.order_by(Department.name).all(),
    )


@bp.route("/knowledge-base/<int:rule_id>/delete", methods=["POST"])
def delete_rule(rule_id):
    rule = SymptomKB.query.get_or_404(rule_id)
    db.session.delete(rule)
    db.session.commit()
    ai.invalidate_rules_cache()
    flash("Rule removed.", "info")
    return redirect(url_for("admin.knowledge_base"))
