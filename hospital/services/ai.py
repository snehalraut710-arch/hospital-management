"""Application-facing wrapper around the triage engine.

triage.py does not touch the database so it can be tested on its own.
This module is the bridge: it loads the knowledge base from the database,
restricts answers to departments the hospital actually staffs, ranks the
doctors to suggest, and records the transcript.
"""
from __future__ import annotations

from flask import current_app

from hospital.extensions import db
from hospital.models import ChatMessage, Department, Doctor, SymptomKB
from hospital.services import scheduling, triage
from hospital.services.triage import KBRule, TriageResult

# Rules change only when an admin edits the knowledge base, but they are read on
# every chatbot message, so they are cached. The cache lives on the Flask app
# rather than in a module global: a global would leak between application
# instances in the same process (two apps, or a test suite) and make behaviour
# depend on which app happened to populate it first.
_CACHE_KEY = "triage_kb_rules"


def load_rules(refresh: bool = False) -> list[KBRule]:
    cache = current_app.extensions.setdefault("hospital", {})
    if not refresh and _CACHE_KEY in cache:
        return cache[_CACHE_KEY]

    rules = [
        KBRule(
            department=row.department.name,
            phrase=row.phrase,
            weight=row.weight,
            is_red_flag=row.is_red_flag,
            advice=row.advice,
        )
        for row in SymptomKB.query.join(Department).all()
    ]
    cache[_CACHE_KEY] = rules
    return rules


def invalidate_rules_cache() -> None:
    current_app.extensions.get("hospital", {}).pop(_CACHE_KEY, None)


def staffed_departments() -> list[str]:
    """Departments with at least one active doctor.

    Recommending a department the hospital does not staff would be a dead end
    for the patient, so those are excluded from the answer entirely.
    """
    return [
        name for (name,) in
        db.session.query(Department.name)
        .join(Doctor, Doctor.department_id == Department.id)
        .filter(Department.is_active.is_(True), Doctor.is_active.is_(True))
        .distinct()
        .all()
    ]


def model():
    config = current_app.config
    return triage.get_model(config["MODEL_PATH"], config["TRAINING_DATA"])


def analyse(text: str) -> TriageResult:
    config = current_app.config
    return triage.classify(
        text,
        model=model(),
        rules=load_rules(),
        available_departments=staffed_departments() or None,
        model_weight=config["MODEL_WEIGHT"],
        kb_weight=config["KB_WEIGHT"],
        confident_threshold=config["CONFIDENT_THRESHOLD"],
        clarify_threshold=config["CLARIFY_THRESHOLD"],
        fallback_department=config["FALLBACK_DEPARTMENT"],
    )


def recommend_doctors(department_name: str, limit: int = 3) -> list[Doctor]:
    """Best doctors in a department to suggest right now.

    Ordered by availability first, then rating, then experience. Availability
    leads because a five-star doctor with no free slot for a fortnight is not a
    useful recommendation -- the point is to get the patient seen.
    """
    department = Department.query.filter_by(name=department_name).first()
    if not department:
        return []

    doctors = [d for d in department.doctors if d.is_active and d.user.is_active]
    scored = []
    for doctor in doctors:
        free_today = scheduling.free_slots_today(doctor)
        days = scheduling.next_available_days(doctor, horizon_days=7)
        soonest = days[0][0] if days else None
        scored.append((
            0 if free_today else 1,
            soonest or  __import__("datetime").date.max,
            -(doctor.avg_rating or 0),
            -(doctor.experience_years or 0),
            doctor.id,
            doctor,
        ))
    scored.sort(key=lambda row: row[:5])
    return [row[5] for row in scored[:limit]]


def log_message(
    session_key: str,
    sender: str,
    text: str,
    user_id: int | None = None,
    result: TriageResult | None = None,
) -> ChatMessage:
    message = ChatMessage(
        session_key=session_key,
        sender=sender,
        text=text,
        user_id=user_id,
        predicted_specialty=result.department if result else None,
        confidence=result.confidence if result else None,
    )
    db.session.add(message)
    db.session.commit()
    return message


def compose_reply(result: TriageResult, doctors: list[Doctor]) -> str:
    """The bot's words. Kept here so tone stays consistent and testable."""
    if result.action == "emergency":
        return (
            f"{result.red_flag_advice}\n\n"
            f"Based on what you've described this falls under {result.department}. "
            "Please do not wait for an appointment if the symptoms are severe."
        )

    if result.action == "fallback":
        if result.clarifying_question:
            return result.clarifying_question
        return (
            "I'm not confident enough to point you to a specialist from that alone. "
            f"{result.department} is the safest starting point -- they can examine you "
            "and refer you onward if needed. Could you tell me a little more about "
            "what you're feeling?"
        )

    reason = ""
    if result.matched_terms:
        quoted = ", ".join(f"'{t}'" for t in result.matched_terms[:3])
        reason = f" I picked that up mainly from {quoted}."

    reply = (
        f"Based on what you've described, **{result.department}** looks like the right "
        f"department ({result.percent}% confidence).{reason}"
    )

    if result.action == "clarify" and result.clarifying_question:
        reply += (
            f"\n\nI'm not fully certain -- it could also be {result.runner_up}. "
            f"{result.clarifying_question}"
        )

    if doctors:
        names = ", ".join(d.display_name for d in doctors)
        reply += f"\n\nAvailable {result.department} doctors: {names}."

    return reply
