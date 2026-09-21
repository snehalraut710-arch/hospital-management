"""Application factory and CLI commands."""
from __future__ import annotations

import click
from flask import Flask, render_template

from config import Config
from hospital.extensions import csrf, db, login_manager


def create_app(config_object: type = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    # Imported inside the factory so the modules can import `db` from
    # extensions without a circular import back through this module.
    from hospital import models  # noqa: F401
    from hospital.blueprints import (
        admin, api, auth, chatbot, docs, doctor, patient, public,
    )

    app.register_blueprint(public.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(patient.bp)
    app.register_blueprint(doctor.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(chatbot.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(docs.bp)

    _register_template_helpers(app)
    _register_error_handlers(app)
    _register_commands(app)
    return app


def _register_template_helpers(app: Flask) -> None:
    from datetime import date, datetime

    from flask_login import current_user

    from hospital.models import ApptStatus, Department
    from hospital.services import notify

    @app.context_processor
    def inject_globals():
        unread = 0
        if current_user.is_authenticated:
            unread = notify.unread_count(current_user)
        return {
            "today": date.today(),
            "now": datetime.now(),
            "ApptStatus": ApptStatus,
            "unread_count": unread,
            "nav_departments": Department.query.filter_by(is_active=True)
                                               .order_by(Department.name).limit(8).all(),
        }

    @app.template_filter("time12")
    def time12(value):
        if value is None:
            return ""
        return value.strftime("%I:%M %p").lstrip("0")

    @app.template_filter("prettydate")
    def prettydate(value):
        if value is None:
            return ""
        today = date.today()
        if value == today:
            return "Today"
        if value == today.fromordinal(today.toordinal() + 1):
            return "Tomorrow"
        return value.strftime("%d %b %Y")

    @app.template_filter("money")
    def money(value):
        return f"Rs {value:,.0f}" if value else "Free"


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(403)
    def forbidden(_):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_):
        return render_template("errors/404.html"), 404

    @app.errorhandler(401)
    def unauthorised(_):
        return render_template("errors/403.html"), 401


def _register_commands(app: Flask) -> None:
    @app.cli.command("init-db")
    def init_db():
        """Create all tables."""
        db.create_all()
        click.echo("Database tables created.")

    @app.cli.command("seed")
    @click.option("--reset", is_flag=True, help="Drop everything first.")
    def seed_command(reset):
        """Load realistic demo data."""
        from seed import seed_database
        if reset:
            db.drop_all()
            click.echo("Dropped existing tables.")
        db.create_all()
        summary = seed_database()
        for line in summary:
            click.echo(f"  {line}")
        click.echo("Seed complete.")

    @app.cli.command("train-model")
    def train_model_command():
        """Train the triage classifier and report cross-validated accuracy."""
        from hospital.services import triage
        _, accuracy = triage.train_model(
            app.config["TRAINING_DATA"], app.config["MODEL_PATH"]
        )
        click.echo(f"Model trained. 5-fold cross-validated accuracy: {accuracy * 100:.1f}%")
        click.echo(f"Saved to {app.config['MODEL_PATH']}")
