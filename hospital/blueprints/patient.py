"""The patient's workspace: booking, the live queue, history and ratings."""
from datetime import date as Date, datetime, timedelta

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
)
from flask_login import current_user, login_required

from hospital.extensions import db
from hospital.models import Appointment, ApptStatus, Department, Doctor, Notification
from hospital.security import patient_required
from hospital.services import ai, booking, notify, queue, ratings, scheduling
from hospital.services.errors import ServiceError

bp = Blueprint("patient", __name__, url_prefix="/patient")


@bp.before_request
@login_required
@patient_required
def guard():
    """Every route in this blueprint is patients-only."""


@bp.route("/")
def dashboard():
    upcoming = booking.patient_appointments(current_user, upcoming=True)
    past = booking.patient_appointments(current_user, upcoming=False)[:5]
    live = [a for a in upcoming if a.appt_date == Date.today()]
    return render_template(
        "patient/dashboard.html",
        upcoming=upcoming, past=past, live_today=live,
        awaiting_rating=[a for a in booking.patient_appointments(current_user) if a.can_rate][:3],
    )


# ---------------------------------------------------------------------------
# Booking flow: symptoms -> department -> doctor -> date -> slot -> confirm
# ---------------------------------------------------------------------------
@bp.route("/book")
def book_start():
    departments = Department.query.filter_by(is_active=True).order_by(Department.name).all()
    suggestion = None
    symptoms = (request.args.get("symptoms") or "").strip()
    if symptoms:
        result = ai.analyse(symptoms)
        suggestion = {
            "result": result,
            "doctors": ai.recommend_doctors(result.department),
        }
    return render_template(
        "patient/book_start.html",
        departments=departments, symptoms=symptoms, suggestion=suggestion,
    )


@bp.route("/book/department/<int:department_id>")
def book_department(department_id):
    department = Department.query.get_or_404(department_id)
    symptoms = (request.args.get("symptoms") or "").strip()
    doctors = sorted(
        department.active_doctors,
        key=lambda d: (-(d.avg_rating or 0), -(d.experience_years or 0)),
    )
    availability = {
        d.id: scheduling.next_available_days(d, current_app.config["BOOKING_HORIZON_DAYS"])
        for d in doctors
    }
    return render_template(
        "patient/book_doctor.html",
        department=department, doctors=doctors,
        availability=availability, symptoms=symptoms,
    )


@bp.route("/book/doctor/<int:doctor_id>")
def book_slots(doctor_id):
    doctor = Doctor.query.get_or_404(doctor_id)
    if not doctor.is_active:
        abort(404)

    symptoms = (request.args.get("symptoms") or "").strip()
    horizon = current_app.config["BOOKING_HORIZON_DAYS"]

    day_param = request.args.get("date")
    day = Date.today()
    if day_param:
        try:
            day = datetime.strptime(day_param, "%Y-%m-%d").date()
        except ValueError:
            flash("That date was not understood.", "warning")

    if not (Date.today() <= day <= Date.today() + timedelta(days=horizon)):
        flash(f"Bookings are open for the next {horizon} days only.", "warning")
        day = Date.today()

    return render_template(
        "patient/book_slots.html",
        doctor=doctor,
        day=day,
        slots=scheduling.available_slots(doctor, day),
        calendar=[Date.today() + timedelta(days=i) for i in range(horizon)],
        available_days={d: c for d, c in scheduling.next_available_days(doctor, horizon)},
        symptoms=symptoms,
    )


