"""Application configuration.

Values that a demo might want to tune live (triage weights, thresholds) are kept
here rather than buried in the service layer.
"""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "hospital.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- Triage engine -------------------------------------------------
    MODEL_PATH = os.environ.get(
        "MODEL_PATH", os.path.join(BASE_DIR, "models", "triage.joblib")
    )
    TRAINING_DATA = os.path.join(BASE_DIR, "hospital", "data", "symptoms.csv")
    # Final score = MODEL_WEIGHT * model_probability + KB_WEIGHT * keyword_score
    MODEL_WEIGHT = 0.7
    KB_WEIGHT = 0.3
    CONFIDENT_THRESHOLD = 0.60     # >= this: recommend outright
    CLARIFY_THRESHOLD = 0.35       # between: ask one clarifying question
    FALLBACK_DEPARTMENT = "General Medicine"

    # --- Booking -------------------------------------------------------
    BOOKING_HORIZON_DAYS = 14      # how far ahead patients may book
    DEFAULT_SLOT_MINUTES = 15

    # --- Notifications --------------------------------------------------
    MAIL_SERVER = os.environ.get("MAIL_SERVER")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_SENDER = os.environ.get("MAIL_SENDER", "noreply@hospital.local")


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
