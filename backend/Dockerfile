# Bewusst 3.13 statt 3.14: für asyncpg und qdrant-client sind dort fertige
# Wheels verfügbar, sonst wird bei jedem Build kompiliert.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Testwerkzeuge nur im Entwicklungs-Image (siehe docker-compose.override.yml).
ARG INSTALL_DEV=false

COPY pyproject.toml ./
RUN pip install --upgrade pip \
 && pip install . \
 && if [ "$INSTALL_DEV" = "true" ]; then pip install ".[dev]"; fi

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

RUN mkdir -p /app/uploads /app/data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
