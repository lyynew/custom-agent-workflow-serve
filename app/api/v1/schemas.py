from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentOut(BaseModel):
    agent_id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class ConversationCreate(BaseModel):
    agent_id: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=200)


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: str
    thread_id: str
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: str
    content: str
    status: str
    created_at: datetime


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32_000)
    conversation_id: UUID | None = None


class ChatResponse(BaseModel):
    conversation_id: UUID
    user_message: MessageOut
    assistant_message: MessageOut | None
    run_id: str | None


class ConversationList(BaseModel):
    items: list[ConversationOut]
    next_before: datetime | None = None


class MessageList(BaseModel):
    items: list[MessageOut]
    next_before: datetime | None = None
