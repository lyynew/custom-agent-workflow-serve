from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from app.services.multimodal import (
    MAX_IMAGES_PER_MESSAGE,
    ImageInputError,
    normalise_image,
)


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


class ImageInput(BaseModel):
    """一张待分析的图片：`url` 与 `base64` 二选一。

    - `url`：公网可访问的 http/https 地址，模型服务自己去取；
    - `base64`：图片字节的 base64，可带 `data:image/png;base64,` 前缀；不写
      `mime_type` 时按文件头自动识别（jpeg/png/webp/gif，单张上限 5MB）。
    """

    url: str | None = None
    base64: str | None = None
    mime_type: str | None = None

    #: 归一化后的地址（私有属性，不进 schema、不进响应），供运行时使用。
    _data_url: str = PrivateAttr(default="")

    @model_validator(mode="after")
    def _normalise(self) -> "ImageInput":
        try:
            self._data_url = normalise_image(url=self.url, base64_data=self.base64, mime_type=self.mime_type)
        except ImageInputError as exc:
            raise ValueError(str(exc)) from exc
        return self

    @property
    def data_url(self) -> str:
        return self._data_url


class ChatRequest(BaseModel):
    """一次用户输入。`message` 与 `images` 至少要有一个。"""

    message: str = Field(default="", max_length=32_000)
    images: list[ImageInput] = Field(default_factory=list, max_length=MAX_IMAGES_PER_MESSAGE)
    conversation_id: UUID | None = None

    @model_validator(mode="after")
    def _needs_text_or_image(self) -> "ChatRequest":
        if not self.message.strip() and not self.images:
            raise ValueError("message 与 images 至少要提供一个")
        return self

    def image_urls(self) -> list[str]:
        return [image.data_url for image in self.images]


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
    #: Last time a poller observed this run; a stale value on a `running` run
    #: means its poller died (the next read reconciles it).
    heartbeat_at: datetime | None = None


class WorkflowRunList(BaseModel):
    items: list[WorkflowRunOut]
    next_before: datetime | None = None


class WorkflowResultOut(BaseModel):
    run_id: UUID
    workflow_id: str
    status: str
    output: dict[str, Any]
