# syntax=docker/dockerfile:1
# P25: production-oriented image for the Hospital Comparison API + workers.
# No secrets are baked in; configuration is environment-only at runtime.

FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Non-root user (least privilege)
RUN groupadd --system app && useradd --system --gid app --create-home app

WORKDIR /app

# System deps commonly needed for lxml/pandas wheels on slim images
# (binary wheels usually suffice; kept minimal and overridable).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install package (copy only metadata first for better layer caching when
# using BuildKit with lockfiles; full source follows).
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config

RUN pip install --upgrade pip \
    && pip install .

# Default runtime paths — operators override via env
ENV MEDCOMPARE_DB_PATH=/app/data/medcompare.db \
    MEDCOMPARE_AUTH_MODE=production \
    MEDCOMPARE_TRUSTED_HOSTS=localhost,127.0.0.1

RUN mkdir -p /app/data && chown -R app:app /app
USER app

EXPOSE 8000

# Default: API. Workers override the command:
#   docker run ... universal-hospital-extractor hospital-extractor worker --once
#   docker run ... universal-hospital-extractor hospital-extractor monitoring worker --once
# Development/local images may set MEDCOMPARE_AUTH_MODE=development.
CMD ["hospital-extractor", "serve", "--host", "0.0.0.0", "--port", "8000"]
