# Home Assistant add-on image for Edibl.
#
# Optimized for ARM (e.g. a Raspberry Pi): the Vue frontend is pre-built and
# vendored in ./webroot, so there is NO node/npm build on the device. Only the
# Python backend is installed — all deps ship aarch64/amd64 wheels, so there is
# no compilation either. The backend source is fetched from the (public) repo at
# build time; pin with --build-arg EDIBL_REF=<tag>.
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
# Backend source (small; no build step).
RUN git clone --depth 1 --branch "${EDIBL_REF}" https://github.com/Amantux/edibl /tmp/edibl \
    && cp -r /tmp/edibl/backend /app/backend \
    && cp /tmp/edibl/docker-entrypoint.sh /app/docker-entrypoint.sh \
    && rm -rf /tmp/edibl \
    && chmod +x /app/docker-entrypoint.sh

RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Pre-built frontend (vendored in the add-on build context — no npm on device).
COPY webroot /app/frontend/dist
COPY run.sh /run.sh
RUN chmod +x /run.sh \
    && mkdir -p /data && chown -R app:app /app /data

CMD [ "/run.sh" ]
