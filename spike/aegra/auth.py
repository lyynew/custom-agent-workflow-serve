"""Per-application JWT authentication for the Aegra spike.

Design notes:
- `X-App-Id` only selects the application configuration; it is never an identity.
- Each application has its own algorithm and secret, resolved through `secret_ref`.
- The returned identity is namespaced as ``{app_id}:{user_id}`` so Aegra's
  owner-scoped thread queries isolate applications even when user IDs collide.
- Application configuration is read from PostgreSQL, proving that the auth
  handler can use an async DB pool inside the request event loop.
"""

from __future__ import annotations

import json
import os
from typing import Any

import asyncpg
import jwt
from langgraph_sdk import Auth

auth = Auth()

_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool:
    """Lazily create one pool per process; safe because handlers share the app loop."""
    global _pool
    if _pool is None:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL is required for the auth handler")
        _pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)
    return _pool


def _lower_headers(headers: dict[str, Any]) -> dict[str, str]:
    return {str(key).lower(): value for key, value in headers.items()}


def _resolve_secret(secret_ref: str) -> str:
    """Resolve ``env:NAME`` references; fail loudly instead of falling back."""
    if not secret_ref.startswith("env:"):
        raise RuntimeError(f"Unsupported secret_ref: {secret_ref}")
    name = secret_ref[4:]
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing secret environment variable: {name}")
    return value


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value)
    raise RuntimeError(f"Unexpected JSONB value type: {type(value)!r}")


@auth.authenticate
async def authenticate(headers: dict[str, Any]) -> dict[str, Any]:
    lowered = _lower_headers(headers)

    app_id = lowered.get("x-app-id")
    if not app_id:
        raise Auth.exceptions.HTTPException(status_code=401, detail="Missing X-App-Id header")

    authorization = lowered.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Auth.exceptions.HTTPException(status_code=401, detail="Missing bearer token")
    token = token.strip()

    pool = await _get_pool()
    row = await pool.fetchrow(
        """
        SELECT app_id, enabled, jwt_config, allowed_agents, allowed_workflows
        FROM biz.applications
        WHERE app_id = $1
        """,
        app_id,
    )
    if row is None:
        raise Auth.exceptions.HTTPException(status_code=401, detail="Unknown application")
    if not row["enabled"]:
        raise Auth.exceptions.HTTPException(status_code=403, detail="Application disabled")

    config = _as_dict(row["jwt_config"])
    algorithm = config.get("algorithm", "HS256")
    secret = _resolve_secret(config["secret_ref"])

    require_claims = ["exp"] if config.get("require_exp", True) else []
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[algorithm],  # pinned per app: rejects alg=none and confusion
            options={"require": require_claims},
            issuer=config.get("issuer") or None,
            audience=config.get("audience") or None,
            leeway=int(config.get("leeway_seconds", 30)),
        )
    except jwt.PyJWTError as exc:
        raise Auth.exceptions.HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc

    claim = config.get("user_id_claim", "user_id")
    user_id = payload.get(claim)
    if not isinstance(user_id, str) or not user_id:
        raise Auth.exceptions.HTTPException(status_code=401, detail=f"Missing or invalid claim: {claim}")

    app_claim = config.get("app_id_claim")
    if app_claim and payload.get(app_claim) != app_id:
        raise Auth.exceptions.HTTPException(status_code=401, detail="Token does not belong to this application")

    return {
        "identity": f"{app_id}:{user_id}",
        "display_name": user_id,
        "app_id": app_id,
        "user_id": user_id,
        "allowed_agents": list(row["allowed_agents"]),
        "allowed_workflows": list(row["allowed_workflows"]),
    }


@auth.on.threads.create
async def inject_scope(ctx: Any, value: dict[str, Any]) -> dict[str, Any]:
    """Stamp app/user scope into thread metadata as a second isolation layer."""
    metadata = dict(value.get("metadata") or {})
    metadata["app_id"] = ctx.user.app_id
    metadata["user_id"] = ctx.user.user_id
    value["metadata"] = metadata
    return value


@auth.on.threads.search
async def filter_search(ctx: Any, value: dict[str, Any]) -> dict[str, Any]:
    return {"metadata": {"app_id": ctx.user.app_id}}


@auth.on.threads.read
async def filter_read(ctx: Any, value: dict[str, Any]) -> dict[str, Any]:
    return {"metadata": {"app_id": ctx.user.app_id}}


@auth.on.threads.delete
async def filter_delete(ctx: Any, value: dict[str, Any]) -> dict[str, Any]:
    return {"metadata": {"app_id": ctx.user.app_id}}
