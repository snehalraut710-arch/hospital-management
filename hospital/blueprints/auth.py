"""Registration, login, logout and profile."""
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from hospital.extensions import db
from hospital.models import PatientProfile, Role, User

bp = Blueprint("auth", __name__, url_prefix="/auth")


def home_for(user: User) -> str:
    """Where a user lands after signing in -- each role has its own workspace."""
    if user.is_admin:
        return url_for("admin.dashboard")
    if user.is_doctor:
        return url_for("doctor.queue")
    return url_for("patient.dashboard")


@bp.route("/register", methods=["GET", "POST"])
def register():
    """Public registration creates patients only.

    Doctors and admins are created by an administrator: letting anyone
    self-register as a doctor would be an obvious hole, and in a real hospital
    clinical staff are onboarded by the organisation, not by themselves.
    """
    if current_user.is_authenticated:
        return redirect(home_for(current_user))

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        phone = (request.form.get("phone") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""

        errors = []
        if len(name) < 2:
            errors.append("Please enter your full name.")
        if "@" not in email or "." not in email:
            errors.append("Please enter a valid email address.")
        if len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if password != confirm:
            errors.append("Passwords do not match.")
        if User.query.filter_by(email=email).first():
            errors.append("An account with that email already exists.")

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template("auth/register.html", form=request.form)

        user = User(name=name, email=email, phone=phone, role=Role.PATIENT)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()

        profile = PatientProfile(
            user_id=user.id,
            gender=(request.form.get("gender") or "").strip() or None,
            blood_group=(request.form.get("blood_group") or "").strip() or None,
        )
        dob = (request.form.get("date_of_birth") or "").strip()
        if dob:
            try:
                profile.date_of_birth = datetime.strptime(dob, "%Y-%m-%d").date()
            except ValueError:
                pass
        db.session.add(profile)
        db.session.commit()

        login_user(user)
        flash(f"Welcome, {user.name}. Your account is ready.", "success")
        return redirect(url_for("patient.dashboard"))

    return render_template("auth/register.html", form={})


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(home_for(current_user))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first()

        # One message for both cases: saying "no such account" would let anyone
        # enumerate which email addresses are registered.
        if not user or not user.check_password(password):
            flash("Incorrect email or password.", "danger")
            return render_template("auth/login.html", email=email)

        if not user.is_active:
            flash("This account has been deactivated. Please contact the hospital.", "warning")
            return render_template("auth/login.html", email=email)

        login_user(user, remember=bool(request.form.get("remember")))
        flash(f"Signed in as {user.name}.", "success")
        return redirect(request.args.get("next") or home_for(user))

    return render_template("auth/login.html", email="")


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("public.home"))


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    profile = current_user.patient_profile
    if current_user.is_patient and profile is None:
        profile = PatientProfile(user_id=current_user.id)
        db.session.add(profile)
        db.session.commit()

    if request.method == "POST":
        current_user.name = (request.form.get("name") or current_user.name).strip()
        current_user.phone = (request.form.get("phone") or "").strip()

        if profile:
            profile.gender = (request.form.get("gender") or "").strip() or None
            profile.blood_group = (request.form.get("blood_group") or "").strip() or None
            profile.allergies = (request.form.get("allergies") or "").strip() or None
            profile.chronic_conditions = (request.form.get("chronic_conditions") or "").strip() or None
            profile.address = (request.form.get("address") or "").strip() or None
            profile.emergency_contact = (request.form.get("emergency_contact") or "").strip() or None
            dob = (request.form.get("date_of_birth") or "").strip()
            if dob:
                try:
                    profile.date_of_birth = datetime.strptime(dob, "%Y-%m-%d").date()
                except ValueError:
                    flash("Date of birth was not understood and has been left unchanged.", "warning")

        password = request.form.get("password") or ""
        if password:
            if len(password) < 6:
                flash("Password must be at least 6 characters.", "danger")
                return render_template("auth/profile.html", profile=profile)
            current_user.set_password(password)
            flash("Password updated.", "success")

        db.session.commit()
        flash("Profile saved.", "success")
        return redirect(url_for("auth.profile"))

    return render_template("auth/profile.html", profile=profile)
