# Familiar runtime image. Built from source by Komodo (run_build=true), not pulled
# from a registry.
#
# Two stages because the app is one process serving two artifacts: the Vite build
# needs Node and ~200MB of node_modules, none of which the runtime needs. Only
# frontend/dist crosses into the final image.

FROM node:22-slim AS frontend

WORKDIR /build

# package.json + lockfile first so npm ci is cached until dependencies actually change.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl_cffi ships manylinux wheels but pulls libcurl-impersonate at runtime; ca-certificates
# is needed for the outbound HTTPS calls to Scryfall/EDHREC/Moxfield and the LLM provider.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY backend/ ./backend/
RUN pip install --no-cache-dir -e ./backend
COPY --from=frontend /build/dist ./frontend/dist

# State lives on the mounted volume, never in the image layer. These are the
# container-side defaults; docker-compose.yml sets them explicitly too so the
# contract is visible at the deploy site rather than only here.
ENV FAMILIAR_DB_PATH=/data/familiar.db \
    EDHREC_CACHE_DIR=/data/cache/edhrec \
    ORACLE_TAGS_CACHE_PATH=/data/oracle_tags_cache.json \
    FRONTEND_DIST_PATH=/app/frontend/dist

# Non-root. /data is chowned so the volume is writable when Docker creates it
# empty on first run; a pre-existing host directory must match this uid.
RUN useradd --create-home --uid 10001 familiar \
    && mkdir -p /data \
    && chown -R familiar:familiar /data /app
USER familiar

EXPOSE 8420

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8420/').status==200 else 1)"]

# --host 0.0.0.0: the default 127.0.0.1 bind is unreachable from outside the container.
CMD ["uvicorn", "app.main:app", "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "8420"]
