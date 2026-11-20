"""Aegra authentication bridge.

Aegra calls ``authenticate`` with request headers only. We use ``X-App-Id`` to
select an application configuration, verify the token with that application's
key, and return an identity namespaced as ``app_id:user_id``.
"""

import logging
from typing import Any

from langgraph_sdk import Auth

from app.auth.context import AuthContext
from app.auth.errors import AuthError
from app.auth.jwt import verify_token
from app.auth.store import application_store

logger = logging.getLogger(__name__)

auth = Auth()


def _http_error(status_code: int, detail: str) -> Exception:
    return Auth.exceptions.HTTPException(status_code=status_code, detail=detail)


def _lower_headers(headers: dict[str, Any]) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in headers.items()}


def _bearer_token(headers: dict[str, str]) -> str | None:
    authorization = headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


@auth.authenticate
async def authenticate(headers: dict[str, Any]) -> dict[str, Any]:
    lowered = _lower_headers(headers)

    app_id = lowered.get("x-app-id")
    if not app_id:
        raise _http_error(401, "Missing X-App-Id header")

    token = _bearer_token(lowered)
    if token is None:
        raise _http_error(401, "Missing bearer token")

    record = await application_store.get(app_id)
    if record is None:
        # Same message as an invalid token: do not confirm app existence.
        raise _http_error(401, "Invalid credentials")

    if not record.enabled:
        raise _http_error(403, "Application disabled")

    try:
        user_id = verify_token(token, record.jwt, expected_app_id=app_id)
    except AuthError as exc:
        logger.info("Authentication rejected for app=%s: %s", app_id, exc.code)
        raise _http_error(exc.status_code, exc.message) from exc

    context = AuthContext(app_id=app_id, user_id=user_id)

    return {
        "identity": context.identity,
        "display_name": user_id,
        "app_id": app_id,
        "user_id": user_id,
        "allowed_agents": list(record.allowed_agents),
        "allowed_workflows": list(record.allowed_workflows),
        # Carried on the authenticated principal so quota checks need no extra
        # database round-trip per request.
        "rate_limit_per_minute": record.rate_limit_per_minute,
        "daily_request_quota": record.daily_request_quota,
        "max_concurrent_workflow_runs": record.max_concurrent_workflow_runs,
    }


def _user_app_id(ctx: Any) -> str | None:
    return getattr(ctx.user, "app_id", None)


@auth.on.threads.create
async def inject_thread_scope(ctx: Any, value: Any) -> Any:
    """Stamp the tenant scope into thread metadata as a second isolation layer."""
    if not isinstance(value, dict):
        return False

    app_id = _user_app_id(ctx)
    user_id = getattr(ctx.user, "user_id", None)
    if not app_id or not user_id:
        return False

    metadata = dict(value.get("metadata") or {})
    metadata["app_id"] = app_id
    metadata["user_id"] = user_id
    value["metadata"] = metadata
    return value


@auth.on.threads.search
async def filter_thread_search(ctx: Any, value: Any) -> Any:
    app_id = _user_app_id(ctx)
    if not app_id:
        return False
    return {"metadata": {"app_id": app_id}}


@auth.on.threads.read
async def filter_thread_read(ctx: Any, value: Any) -> Any:
    app_id = _user_app_id(ctx)
    if not app_id:
        return False
    return {"metadata": {"app_id": app_id}}


@auth.on.threads.delete
async def filter_thread_delete(ctx: Any, value: Any) -> Any:
    app_id = _user_app_id(ctx)
    if not app_id:
        return False
    return {"metadata": {"app_id": app_id}}
