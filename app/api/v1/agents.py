"""Agent listing and chat endpoints."""

import asyncio
import logging

from aegra_api.core.auth_deps import require_auth
from aegra_api.models.auth import User
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import agent_registry
from app.api.deps import (
    get_auth_context,
    get_runtime_adapter,
    get_runtime_credentials,
    get_task_launcher,
)
from app.api.errors import APIError
from app.api.sse import SSE_HEADERS, sse
from app.api.v1.helpers import ensure_agent_allowed, ensure_registered_agent
from app.api.v1.schemas import AgentOut, ChatRequest, ChatResponse, MessageOut
from app.auth.context import AuthContext
from app.auth.permissions import is_allowed
from app.database.session import get_session
from app.runtime.adapter import (
    RunFailed,
    RunFinished,
    RunStarted,
    RuntimeAdapter,
    RuntimeCredentials,
    TokenDelta,
    ToolCallEvent,
    ToolResultEvent,
)
from app.runtime.aegra import RuntimeCallError
from app.services.conversations import (
    ConversationService,
    last_ai_text,
    persist_assistant_message,
    title_from_message,
)
from app.services.multimodal import build_runtime_content, stored_user_text, title_text
from app.services.workflows import TaskLauncher

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agents"])


@router.get("/agents", response_model=list[AgentOut])
async def list_agents(user: User = Depends(require_auth)) -> list[AgentOut]:
    allowed = list(getattr(user, "allowed_agents", []))
    return [
        AgentOut(
            agent_id=agent.agent_id,
            name=agent.name,
            description=agent.description,
            tags=list(agent.tags),
        )
        for agent in agent_registry.list()
        if is_allowed(allowed, agent.agent_id)
    ]


@router.post("/agents/{agent_id}/chat", response_model=ChatResponse)
async def chat(
    agent_id: str,
    payload: ChatRequest,
    user: User = Depends(require_auth),
    context: AuthContext = Depends(get_auth_context),
    credentials: RuntimeCredentials = Depends(get_runtime_credentials),
    runtime: RuntimeAdapter = Depends(get_runtime_adapter),
    session: AsyncSession = Depends(get_session),
) -> ChatResponse:
    ensure_agent_allowed(user, agent_id)
    ensure_registered_agent(agent_id)

    service = ConversationService(session, context, credentials, runtime)
    conversation = await service.resolve(
        agent_id,
        payload.conversation_id,
        title=title_from_message(title_text(payload.message, len(payload.images))),
    )
    user_message = await service.add_user_message(
        conversation,
        stored_user_text(payload.message, len(payload.images)),
    )
    runtime_content = build_runtime_content(payload.message, payload.image_urls())

    try:
        result = await runtime.run_wait_on_thread(
            credentials,
            thread_id=conversation.thread_id,
            graph_id=agent_id,
            input={"messages": [{"role": "user", "content": runtime_content}]},
        )
    except RuntimeCallError as exc:
        logger.warning("Agent runtime call failed: %s", exc)
        raise APIError(502, "runtime_error", "Agent runtime call failed") from exc

    assistant_text = last_ai_text(result.state)
    try:
        assistant_message = await persist_assistant_message(
            context,
            conversation.id,
            assistant_text,
            run_id=result.run_id,
        )
    except Exception as exc:
        logger.exception("Failed to persist assistant message")
        raise APIError(500, "persistence_error", "Failed to persist assistant message") from exc

    return ChatResponse(
        conversation_id=conversation.id,
        user_message=MessageOut.model_validate(user_message),
        assistant_message=MessageOut.model_validate(assistant_message) if assistant_message else None,
        run_id=result.run_id,
    )


