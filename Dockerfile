# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# frontend — vite build; only the compiled dist/ crosses into the backend
# image, so no node/npm ends up in the final image.
# ---------------------------------------------------------------------------
FROM node:22-alpine AS frontend-builder
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# backend — deps from the committed lockfile, then app code, then the built
# SPA copied in as static/ (see app/main.py's SPA-fallback route). One stage:
# uv sync is fast enough that a deps/runner split buys nothing here, unlike
# the old npm image where `next build` needed the full node_modules tree.
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS backend
WORKDIR /app

# Baked in at build time (e.g. `--build-arg PROMPTRACK_COMMIT=$(git rev-parse
# --short HEAD)`) so `GET /api/version` can report what's actually running;
# empty by default, which the endpoint reports as a null commit.
ARG PROMPTRACK_COMMIT=""
ENV PROMPTRACK_COMMIT=$PROMPTRACK_COMMIT

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    ENVIRONMENT=production

# pyproject.toml + uv.lock alone, so this layer is cached across app-only
# changes; backend/pyproject.toml carries no [build-system], so `uv sync`
# installs only the locked dependencies, never tries to build the project
# itself as a package.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev

COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
COPY --from=frontend-builder /app/dist ./static
COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
