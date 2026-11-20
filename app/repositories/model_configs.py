"""System-level access to model provider configuration.

Not scoped to an AuthContext: model configs are infrastructure shared by every
application, and they are never exposed through a business route.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model_config import ModelConfig


class ModelConfigRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[ModelConfig]:
        statement = select(ModelConfig).order_by(ModelConfig.name)
        return list((await self._session.scalars(statement)).all())

    async def get_by_name(self, name: str) -> ModelConfig | None:
        statement = select(ModelConfig).where(ModelConfig.name == name)
        return await self._session.scalar(statement)

    async def upsert(self, values: dict) -> ModelConfig:
        row = await self.get_by_name(values["name"])
        if row is None:
            row = ModelConfig(name=values["name"])
            self._session.add(row)

        for field in (
            "provider",
            "base_url",
            "model_name",
            "api_key_encrypted",
            "temperature",
            "max_tokens",
            "timeout_seconds",
            "extra",
            "enabled",
        ):
            if field in values:
                setattr(row, field, values[field])

        await self._session.flush()
        return row
