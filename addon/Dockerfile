# Home Assistant add-on image for Edibl.
#
# The add-on build context is this folder, so the app source is pulled from the
# (public) repo at build time. Pin to a ref with --build-arg EDIBL_REF=<tag>.

# --- Stage 1: build the Vue frontend ---
FROM node:20-alpine AS frontend
ARG EDIBL_REF=master
RUN apk add --no-cache git
RUN git clone --depth 1 --branch "${EDIBL_REF}" https://github.com/Amantux/edibl /src
WORKDIR /src/frontend
RUN npm install --no-audit --no-fund && npm run build

# --- Stage 2: python runtime (matches the standalone Edibl container) ---
ARG BUILD_FROM=python:3.11-slim-bookworm
FROM ${BUILD_FROM}
ARG EDIBL_REF=master

ENV PYTHONUNBUFFERED=1 \
    EDIBL_DATA_DIR=/data \
    EDIBL_FRONTEND_DIST=/app/frontend/dist \
    EDIBL_PORT=7746

# gosu: privileged setup runs as root, servers drop to a non-root user (same as
# the standalone image + docker-entrypoint.sh).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r app && useradd -r -g app -u 1000 -m -d /home/app app

WORKDIR /app
RUN git clone --depth 1 --branch "${EDIBL_REF}" https://github.com/Amantux/edibl /tmp/edibl \
    && cp -r /tmp/edibl/backend /app/backend \
    && cp /tmp/edibl/docker-entrypoint.sh /app/docker-entrypoint.sh \
    && rm -rf /tmp/edibl \
    && chmod +x /app/docker-entrypoint.sh

RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY --from=frontend /src/frontend/dist /app/frontend/dist
COPY run.sh /run.sh
RUN chmod +x /run.sh \
    && mkdir -p /data && chown -R app:app /app /data

CMD [ "/run.sh" ]
