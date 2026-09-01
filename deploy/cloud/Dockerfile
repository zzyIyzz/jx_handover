# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONPATH=/opt/jx-handover/backend \
    TZ=Asia/Shanghai

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/jx-handover
COPY backend/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
    --requirement /tmp/requirements.txt

COPY backend/ ./backend/
COPY resources/ ./resources/
COPY --from=frontend-build /build/frontend/dist ./frontend/dist
COPY deploy/cloud/docker-entrypoint.sh /usr/local/bin/jx-cloud-entrypoint

RUN groupadd --gid 10001 jxhandover \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin jxhandover \
    && install -d -o 10001 -g 10001 -m 0750 /var/lib/jx-handover \
    && chmod 0755 /usr/local/bin/jx-cloud-entrypoint

USER 10001:10001
EXPOSE 1215

ENTRYPOINT ["/usr/local/bin/jx-cloud-entrypoint"]
