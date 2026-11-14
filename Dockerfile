# syntax=docker/dockerfile:1
# LiveKit Cloud build for the Clinical Voice Agent worker.
# Based on LiveKit's pip template; Python 3.11 matches what you test locally.
ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

# --- Build stage: compilers live here only ---
FROM base AS build
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    python3-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN python -m venv .venv
ENV PATH="/app/.venv/bin:$PATH"
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# --- Production stage ---
FROM base
ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/app" \
    --shell "/sbin/nologin" \
    --uid "${UID}" \
    appuser

WORKDIR /app
COPY --from=build --chown=appuser:appuser /app /app
ENV PATH="/app/.venv/bin:$PATH"
USER appuser

# "start" = production mode: connect to LiveKit Cloud and wait for calls.
CMD ["python", "-m", "backend.agent.voice_agent", "start"]
