"""System endpoints used to verify identity and application scope."""

from typing import Any

from aegra_api.core.auth_deps import require_auth
from aegra_api.models.auth import User
from fastapi import APIRouter, Depends

from app.api.deps import get_auth_context
from app.api.errors import APIError
from app.auth.context import AuthContext
from app.auth.store import application_store

router = APIRouter(tags=["system"])


@router.get("/whoami")
async def whoami(
    user: User = Depends(require_auth),
    context: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    return {
        "identity": context.identity,
        "app_id": context.app_id,
        "user_id": context.user_id,
        "identity_provider": context.identity_provider,
        "allowed_agents": list(getattr(user, "allowed_agents", [])),
        "allowed_workflows": list(getattr(user, "allowed_workflows", [])),
    }


@router.get("/apps/me")
async def current_application(context: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    record = await application_store.get(context.app_id)
    if record is None:
        raise APIError(404, "application_not_found", "Application is no longer registered")

    return {
        "app_id": record.app_id,
        "app_name": record.app_name,
        "auth_type": record.auth_type,
        "allowed_agents": list(record.allowed_agents),
        "allowed_workflows": list(record.allowed_workflows),
    }