@bp.route("/book/confirm", methods=["POST"])
def book_confirm():
    doctor = Doctor.query.get_or_404(request.form.get("doctor_id", type=int))
    slot_raw = request.form.get("slot_start") or ""
    symptoms = (request.form.get("symptoms") or "").strip()
    via = request.form.get("booked_via") or "web"

    try:
        slot_start = datetime.strptime(slot_raw, "%Y-%m-%d %H:%M")
    except ValueError:
        flash("That time slot was not understood. Please choose again.", "danger")
        return redirect(url_for("patient.book_slots", doctor_id=doctor.id))

    specialty = confidence = None
    if symptoms:
        result = ai.analyse(symptoms)
        specialty, confidence = result.department, result.confidence

    try:
        appointment = booking.book_appointment(
            patient=current_user, doctor=doctor, slot_start=slot_start,
            symptoms_text=symptoms or None,
            triage_specialty=specialty, triage_confidence=confidence,
            booked_via=via,
        )
    except ServiceError as exc:
        flash(str(exc), "danger")
        return redirect(url_for(
            "patient.book_slots", doctor_id=doctor.id,
            date=slot_start.date().isoformat(), symptoms=symptoms,
        ))

    notify.appointment_booked(appointment)
    flash(
        f"Appointment confirmed. Your token number is #{appointment.token_number}.",
        "success",
    )
    return redirect(url_for("patient.appointment_detail", appointment_id=appointment.id))


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------
def _own_appointment(appointment_id: int) -> Appointment:
    appointment = Appointment.query.get_or_404(appointment_id)
    if appointment.patient_id != current_user.id:
        abort(403)
    return appointment


@bp.route("/appointments")
def appointments():
    return render_template(
        "patient/appointments.html",
        upcoming=booking.patient_appointments(current_user, upcoming=True),
        past=booking.patient_appointments(current_user, upcoming=False),
    )


@bp.route("/appointments/<int:appointment_id>")
def appointment_detail(appointment_id):
    appointment = _own_appointment(appointment_id)
    return render_template(
        "patient/appointment_detail.html",
        appointment=appointment,
        position=queue.position_of(appointment) if appointment.is_live else None,
    )


@bp.route("/appointments/<int:appointment_id>/cancel", methods=["POST"])
def cancel(appointment_id):
    appointment = _own_appointment(appointment_id)
    try:
        booking.cancel_appointment(
            appointment, by_user=current_user,
            reason=(request.form.get("reason") or "").strip() or None,
        )
    except ServiceError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("patient.appointment_detail", appointment_id=appointment.id))

    notify.appointment_cancelled(appointment, by_role="patient")
    flash("Appointment cancelled.", "info")
    return redirect(url_for("patient.appointments"))


@bp.route("/appointments/<int:appointment_id>/check-in", methods=["POST"])
def check_in(appointment_id):
    """Patient marks themselves as arrived.

    Only on the day itself -- checking in a week early would put someone in a
    queue that does not exist yet, and would jump them ahead of patients who
    are actually in the building.
    """
    appointment = _own_appointment(appointment_id)
    if appointment.appt_date != Date.today():
        flash("You can only check in on the day of your appointment.", "warning")
        return redirect(url_for("patient.appointment_detail", appointment_id=appointment.id))

    try:
        queue.check_in(appointment)
        flash(
            f"Checked in. You are token #{appointment.token_number} -- "
            "please take a seat and watch the queue.",
            "success",
        )
    except ServiceError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("patient.live_queue", appointment_id=appointment.id))


@bp.route("/appointments/<int:appointment_id>/queue")
def live_queue(appointment_id):
    appointment = _own_appointment(appointment_id)
    return render_template(
        "patient/queue.html",
        appointment=appointment,
        position=queue.position_of(appointment),
        snapshot=queue.snapshot(appointment.session),
    )


@bp.route("/appointments/<int:appointment_id>/rate", methods=["POST"])
def rate(appointment_id):
    appointment = _own_appointment(appointment_id)
    try:
        ratings.submit_rating(
            appointment, patient=current_user,
            stars=request.form.get("stars", type=int) or 0,
            comment=request.form.get("comment"),
        )
        flash("Thank you for your feedback.", "success")
    except ServiceError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("patient.appointment_detail", appointment_id=appointment.id))


@bp.route("/prescription/<int:appointment_id>")
def prescription(appointment_id):
    appointment = _own_appointment(appointment_id)
    if not appointment.consultation:
        flash("No prescription has been issued for that visit.", "warning")
        return redirect(url_for("patient.appointment_detail", appointment_id=appointment.id))
    return render_template("patient/prescription.html", appointment=appointment)


@bp.route("/notifications")
def notifications():
    items = Notification.query.filter_by(user_id=current_user.id).order_by(
        Notification.created_at.desc()
    ).limit(50).all()
    notify.mark_all_read(current_user)
    return render_template("patient/notifications.html", notifications=items)
