"""Conversation orchestration: thread creation, message persistence, title."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import AuthContext
from app.database.session import get_session_factory
from app.models.conversation import Conversation
from app.models.message import ConversationMessage
from app.repositories.conversations import ConversationRepository, MessageRepository
from app.repositories.errors import ResourceNotFound
from app.runtime.adapter import RuntimeAdapter, RuntimeCredentials

logger = logging.getLogger(__name__)


def title_from_message(message: str, limit: int = 50) -> str | None:
    normalized = " ".join(message.split())
    return normalized[:limit] or None


def last_ai_text(state: dict[str, Any]) -> str:
    """Extract the final assistant text from a LangGraph MessagesState."""
    messages = state.get("messages")
    if not isinstance(messages, list):
        return ""

    for item in reversed(messages):
        if not isinstance(item, dict):
            continue
        if item.get("type") not in {"ai", "AIMessage"} and item.get("role") != "assistant":
            continue

        content = item.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return ""

    return ""


class ConversationService:
    def __init__(
        self,
        session: AsyncSession,
        context: AuthContext,
        credentials: RuntimeCredentials,
        runtime: RuntimeAdapter,
    ) -> None:
        self._session = session
        self._context = context
        self._credentials = credentials
        self._runtime = runtime

    async def create(self, agent_id: str, title: str | None = None) -> Conversation:
        thread_id = await self._runtime.create_thread(
            self._credentials,
            metadata={"agent_id": agent_id},
        )

        repository = ConversationRepository(self._session, self._context)
        conversation = await repository.create(
            agent_id=agent_id,
            thread_id=thread_id,
            title=title,
        )

        try:
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            try:
                await self._runtime.delete_thread(self._credentials, thread_id)
            except Exception:
                logger.warning("Failed to clean up thread %s after DB error", thread_id, exc_info=True)
            raise

        return conversation

    async def resolve(
        self,
        agent_id: str,
        conversation_id: UUID | None,
        title: str | None = None,
    ) -> Conversation:
        if conversation_id is None:
            return await self.create(agent_id, title=title)

        repository = ConversationRepository(self._session, self._context)
        conversation = await repository.require(conversation_id)

        if conversation.agent_id != agent_id:
            # Do not reveal that the conversation exists under another agent.
            raise ResourceNotFound("Conversation", str(conversation_id))

        return conversation

    async def add_user_message(
        self,
        conversation: Conversation,
        content: str,
    ) -> ConversationMessage:
        messages = MessageRepository(self._session, self._context)
        message = await messages.add(conversation.id, role="user", content=content)
        await self._session.commit()
        return message


async def persist_assistant_message(
    context: AuthContext,
    conversation_id: UUID,
    content: str,
    *,
    status: str = "complete",
    run_id: str | None = None,
) -> ConversationMessage | None:
    """Persist the assistant reply in a fresh session.

    Streaming responses outlive the request-scoped session, so this deliberately
    opens its own session after the stream finishes.
    """
    if not content and status == "complete":
        return None

    async with get_session_factory()() as session:
        messages = MessageRepository(session, context)
        message = await messages.add(
            conversation_id,
            role="assistant",
            content=content,
            status=status,
            run_id=run_id,
        )
        await session.commit()
        return message
