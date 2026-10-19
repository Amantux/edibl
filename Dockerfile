# --- Stage 1: build the Vue frontend ---
FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Stage 2: python runtime ---
FROM python:3.14-slim
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    EDIBL_DATA_DIR=/data \
    EDIBL_FRONTEND_DIST=/app/frontend/dist \
    EDIBL_PORT=7746 \
    # gosu does not reset HOME; without this it stays /root and anything
    # writing ~/.cache (pip, httpx trust_env, tokenizers) hits EACCES as uid 1000.
    HOME=/home/app

# gosu: privileged setup runs as root, servers drop to a non-root user.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r app && useradd -r -g app -u 1000 -m -d /home/app app

COPY backend/requirements.txt ./backend/requirements.txt
# git is a BUILD-only dependency: foodcore is pinned to a commit of the shared
# repository, and pip shells out to git for a VCS requirement. Installed and
# purged inside one layer so the shipped image carries neither git nor its
# ~50 MB of dependencies.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && pip install --no-cache-dir -r backend/requirements.txt \
    && apt-get purge -y --auto-remove git \
    && rm -rf /var/lib/apt/lists/*

# Assert the foodcore ARTIFACT, not merely that `import foodcore` succeeds.
# This library has already shipped a wheel that imported cleanly while missing
# a subpackage, and its USDA reference snapshot is package DATA, which a
# pyproject change can silently drop. Both failures present identically at
# runtime — the food-aware conversion and the allergen read quietly do nothing
# — and neither is visible from a source checkout, where the files are simply
# there on disk. Fail the build instead.
RUN python3 -c "import foodcore, gzip, os; \
p = os.path.join(os.path.dirname(foodcore.__file__), 'data', 'usda-sr-legacy-2018-04.sqlite.gz'); \
assert os.path.isfile(p), 'the shipped reference dataset is missing from the installed wheel'; \
assert len(gzip.open(p, 'rb').read(64)) == 64, 'the shipped reference dataset is not readable gzip'; \
__import__('foodcore.rules.rulesets', fromlist=['x']).allergen_ruleset(); \
__import__('foodcore.allergens', fromlist=['x']); \
__import__('foodcore.convert', fromlist=['x']); \
print('foodcore', foodcore.__version__, '+ dataset', round(os.path.getsize(p) / 1e6, 1), 'MB')"

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
