# --- Stage 1: build the Vue frontend ---
FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Stage 2: python runtime ---
FROM python:3.11-slim
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    EDIBL_DATA_DIR=/data \
    EDIBL_FRONTEND_DIST=/app/frontend/dist \
    EDIBL_PORT=7746

# gosu: privileged setup runs as root, servers drop to a non-root user.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r app && useradd -r -g app -u 1000 -m -d /home/app app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/
COPY --from=frontend /build/dist ./frontend/dist
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh \
    && mkdir -p /data && chown -R app:app /app /data

VOLUME ["/data"]
EXPOSE 7746

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7746/api/v1/ready', timeout=3)" || exit 1

CMD ["/app/docker-entrypoint.sh"]
