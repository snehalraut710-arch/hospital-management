"""The AI chatbot.

Available without signing in: someone who does not yet know which department
they need should not have to create an account to find out. Sign-in is asked
for only at the point of booking.
"""
import uuid

from flask import Blueprint, jsonify, render_template, request, session
from flask_login import current_user

from hospital.services import ai

bp = Blueprint("chatbot", __name__, url_prefix="/chat")

GREETING = (
    "Hello. Describe what you're feeling in your own words -- for example "
    "\"I've had a throbbing headache for three days and light hurts my eyes\" -- "
    "and I'll suggest the right department and doctors."
)


def _session_key() -> str:
    """A stable id per browser session, so a conversation hangs together."""
    if "chat_key" not in session:
        session["chat_key"] = uuid.uuid4().hex
    return session["chat_key"]


@bp.route("/")
def chat():
    return render_template("chatbot/chat.html", greeting=GREETING)


@bp.route("/message", methods=["POST"])
def message():
    payload = request.get_json(silent=True) or request.form
    text = (payload.get("message") or "").strip()

    if not text:
        return jsonify({
            "reply": "Could you describe what you're experiencing?",
            "result": None, "doctors": [],
        })
    if len(text) > 1000:
        text = text[:1000]

    key = _session_key()
    user_id = current_user.id if current_user.is_authenticated else None

    result = ai.analyse(text)
    doctors = ai.recommend_doctors(result.department) if result.action != "fallback" else []
    reply = ai.compose_reply(result, doctors)

    ai.log_message(key, "user", text, user_id, result)
    ai.log_message(key, "bot", reply, user_id)

    return jsonify({
        "reply": reply,
        "result": result.to_dict(),
        "doctors": [
            {
                "id": d.id,
                "name": d.display_name,
                "department": d.department.name,
                "qualification": d.qualification,
                "experience": d.experience_years,
                "rating": d.avg_rating,
                "rating_count": d.rating_count,
                "fee": d.consultation_fee,
                "room": d.room_no,
            }
            for d in doctors
        ],
        "authenticated": current_user.is_authenticated,
    })
