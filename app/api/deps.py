"""Shared FastAPI dependencies.

`get_auth_context` is the only place that converts Aegra's `User` into the
domain `AuthContext`. Business code must not read Aegra user fields directly.
"""

from functools import lru_cache

from aegra_api.core.auth_deps import require_auth
from aegra_api.models.auth import User
from fastapi import Depends, Request

from app.api.errors import APIError
from app.auth.context import AuthContext
from app.config import get_settings
from app.database.session import get_session
from app.repositories.conversations import ConversationRepository, MessageRepository
from app.runtime.adapter import RuntimeAdapter, RuntimeCredentials
from app.runtime.aegra import AegraRuntimeAdapter


async def get_auth_context(user: User = Depends(require_auth)) -> AuthContext:
    app_id = getattr(user, "app_id", None)
    user_id = getattr(user, "user_id", None)

    if not isinstance(app_id, str) or not app_id or not isinstance(user_id, str) or not user_id:
        raise APIError(401, "invalid_auth_context", "Authenticated identity is missing application scope")

    return AuthContext(app_id=app_id, user_id=user_id)


@lru_cache
def get_runtime_adapter() -> RuntimeAdapter:
    return AegraRuntimeAdapter(get_settings().self_url)


def get_runtime_credentials(
    request: Request,
    context: AuthContext = Depends(get_auth_context),
) -> RuntimeCredentials:
    authorization = request.headers.get("authorization")
    if not authorization:
        raise APIError(401, "missing_authorization", "Missing Authorization header")
    return RuntimeCredentials(app_id=context.app_id, authorization=authorization)


async def get_conversation_repository(
    context: AuthContext = Depends(get_auth_context),
    session=Depends(get_session),
) -> ConversationRepository:
    return ConversationRepository(session, context)


async def get_message_repository(
    context: AuthContext = Depends(get_auth_context),
    session=Depends(get_session),
) -> MessageRepository:
    return MessageRepository(session, context)
