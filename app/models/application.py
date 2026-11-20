"""Registered business applications and their JWT configuration."""

from typing import Any

from sqlalchemy import Boolean, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Application(Base, TimestampMixin):
    __tablename__ = "applications"

    app_id: Mapped[str] = mapped_column(Text, primary_key=True)
    app_name: Mapped[str] = mapped_column(Text, nullable=False)
    auth_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="jwt",
        server_default=text("'jwt'"),
    )
    jwt_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    allowed_agents: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default=text("'{}'::text[]"),
    )
    allowed_workflows: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default=text("'{}'::text[]"),
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
