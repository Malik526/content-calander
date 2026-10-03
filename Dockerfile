# Dockerfile — the one image for both Railway services (API: railway.json,
# worker: railway.worker.json). Each service's deploy.startCommand picks
# what runs; this file only builds.
#
# Why a Dockerfile instead of Nixpacks (Milestone 3.14 follow-up): Nixpacks'
# generated Dockerfile declares `ARG X` + `ENV X=$X` for every service
# variable it is given, so runtime secrets (CREDENTIAL_ENCRYPTION_KEY,
# SERVICE_ROLE_KEY, TIKTOK_CLIENT_SECRET, ...) were passed into the image
# build and written into the image's ENV config — Docker's
# SecretsUsedInArgOrEnv warnings in the build log. With Railway's Dockerfile
# builder, a service variable reaches the build only if the Dockerfile
# declares it with ARG. This file declares none: the build needs no secrets,
# and Railway injects every variable into the container at runtime.
#
# Do not add an ARG or ENV for a secret here. If a build step ever needs a
# credential, use a BuildKit secret mount, never ARG/ENV.

FROM python:3.11-slim-bookworm

# Non-secret build/runtime settings only.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg provides ffprobe, which the worker needs to inspect media before
# publishing (scheduling/hosted_worker.worker_prerequisite_problems refuses
# to start without it). Replaces nixpacks.toml's nixPkgs entry.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so source-only changes reuse this layer.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Same install the Nixpacks buildCommand ran: a real (non-editable) install,
# which carries persistence/postgres_migrations/*.sql as package data
# (pyproject.toml). cli/*.py run from /app and import the installed package.
COPY . .
RUN pip install .
