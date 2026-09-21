"""The public-facing website: everything a visitor can see without an account."""
from flask import Blueprint, render_template, request

from hospital.models import Department, Doctor, User
from hospital.services import reports, scheduling

bp = Blueprint("public", __name__)


@bp.route("/")
def home():
    departments = (
        Department.query.filter_by(is_active=True).order_by(Department.name).all()
    )
    featured = (
        Doctor.query.filter_by(is_active=True)
        .order_by(Doctor.avg_rating.desc(), Doctor.experience_years.desc())
        .limit(6)
        .all()
    )
    stats = reports.headline_counts()
    return render_template(
        "public/home.html",
        departments=departments,
        featured_doctors=featured,
        stats=stats,
    )


@bp.route("/departments")
def departments():
    items = Department.query.filter_by(is_active=True).order_by(Department.name).all()
    return render_template("public/departments.html", departments=items)


@bp.route("/departments/<slug>")
def department_detail(slug):
    department = Department.query.filter_by(slug=slug).first_or_404()
    doctors = sorted(
        department.active_doctors,
        key=lambda d: (-(d.avg_rating or 0), -(d.experience_years or 0)),
    )
    return render_template(
        "public/department_detail.html", department=department, doctors=doctors
    )


@bp.route("/doctors")
def doctors():
    query = Doctor.query.filter_by(is_active=True).join(User).filter(User.is_active_flag.is_(True))

    department_id = request.args.get("department", type=int)
    if department_id:
        query = query.filter(Doctor.department_id == department_id)

    search = (request.args.get("q") or "").strip()
    if search:
        query = query.filter(User.name.ilike(f"%{search}%"))

    sort = request.args.get("sort", "rating")
    if sort == "experience":
        query = query.order_by(Doctor.experience_years.desc())
    elif sort == "fee":
        query = query.order_by(Doctor.consultation_fee.asc())
    else:
        query = query.order_by(Doctor.avg_rating.desc(), Doctor.rating_count.desc())

    return render_template(
        "public/doctors.html",
        doctors=query.all(),
        departments=Department.query.order_by(Department.name).all(),
        selected_department=department_id,
        search=search,
        sort=sort,
    )


@bp.route("/doctors/<int:doctor_id>")
def doctor_detail(doctor_id):
    doctor = Doctor.query.get_or_404(doctor_id)
    from hospital.services import ratings
    return render_template(
        "public/doctor_detail.html",
        doctor=doctor,
        upcoming=scheduling.next_available_days(doctor, horizon_days=14),
        reviews=ratings.recent_for_doctor(doctor, limit=5),
    )


@bp.route("/about")
def about():
    return render_template("public/about.html", stats=reports.headline_counts())