@router.post("/agents/{agent_id}/chat/stream")
async def chat_stream(
    agent_id: str,
    payload: ChatRequest,
    user: User = Depends(require_auth),
    context: AuthContext = Depends(get_auth_context),
    credentials: RuntimeCredentials = Depends(get_runtime_credentials),
    runtime: RuntimeAdapter = Depends(get_runtime_adapter),
    session: AsyncSession = Depends(get_session),
    launcher: TaskLauncher = Depends(get_task_launcher),
) -> StreamingResponse:
    ensure_agent_allowed(user, agent_id)
    ensure_registered_agent(agent_id)

    service = ConversationService(session, context, credentials, runtime)
    conversation = await service.resolve(
        agent_id,
        payload.conversation_id,
        title=title_from_message(title_text(payload.message, len(payload.images))),
    )
    user_message = await service.add_user_message(
        conversation,
        stored_user_text(payload.message, len(payload.images)),
    )
    runtime_content = build_runtime_content(payload.message, payload.image_urls())

    async def events():
        yield sse(
            "meta",
            {
                "conversation_id": str(conversation.id),
                "thread_id": conversation.thread_id,
                "user_message_id": str(user_message.id),
                "agent_id": agent_id,
            },
        )

        chunks: list[str] = []
        run_id: str | None = None
        failed = False

        try:
            async for event in runtime.stream_thread_run(
                credentials,
                thread_id=conversation.thread_id,
                graph_id=agent_id,
                input={"messages": [{"role": "user", "content": runtime_content}]},
            ):
                if isinstance(event, RunStarted):
                    run_id = event.run_id
                elif isinstance(event, TokenDelta):
                    chunks.append(event.text)
                    yield sse("token", {"delta": event.text})
                elif isinstance(event, ToolCallEvent):
                    yield sse(
                        "tool_call",
                        {"call_id": event.call_id, "name": event.name, "arguments": event.arguments},
                    )
                elif isinstance(event, ToolResultEvent):
                    yield sse(
                        "tool_result",
                        {"call_id": event.call_id, "name": event.name, "content": event.content},
                    )
                elif isinstance(event, RunFailed):
                    failed = True
                    logger.warning("Agent run failed: %s", event.message)
                    yield sse("error", {"code": event.code, "message": "Agent run failed"})
                    break
                elif isinstance(event, RunFinished):
                    break
        except (asyncio.CancelledError, GeneratorExit):
            # The client closed the connection mid-stream. The runtime cancels
            # the run by default (`on_disconnect=cancel`), and whatever was
            # already sent to the client is kept as a partial message.
            # Scheduled rather than awaited: yielding is illegal while the
            # generator is being closed.
            if chunks:
                launcher.launch(
                    f"sse-partial-{conversation.id}",
                    persist_assistant_message(
                        context,
                        conversation.id,
                        "".join(chunks),
                        status="partial",
                        run_id=run_id,
                    ),
                )
            raise
        except RuntimeCallError as exc:
            failed = True
            logger.warning("Agent stream failed: %s", exc)
            yield sse("error", {"code": "runtime_error", "message": "Agent runtime call failed"})
        except Exception:
            failed = True
            logger.exception("Unexpected error during agent stream")
            yield sse("error", {"code": "internal_error", "message": "Agent run failed"})

        content = "".join(chunks)

        if failed:
            if content:
                try:
                    await persist_assistant_message(
                        context,
                        conversation.id,
                        content,
                        status="partial",
                        run_id=run_id,
                    )
                except Exception:
                    logger.exception("Failed to persist partial assistant message")
            return

        try:
            assistant_message = await persist_assistant_message(
                context,
                conversation.id,
                content,
                run_id=run_id,
            )
        except Exception:
            logger.exception("Failed to persist assistant message")
            yield sse("error", {"code": "persistence_error", "message": "Failed to persist assistant message"})
            return

        yield sse(
            "done",
            {
                "conversation_id": str(conversation.id),
                "message_id": str(assistant_message.id) if assistant_message else None,
                "run_id": run_id,
                "finish_reason": "stop",
            },
        )

    return StreamingResponse(events(), media_type="text/event-stream", headers=SSE_HEADERS)
