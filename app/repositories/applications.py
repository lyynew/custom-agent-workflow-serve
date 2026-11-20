"""System-level access to application registrations.

This repository is not scoped to an AuthContext because it is used by
bootstrapping, the CLI, and the auth cache — never by a business request
acting on another tenant's data.
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.store import ApplicationConfig
from app.models.application import Application


class ApplicationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[Application]:
        return list((await self._session.scalars(select(Application).order_by(Application.app_id))).all())

    async def get(self, app_id: str) -> Application | None:
        return await self._session.get(Application, app_id)

    async def upsert(self, config: ApplicationConfig) -> Application:
        row = await self._session.get(Application, config.app_id)
        if row is None:
            row = Application(app_id=config.app_id)
            self._session.add(row)

        row.app_name = config.app_name
        row.auth_type = config.auth_type
        row.jwt_config = config.jwt.model_dump(exclude_none=True)
        row.allowed_agents = list(config.allowed_agents)
        row.allowed_workflows = list(config.allowed_workflows)
        row.enabled = config.enabled
        row.rate_limit_per_minute = config.rate_limit_per_minute
        row.daily_request_quota = config.daily_request_quota
        row.max_concurrent_workflow_runs = config.max_concurrent_workflow_runs

        await self._session.flush()
        return row

    async def delete(self, app_id: str) -> bool:
        result = await self._session.execute(delete(Application).where(Application.app_id == app_id))
        return bool(result.rowcount)
