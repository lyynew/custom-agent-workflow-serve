"""Conversation and message repositories.

Messages can only be reached through an owned conversation, so a leaked
`conversation_id` is not enough to read another tenant's history.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import AuthContext
from app.models.conversation import Conversation
from app.models.message import ConversationMessage
from app.repositories.base import ScopedRepository

MAX_PAGE_SIZE = 100


class ConversationRepository(ScopedRepository[Conversation]):
    model = Conversation

    async def create(
        self,
        *,
        agent_id: str,
        thread_id: str,
        title: str | None = None,
    ) -> Conversation:
        conversation = Conversation(
            app_id=self.context.app_id,
            user_id=self.context.user_id,
            agent_id=agent_id,
            thread_id=thread_id,
            title=title,
        )
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    async def get(self, resource_id: UUID | str) -> Conversation | None:
        statement = self._scoped_select().where(
            Conversation.id == resource_id,
            Conversation.deleted_at.is_(None),
        )
        return (await self.session.scalars(statement)).one_or_none()

    async def list(
        self,
        *,
        agent_id: str | None = None,
        limit: int = 20,
        before: datetime | None = None,
    ) -> Sequence[Conversation]:
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        statement = self._scoped_select().where(Conversation.deleted_at.is_(None))

        if agent_id is not None:
            statement = statement.where(Conversation.agent_id == agent_id)
        if before is not None:
            statement = statement.where(Conversation.created_at < before)

        statement = statement.order_by(Conversation.created_at.desc()).limit(limit)
        return list((await self.session.scalars(statement)).all())

    async def soft_delete(self, resource_id: UUID | str) -> Conversation:
        conversation = await self.require(resource_id)
        conversation.deleted_at = datetime.now(UTC)
        await self.session.flush()
        return conversation


class MessageRepository:
    def __init__(self, session: AsyncSession, context: AuthContext) -> None:
        self._session = session
        self._context = context
        self._conversations = ConversationRepository(session, context)

    async def add(
        self,
        conversation_id: UUID | str,
        *,
        role: str,
        content: str,
        status: str = "complete",
        run_id: str | None = None,
    ) -> ConversationMessage:
        conversation = await self._conversations.require(conversation_id)

        message = ConversationMessage(
            conversation_id=conversation.id,
            app_id=self._context.app_id,
            user_id=self._context.user_id,
            role=role,
            content=content,
            status=status,
            run_id=run_id,
        )
        self._session.add(message)
        conversation.updated_at = datetime.now(UTC)
        await self._session.flush()
        return message

    async def list(
        self,
        conversation_id: UUID | str,
        *,
        limit: int = 50,
        before: datetime | None = None,
    ) -> Sequence[ConversationMessage]:
        # Ownership check first: the ID alone is never sufficient.
        await self._conversations.require(conversation_id)

        limit = max(1, min(limit, MAX_PAGE_SIZE))
        statement = select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.app_id == self._context.app_id,
            ConversationMessage.user_id == self._context.user_id,
        )
        if before is not None:
            statement = statement.where(ConversationMessage.created_at < before)

        statement = statement.order_by(ConversationMessage.created_at.asc()).limit(limit)
        return list((await self._session.scalars(statement)).all())
