# MediQueue - AI Powered Hospital Appointment & Queue Management System
#
# Everything runs inside the container: the web app, the SQLite database and
# the machine learning model. No network access is needed at run time.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so editing application code does not invalidate this
# layer. gunicorn is installed here rather than in requirements.txt because it
# is only needed for the container, not for local development.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0

COPY . .

# Train the classifier at build time so the image is self-contained and the
# first chatbot request does not have to wait for it.
RUN python -c "\
from config import Config; \
from hospital.services import triage; \
_, acc = triage.train_model(Config.TRAINING_DATA, Config.MODEL_PATH); \
print(f'triage model trained, cross-validated accuracy {acc:.1%}')"

# Run as a normal user. /app/data is created here so that a named volume
# mounted over it inherits the right ownership.
RUN useradd --create-home --uid 1000 mediqueue \
 && mkdir -p /app/data \
 && chown -R mediqueue:mediqueue /app
USER mediqueue

ENV FLASK_APP=wsgi \
    DATABASE_URL=sqlite:////app/data/hospital.db

EXPOSE 5000

# Note: podman builds OCI-format images by default and ignores this
# instruction. docker-compose.yml declares the same check, which both
# Docker and Podman honour. Build with `podman build --format docker` if
# you want it baked into the image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:5000/').read()" \
      || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]

# One worker with threads rather than several processes: the database is
# SQLite, and concurrent writers from separate processes would contend for the
# file lock.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", \
     "--workers", "1", "--threads", "8", "--worker-class", "gthread", \
     "--timeout", "60", "--access-logfile", "-", "wsgi:app"]
