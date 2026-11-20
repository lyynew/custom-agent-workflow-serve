from datetime import datetime
from typing import Any, Literal
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


class WorkflowOut(BaseModel):
    workflow_id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)


class WorkflowExecuteRequest(BaseModel):
    #: `async` returns 202 and a run id; `sync` waits inside the request.
    mode: Literal["sync", "async"] = "async"
    input: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: UUID = Field(validation_alias="id")
    workflow_id: str
    status: str
    output: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class WorkflowRunList(BaseModel):
    items: list[WorkflowRunOut]
    next_before: datetime | None = None


class WorkflowResultOut(BaseModel):
    run_id: UUID
    workflow_id: str
    status: str
    output: dict[str, Any]
