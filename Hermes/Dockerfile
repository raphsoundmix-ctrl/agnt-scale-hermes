FROM python:3.12-slim

# Non-root user
RUN useradd --create-home --shell /bin/bash --uid 1000 app

WORKDIR /app

# Build deps for asyncpg / cryptography C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .

USER app

EXPOSE 7777

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:7777/health || exit 1

# Single worker — in-memory rate-limiter is process-local; multiple workers
# would multiply per-tenant limits by N. Use Redis-backed limiter before scaling.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-7777} --workers 1"]
