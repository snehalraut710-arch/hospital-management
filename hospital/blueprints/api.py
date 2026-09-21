"""Small JSON endpoints that keep the live pages current.

Polling rather than WebSockets: a five-second poll is entirely adequate for a
clinic queue, needs no extra server process, and cannot leave the page silently
stale after a dropped socket.
"""
from datetime import datetime

from flask import Blueprint, abort, jsonify, request
from flask_login import current_user, login_required

from hospital.models import Appointment, ClinicSession, Doctor
from hospital.services import queue as queue_service
from hospital.services import scheduling

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/queue/<int:session_id>")
def queue_snapshot(session_id):
    """Public board data for one clinic sitting.

    Patient names are included only for signed-in staff; everyone else sees
    token numbers alone, which is what a waiting-room display should show.
    """
    session = ClinicSession.query.get_or_404(session_id)
    data = queue_service.snapshot(session)

    is_staff = current_user.is_authenticated and (
        current_user.is_admin
        or (current_user.is_doctor and current_user.doctor
            and current_user.doctor.id == session.doctor_id)
    )
    if not is_staff:
        data["serving_patient"] = None
        for token in data["tokens"]:
            token["patient"] = None

    return jsonify(data)


@bp.route("/my-position/<int:appointment_id>")
@login_required
def my_position(appointment_id):
    appointment = Appointment.query.get_or_404(appointment_id)
    if appointment.patient_id != current_user.id and not current_user.is_admin:
        abort(403)
    return jsonify(queue_service.position_of(appointment).to_dict())


@bp.route("/slots/<int:doctor_id>")
def slots(doctor_id):
    doctor = Doctor.query.get_or_404(doctor_id)
    raw = request.args.get("date") or ""
    try:
        day = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        abort(400)

    return jsonify({
        "doctor": doctor.display_name,
        "date": day.isoformat(),
        "slots": [
            {"start": s.start.strftime("%Y-%m-%d %H:%M"), "label": s.label}
            for s in scheduling.available_slots(doctor, day)
        ],
    })
