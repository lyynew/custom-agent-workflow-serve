"""Cached application configuration.

The auth handler runs on every request, so configuration is cached per app with
a short TTL. `warmup()` validates every `secret_ref` at startup and fails fast
instead of letting the first business request discover a broken secret.
"""

import logging
from time import monotonic

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError

from app.auth.errors import SecretResolutionError
from app.auth.jwt import JwtConfig, resolve_key
from app.config import get_settings
from app.database.session import get_session_factory
from app.models.application import Application

logger = logging.getLogger(__name__)


class ApplicationConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    app_id: str
    app_name: str
    auth_type: str = "jwt"
    jwt: JwtConfig
    allowed_agents: tuple[str, ...] = ()
    allowed_workflows: tuple[str, ...] = ()
    enabled: bool = True
    #: NULL means unlimited.
    rate_limit_per_minute: int | None = None
    daily_request_quota: int | None = None
    max_concurrent_workflow_runs: int | None = None

    @classmethod
    def from_row(cls, row: Application) -> "ApplicationConfig":
        return cls(
            app_id=row.app_id,
            app_name=row.app_name,
            auth_type=row.auth_type,
            jwt=JwtConfig.model_validate(row.jwt_config),
            allowed_agents=tuple(row.allowed_agents or ()),
            allowed_workflows=tuple(row.allowed_workflows or ()),
            enabled=row.enabled,
            rate_limit_per_minute=row.rate_limit_per_minute,
            daily_request_quota=row.daily_request_quota,
            max_concurrent_workflow_runs=row.max_concurrent_workflow_runs,
        )


class ApplicationStore:
    def __init__(self, ttl_seconds: int | None = None) -> None:
        self._ttl = get_settings().auth_cache_ttl_seconds if ttl_seconds is None else ttl_seconds
        self._cache: dict[str, tuple[float, ApplicationConfig]] = {}

    async def get(self, app_id: str) -> ApplicationConfig | None:
        now = monotonic()
        cached = self._cache.get(app_id)
        if cached is not None and cached[0] > now:
            return cached[1]

        async with get_session_factory()() as session:
            row = await session.get(Application, app_id)

        if row is None:
            self._cache.pop(app_id, None)
            return None

        config = ApplicationConfig.from_row(row)
        self._cache[app_id] = (now + self._ttl, config)
        return config

    async def warmup(self) -> None:
        """Load all applications and validate their key references."""
        try:
            async with get_session_factory()() as session:
                rows = (await session.scalars(select(Application).order_by(Application.app_id))).all()
        except ProgrammingError as exc:
            raise RuntimeError(
                "biz schema is not migrated. Run `alembic upgrade head` before starting the service."
            ) from exc

        configs = [ApplicationConfig.from_row(row) for row in rows]

        for config in configs:
            try:
                resolve_key(config.jwt)
            except SecretResolutionError as exc:
                raise RuntimeError(
                    f"Application '{config.app_id}' has an unresolvable key reference: {exc}"
                ) from exc

        expires_at = monotonic() + self._ttl
        self._cache = {config.app_id: (expires_at, config) for config in configs}
        logger.info("Loaded %d application(s) into the auth cache", len(configs))

    def clear(self) -> None:
        self._cache.clear()

    async def reload(self) -> None:
        self.clear()
        await self.warmup()

    @property
    def cached_app_ids(self) -> frozenset[str]:
        return frozenset(self._cache)


application_store = ApplicationStore()
