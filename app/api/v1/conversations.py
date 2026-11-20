"""Conversation and message endpoints."""

import logging
from datetime import datetime
from uuid import UUID

from aegra_api.core.auth_deps import require_auth
from aegra_api.models.auth import User
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_auth_context,
    get_conversation_repository,
    get_message_repository,
    get_runtime_adapter,
    get_runtime_credentials,
)
from app.api.errors import APIError
from app.api.v1.helpers import ensure_agent_allowed, ensure_registered_agent
from app.api.v1.schemas import (
    ConversationCreate,
    ConversationList,
    ConversationOut,
    MessageList,
    MessageOut,
)
from app.auth.context import AuthContext
from app.database.session import get_session
from app.repositories.conversations import ConversationRepository, MessageRepository
from app.runtime.adapter import RuntimeAdapter, RuntimeCredentials
from app.runtime.aegra import RuntimeCallError
from app.services.conversations import ConversationService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["conversations"])


@router.post("/conversations", response_model=ConversationOut, status_code=201)
async def create_conversation(
    payload: ConversationCreate,
    user: User = Depends(require_auth),
    context: AuthContext = Depends(get_auth_context),
    credentials: RuntimeCredentials = Depends(get_runtime_credentials),
    runtime: RuntimeAdapter = Depends(get_runtime_adapter),
    session: AsyncSession = Depends(get_session),
) -> ConversationOut:
    ensure_agent_allowed(user, payload.agent_id)
    ensure_registered_agent(payload.agent_id)

    service = ConversationService(session, context, credentials, runtime)
    try:
        conversation = await service.create(payload.agent_id, title=payload.title)
    except RuntimeCallError as exc:
        logger.warning("Failed to create runtime thread: %s", exc)
        raise APIError(502, "runtime_error", "Failed to create conversation") from exc

    return ConversationOut.model_validate(conversation)


@router.get("/conversations", response_model=ConversationList)
async def list_conversations(
    agent_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    before: datetime | None = None,
    repository: ConversationRepository = Depends(get_conversation_repository),
) -> ConversationList:
    items = await repository.list(agent_id=agent_id, limit=limit, before=before)
    next_before = items[-1].created_at if len(items) == limit else None
    return ConversationList(
        items=[ConversationOut.model_validate(item) for item in items],
        next_before=next_before,
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: UUID,
    repository: ConversationRepository = Depends(get_conversation_repository),
) -> ConversationOut:
    conversation = await repository.require(conversation_id)
    return ConversationOut.model_validate(conversation)


@router.get("/conversations/{conversation_id}/messages", response_model=MessageList)
async def list_messages(
    conversation_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    before: datetime | None = None,
    repository: MessageRepository = Depends(get_message_repository),
) -> MessageList:
    messages = await repository.list(conversation_id, limit=limit, before=before)
    next_before = messages[-1].created_at if len(messages) == limit else None
    return MessageList(
        items=[MessageOut.model_validate(message) for message in messages],
        next_before=next_before,
    )


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: UUID,
    credentials: RuntimeCredentials = Depends(get_runtime_credentials),
    runtime: RuntimeAdapter = Depends(get_runtime_adapter),
    repository: ConversationRepository = Depends(get_conversation_repository),
    session: AsyncSession = Depends(get_session),
) -> Response:
    conversation = await repository.require(conversation_id)
    await repository.soft_delete(conversation_id)
    await session.commit()

    try:
        await runtime.delete_thread(credentials, conversation.thread_id)
    except RuntimeCallError as exc:
        # The business row is already hidden; a stale runtime thread is harmless.
        logger.warning("Failed to delete runtime thread %s: %s", conversation.thread_id, exc)

    return Response(status_code=204)
