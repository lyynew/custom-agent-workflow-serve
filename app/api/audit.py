"""ASGI 中间件：把每个已认证的业务请求写进审计表。

为什么不是 `BaseHTTPMiddleware`：那个实现会包一层响应流，SSE 容易被它缓冲或改写；
纯 ASGI 只旁观 `http.response.start`，不动 body。

为什么耗时在 `await self.app(...)` 前后取：流式请求要等到响应真正写完才返回，所以
这个差值就是"整轮对话耗时"，而不是首字节时间。

为什么写库是后台任务：审计是旁路，不能因为它慢（或库抖）把用户请求拖住。代价是
进程被强杀时可能丢最后几条——对访问日志来说可以接受。
"""

import asyncio
import logging
import time
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.database.session import get_session_factory
from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)

AUDITED_PREFIX = "/api/v1/"
MAX_USER_AGENT = 200
MAX_IP = 64

#: 持有后台任务的强引用，避免事件循环回收未完成的任务。
_pending: set[asyncio.Task[None]] = set()


async def _persist(payload: dict[str, Any], status_code: int, duration_ms: int) -> None:
    try:
        async with get_session_factory()() as session:
            session.add(
                AuditLog(
                    app_id=payload["app_id"],
                    user_id=payload["user_id"],
                    request_id=payload.get("request_id"),
                    method=payload["method"],
                    path=payload["path"],
                    status_code=status_code,
                    duration_ms=duration_ms,
                    client_ip=payload.get("client_ip"),
                    user_agent=payload.get("user_agent"),
                )
            )
            await session.commit()
    except Exception:  # noqa: BLE001 - 审计失败不能影响业务
        logger.warning("Failed to write audit log for %s", payload.get("path"), exc_info=True)


def _schedule(payload: dict[str, Any], status_code: int, duration_ms: int) -> None:
    task = asyncio.create_task(_persist(payload, status_code, duration_ms))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


class AuditMiddleware:
    """只审计业务路由；未认证请求不落库（否则 401 可以被刷爆表）。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith(AUDITED_PREFIX):
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            state = scope.get("state") or {}
            payload = state.get("audit")
            if payload:
                duration_ms = int((time.perf_counter() - started) * 1000)
                _schedule(payload, status_code, duration_ms)
