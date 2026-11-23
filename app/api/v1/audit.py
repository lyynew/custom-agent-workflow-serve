"""审计日志查询：列表 + 概览。

租户级可见（同应用下的人能看到彼此调用了什么），跨应用仍被 `app_id` 隔离。
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_auth_context
from app.api.v1.schemas import AuditLogList, AuditLogOut, AuditSummary
from app.auth.context import AuthContext
from app.database.session import get_session
from app.repositories.audit import AuditLogRepository

router = APIRouter(tags=["audit"])

StatusClass = Literal["2xx", "3xx", "4xx", "5xx"]


@router.get("/audit/logs", response_model=AuditLogList)
async def list_audit_logs(
    status: StatusClass | None = None,
    method: str | None = Query(default=None, max_length=10),
    path: str | None = Query(default=None, max_length=200),
    since: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    before: datetime | None = None,
    context: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> AuditLogList:
    repository = AuditLogRepository(session, context)
    items = await repository.list(
        status=status,
        method=method,
        path=path,
        since=since,
        before=before,
        limit=limit,
    )
    return AuditLogList(
        items=[AuditLogOut.model_validate(item) for item in items],
        next_before=items[-1].created_at if len(items) == limit and items else None,
    )


@router.get("/audit/summary", response_model=AuditSummary)
async def audit_summary(
    hours: int = Query(default=24, ge=1, le=24 * 30),
    context: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> AuditSummary:
    return AuditSummary(**await AuditLogRepository(session, context).summary(hours=hours))
