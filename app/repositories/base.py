"""Repository base class that makes unscoped access awkward by design.

Every query starts from `(app_id, user_id)`. There is intentionally no
`get_by_id_only` method: a resource ID never authorizes access on its own.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import AuthContext
from app.models.base import Base
from app.repositories.errors import ResourceNotFound


class ScopedRepository[ModelT: Base]:
    model: type[ModelT]

    def __init__(self, session: AsyncSession, context: AuthContext) -> None:
        if not context.app_id or not context.user_id:
            raise ValueError("AuthContext must contain app_id and user_id")
        self._session = session
        self._context = context

    @property
    def session(self) -> AsyncSession:
        return self._session

    @property
    def context(self) -> AuthContext:
        return self._context

    def _scoped_select(self) -> Select[Any]:
        return select(self.model).where(
            self.model.app_id == self._context.app_id,  # type: ignore[attr-defined]
            self.model.user_id == self._context.user_id,  # type: ignore[attr-defined]
        )

    async def get(self, resource_id: UUID | str) -> ModelT | None:
        statement = self._scoped_select().where(self.model.id == resource_id)  # type: ignore[attr-defined]
        return (await self._session.scalars(statement)).one_or_none()

    async def require(self, resource_id: UUID | str) -> ModelT:
        resource = await self.get(resource_id)
        if resource is None:
            raise ResourceNotFound(self.model.__name__, str(resource_id))
        return resource
