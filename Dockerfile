# Multi-stage Dockerfile for all V8 services.
# Build: docker build --build-arg SERVICE=ingestor -t v8-ingestor .
# Run:   docker run -p 8080:8080 --env-file .env v8-ingestor

FROM python:3.12-slim AS base

ARG SERVICE
ENV SERVICE=${SERVICE}
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl \
    && rm -rf /var/lib/apt/lists/*

# Copy shared library
COPY services/shared/ /app/shared/

# Copy service-specific code
COPY services/${SERVICE}/ /app/${SERVICE}/

# Install Python deps
COPY services/${SERVICE}/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

EXPOSE 8080

# Run the Flask app via gunicorn
RUN pip install --no-cache-dir gunicorn

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:8080 --workers 1 --timeout 900 ${SERVICE}.main:app"]
