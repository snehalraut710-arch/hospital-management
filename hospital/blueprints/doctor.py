"""The doctor's workspace: the live queue console and clinical record-keeping."""
from datetime import date as Date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from hospital.extensions import db
from hospital.models import (
    Appointment, ApptStatus, ClinicSession, Consultation, PrescriptionItem,
)
from hospital.security import doctor_required
from hospital.services import notify, ratings, scheduling
from hospital.services import queue as queue_service
from hospital.services.errors import ServiceError

bp = Blueprint("doctor", __name__, url_prefix="/doctor")


@bp.before_request
@login_required
@doctor_required
def guard():
    """Doctors only -- and a doctor account must have a doctor profile."""
    if current_user.doctor is None:
        abort(403)


def me():
    return current_user.doctor


def _own_appointment(appointment_id: int) -> Appointment:
    appointment = Appointment.query.get_or_404(appointment_id)
    if appointment.doctor_id != me().id:
        abort(403)
    return appointment


def _own_session(session_id: int) -> ClinicSession:
    session = ClinicSession.query.get_or_404(session_id)
    if session.doctor_id != me().id:
        abort(403)
    return session


# ---------------------------------------------------------------------------
# Queue console
# ---------------------------------------------------------------------------
@bp.route("/")
def queue_console():
    return redirect(url_for("doctor.queue"))


@bp.route("/queue")
def queue():
    day_param = request.args.get("date")
    day = Date.today()
    if day_param:
        try:
            day = datetime.strptime(day_param, "%Y-%m-%d").date()
        except ValueError:
            flash("That date was not understood.", "warning")

    sessions = scheduling.todays_sessions(me(), day)
    return render_template(
        "doctor/queue.html",
        day=day,
        sessions=sessions,
        snapshots={s.id: queue_service.snapshot(s) for s in sessions},
    )


@bp.route("/session/<int:session_id>/open", methods=["POST"])
def open_session(session_id):
    session = _own_session(session_id)
    try:
        queue_service.open_session(session)
        flash("Clinic opened.", "success")
    except ServiceError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("doctor.queue", date=session.date.isoformat()))


@bp.route("/session/<int:session_id>/call-next", methods=["POST"])
def call_next(session_id):
    session = _own_session(session_id)
    try:
        called = queue_service.call_next(session)
    except ServiceError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("doctor.queue", date=session.date.isoformat()))

    if called is None:
        flash("Nobody is waiting in this queue.", "info")
        return redirect(url_for("doctor.queue", date=session.date.isoformat()))

    notify.patient_called(called)
    return redirect(url_for("doctor.consult", appointment_id=called.id))


@bp.route("/session/<int:session_id>/close", methods=["POST"])
def close_session(session_id):
    session = _own_session(session_id)
    queue_service.close_session(session)
    flash("Clinic closed. Anyone still waiting was marked as a no-show.", "info")
    return redirect(url_for("doctor.queue", date=session.date.isoformat()))


@bp.route("/appointment/<int:appointment_id>/check-in", methods=["POST"])
def check_in(appointment_id):
    """Reception-desk check-in, for patients who arrive without using the app."""
    appointment = _own_appointment(appointment_id)
    try:
        queue_service.check_in(appointment)
        flash(f"Token #{appointment.token_number} checked in.", "success")
    except ServiceError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("doctor.queue", date=appointment.appt_date.isoformat()))


@bp.route("/appointment/<int:appointment_id>/no-show", methods=["POST"])
def no_show(appointment_id):
    appointment = _own_appointment(appointment_id)
    try:
        queue_service.mark_no_show(appointment)
        flash(f"Token #{appointment.token_number} marked as a no-show.", "info")
    except ServiceError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("doctor.queue", date=appointment.appt_date.isoformat()))


# ---------------------------------------------------------------------------
# Consultation
# ---------------------------------------------------------------------------
@bp.route("/consult/<int:appointment_id>", methods=["GET", "POST"])
def consult(appointment_id):
    appointment = _own_appointment(appointment_id)

    if request.method == "POST":
        if appointment.status != ApptStatus.IN_CONSULT:
            flash("This consultation is no longer open.", "warning")
            return redirect(url_for("doctor.queue"))

        consultation = appointment.consultation or Consultation(appointment_id=appointment.id)
        consultation.diagnosis = (request.form.get("diagnosis") or "").strip() or None
        consultation.notes = (request.form.get("notes") or "").strip() or None
        consultation.advice = (request.form.get("advice") or "").strip() or None

        follow_up = (request.form.get("follow_up_date") or "").strip()
        if follow_up:
            try:
                consultation.follow_up_date = datetime.strptime(follow_up, "%Y-%m-%d").date()
            except ValueError:
                flash("The follow-up date was not understood and has been skipped.", "warning")

        db.session.add(consultation)
        db.session.flush()

        # Prescription rows are re-created rather than diffed: the form posts the
        # whole list each time, and a handful of rows is not worth diffing.
        for item in list(consultation.prescription_items):
            db.session.delete(item)

        drugs = request.form.getlist("drug")
        dosages = request.form.getlist("dosage")
        frequencies = request.form.getlist("frequency")
        durations = request.form.getlist("duration")
        instructions = request.form.getlist("instructions")

        for index, drug in enumerate(drugs):
            drug = (drug or "").strip()
            if not drug:
                continue
            db.session.add(PrescriptionItem(
                consultation_id=consultation.id,
                drug=drug,
                dosage=_at(dosages, index),
                frequency=_at(frequencies, index),
                duration=_at(durations, index),
                instructions=_at(instructions, index),
            ))

        db.session.commit()

        try:
            queue_service.complete(appointment)
        except ServiceError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("doctor.queue"))

        notify.consultation_ready(appointment)
        flash(f"Consultation for token #{appointment.token_number} completed.", "success")
        return redirect(url_for("doctor.queue", date=appointment.appt_date.isoformat()))

    history = (
        Appointment.query
        .filter(
            Appointment.patient_id == appointment.patient_id,
            Appointment.status == ApptStatus.COMPLETED,
            Appointment.id != appointment.id,
        )
        .order_by(Appointment.slot_start.desc())
        .limit(5)
        .all()
    )
    return render_template(
        "doctor/consult.html",
        appointment=appointment,
        profile=appointment.patient.patient_profile,
        history=history,
    )


def _at(values, index):
    value = values[index] if index < len(values) else ""
    return (value or "").strip() or None


# ---------------------------------------------------------------------------
# Schedule and history
# ---------------------------------------------------------------------------
@bp.route("/schedule")
def schedule():
    from hospital.models import DoctorSchedule
    return render_template(
        "doctor/schedule.html",
        schedules=sorted(me().schedules, key=lambda s: (s.weekday, s.start_time)),
        exceptions=sorted(
            (e for e in me().exceptions if e.date >= Date.today()), key=lambda e: e.date
        ),
        weekdays=DoctorSchedule.WEEKDAYS,
    )


@bp.route("/history")
def history():
    page = request.args.get("page", 1, type=int)
    pagination = (
        Appointment.query
        .filter(Appointment.doctor_id == me().id, Appointment.status == ApptStatus.COMPLETED)
        .order_by(Appointment.slot_start.desc())
        .paginate(page=page, per_page=20, error_out=False)
    )
    return render_template("doctor/history.html", pagination=pagination)


@bp.route("/reviews")
def reviews():
    return render_template(
        "doctor/reviews.html",
        doctor=me(),
        reviews=ratings.recent_for_doctor(me(), limit=50),
    )
