"""审计日志读写。

和业务仓储不同，这里**按应用维度**过滤，不按用户维度：运维要能看到"这个应用今天
有哪些人调了什么"，按用户过滤会让同一租户下的同事互相看不见。租户隔离仍在，
`app_id` 永远来自已认证的 `AuthContext`，不来自请求参数。
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import AuthContext
from app.models.audit_log import AuditLog

MAX_PAGE_SIZE = 200


def _status_range(status: str | None) -> tuple[int, int] | None:
    if not status:
        return None
    ranges = {"2xx": (200, 299), "3xx": (300, 399), "4xx": (400, 499), "5xx": (500, 599)}
    return ranges.get(status.lower())


class AuditLogRepository:
    def __init__(self, session: AsyncSession, context: AuthContext) -> None:
        self._session = session
        self._app_id = context.app_id

    def _base(self) -> Select[Any]:
        return select(AuditLog).where(AuditLog.app_id == self._app_id)

    async def list(
        self,
        *,
        status: str | None = None,
        method: str | None = None,
        path: str | None = None,
        since: datetime | None = None,
        before: datetime | None = None,
        limit: int = 50,
    ) -> Sequence[AuditLog]:
        statement = self._base()

        span = _status_range(status)
        if span is not None:
            statement = statement.where(AuditLog.status_code.between(*span))
        if method:
            statement = statement.where(func.upper(AuditLog.method) == method.upper())
        if path:
            # 前缀匹配就够了：调用方通常是按 /api/v1/agents 这样的段筛。
            statement = statement.where(AuditLog.path.startswith(path, autoescape=True))
        if since is not None:
            statement = statement.where(AuditLog.created_at >= since)
        if before is not None:
            statement = statement.where(AuditLog.created_at < before)

        statement = statement.order_by(AuditLog.created_at.desc()).limit(min(limit, MAX_PAGE_SIZE))
        return (await self._session.scalars(statement)).all()

    async def summary(self, *, hours: int = 24) -> dict[str, Any]:
        since = datetime.now(UTC) - timedelta(hours=hours)
        statement = select(
            func.count().label("requests"),
            func.count().filter(AuditLog.status_code >= 500).label("server_errors"),
            func.count().filter(AuditLog.status_code.between(400, 499)).label("client_errors"),
            func.count(func.distinct(AuditLog.user_id)).label("users"),
            func.coalesce(func.avg(AuditLog.duration_ms), 0).label("avg_ms"),
            func.coalesce(
                func.percentile_cont(0.95).within_group(AuditLog.duration_ms),
                0,
            ).label("p95_ms"),
        ).where(AuditLog.app_id == self._app_id, AuditLog.created_at >= since)

        row = (await self._session.execute(statement)).one()
        requests = int(row.requests or 0)
        errors = int(row.server_errors or 0) + int(row.client_errors or 0)

        return {
            "window_hours": hours,
            "requests": requests,
            "errors": errors,
            "server_errors": int(row.server_errors or 0),
            "client_errors": int(row.client_errors or 0),
            "error_rate": round(errors / requests, 4) if requests else 0.0,
            "users": int(row.users or 0),
            "avg_ms": round(float(row.avg_ms or 0)),
            "p95_ms": round(float(row.p95_ms or 0)),
        }

    async def prune(self, *, days: int) -> int:
        """删除早于 N 天的记录，返回删除行数（给运维的保留策略用）。"""
        cutoff = datetime.now(UTC) - timedelta(days=days)
        result = await self._session.execute(
            delete(AuditLog).where(AuditLog.app_id == self._app_id, AuditLog.created_at < cutoff)
        )
        return int(result.rowcount or 0)
