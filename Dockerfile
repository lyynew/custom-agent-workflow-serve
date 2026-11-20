# syntax=docker/dockerfile:1

# Pinned so the image rebuilds identically; bump together with uv.lock.
FROM ghcr.io/astral-sh/uv:0.11.26 AS uv

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH

RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --create-home app

WORKDIR /app

COPY --from=uv /uv /uvx /bin/

# Dependency layer first: editing app code does not re-resolve dependencies.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini aegra.json ./
COPY migrations ./migrations
COPY app ./app
COPY seed ./seed
COPY scripts ./scripts

RUN chmod +x scripts/*.sh && chown -R app:app /app

USER app

EXPOSE 2026

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["aegra", "serve", "--host", "0.0.0.0", "--port", "2026"]
