FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Build deps for asyncpg & psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY pyproject.toml alembic.ini main.py ./
COPY alembic ./alembic
COPY aiforen ./aiforen

EXPOSE 8000

# Railway injects PORT at runtime; default 8000 for local/docker-compose.
CMD ["sh", "-c", "exec uvicorn aiforen.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
