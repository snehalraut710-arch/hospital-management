"""Role-based access control.

One decorator per role rather than a generic permission system: with exactly
three fixed roles, a permission framework would be more machinery than the
problem justifies, and `@doctor_required` reads better at the call site than
`@requires("appointment.write")`.
"""
from functools import wraps

from flask import abort
from flask_login import current_user

from hospital.models import Role


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapper
    return decorator


admin_required = role_required(Role.ADMIN)
doctor_required = role_required(Role.DOCTOR)
patient_required = role_required(Role.PATIENT)
staff_required = role_required(Role.ADMIN, Role.DOCTOR)
