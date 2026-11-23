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
from app.services.workflows import TaskLauncher, task_launcher


async def get_auth_context(user: User = Depends(require_auth)) -> AuthContext:
    app_id = getattr(user, "app_id", None)
    user_id = getattr(user, "user_id", None)

    if not isinstance(app_id, str) or not app_id or not isinstance(user_id, str) or not user_id:
        raise APIError(401, "invalid_auth_context", "Authenticated identity is missing application scope")

    return AuthContext(app_id=app_id, user_id=user_id)


async def mark_audit(request: Request, user: User = Depends(require_auth)) -> None:
    """把身份写进 `scope["state"]`，供审计中间件读取。

    中间件拿不到 FastAPI 的依赖注入结果，只能读 scope；而 `Request.state` 背后就是
    `scope["state"]`，两边因此能对上。

    放在路由级依赖里而不是 `get_auth_context` 里：有些路由（如 `/agents`）只需要
    `require_auth`，不走 `get_auth_context`，那样会漏记。
    """
    app_id = getattr(user, "app_id", None)
    user_id = getattr(user, "user_id", None)
    if not isinstance(app_id, str) or not app_id or not isinstance(user_id, str) or not user_id:
        return

    # 只放身份与元信息：绝不存请求体、Authorization 或 token。
    request.state.audit = {
        "app_id": app_id,
        "user_id": user_id,
        "request_id": request.headers.get("x-request-id"),
        "method": request.method,
        "path": request.url.path,
        "client_ip": request.client.host[:64] if request.client else None,
        "user_agent": (request.headers.get("user-agent") or "")[:200] or None,
    }


@lru_cache
def get_runtime_adapter() -> RuntimeAdapter:
    return AegraRuntimeAdapter(get_settings().self_url)


def get_task_launcher() -> TaskLauncher:
    """Background launcher for async workflow execution.

    A single instance is shared so tests can swap it for one that records the
    coroutines instead of racing them.
    """
    return task_launcher


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
