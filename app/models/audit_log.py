"""Request audit trail: one row per authenticated business request.

Deliberately **not** a log of payloads. Bodies are never stored — they can carry
image bytes, prompts and business data, and an audit table is the last place you
want a copy of them. What is stored answers "谁、什么时候、打了哪个接口、结果如何、
花了多久".
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        # 列表默认按「本应用 + 时间倒序」翻页，这条索引正好覆盖。
        Index("ix_audit_logs_app_created", "app_id", "created_at"),
        Index("ix_audit_logs_app_status", "app_id", "status_code", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: 租户隔离键；审计是应用级可见，不是用户级。
    app_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    #: 与访问日志的 request_id 对齐，方便两边串起来查。
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 整条请求耗时；流式请求是"直到响应写完"，所以等于整轮对话时长。
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    client_ip: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
