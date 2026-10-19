# syntax=docker/dockerfile:1
# plats.hosh.it — one container: FastAPI + SQLite. Non-root, read-only rootfs, state in /data.

FROM python:3.14-slim-trixie AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
COPY requirements.txt /tmp/requirements.txt
RUN /venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
 && /venv/bin/pip uninstall -y pip

FROM python:3.14-slim-trixie
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="plats" \
      org.opencontainers.image.description="plats.hosh.it — which category is this French dish closest to?" \
      org.opencontainers.image.source="https://github.com/pazka/plats" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*
# The runtime needs no installer: drop pip/setuptools from the base image (smaller attack surface).
RUN python -m pip uninstall -y pip setuptools wheel
RUN groupadd --gid 10001 plats \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin plats \
 && mkdir -p /data && chown 10001:10001 /data && chmod 700 /data

COPY --from=build /venv /venv
WORKDIR /app
COPY app ./app
COPY scripts ./scripts

ENV PATH=/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLATS_DATA_DIR=/data \
    PLATS_VERSION=${VERSION}

USER 10001:10001
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"]

# Proxy headers are handled by the app itself (X-Forwarded-For trusted only from PLATS_TRUSTED_PROXIES).
CMD ["uvicorn", "--factory", "app.main:run", "--host", "0.0.0.0", "--port", "8000", \
     "--no-proxy-headers", "--no-server-header", "--no-access-log", "--workers", "1"]
