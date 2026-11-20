#!/bin/sh
# Container entrypoint.
#
# Aegra migrates its own `public` tables on startup; the business tables in the
# `biz` schema are migrated here, before any request can arrive. Both are
# idempotent, so a restart is safe.
set -eu

echo "[entrypoint] applying business migrations (biz schema)"
alembic upgrade head

if [ -n "${SEED_APPLICATIONS_FILE:-}" ]; then
    if [ ! -f "${SEED_APPLICATIONS_FILE}" ]; then
        echo "[entrypoint] SEED_APPLICATIONS_FILE not found: ${SEED_APPLICATIONS_FILE}" >&2
        exit 1
    fi
    echo "[entrypoint] upserting applications from ${SEED_APPLICATIONS_FILE}"
    python -m app.cli seed-apps --file "${SEED_APPLICATIONS_FILE}"
    # Fails fast when a secret_ref cannot be resolved.
    python -m app.cli validate
fi

exec "$@"
