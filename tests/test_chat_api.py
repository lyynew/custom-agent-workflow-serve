"""Sync chat, streaming chat, multi-turn, tool events and failure paths."""

import json

from aegra_api.models.auth import User

from app.runtime.adapter import (
    RunFailed,
    RunFinished,
    RunStarted,
    TokenDelta,
    ToolCallEvent,
    ToolResultEvent,
)


def _user(
    app_id: str = "agriculture_app",
    user_id: str = "10001",
    allowed_agents: list[str] | None = None,
) -> User:
    return User(
        identity=f"{app_id}:{user_id}",
        app_id=app_id,
        user_id=user_id,
        allowed_agents=["*"] if allowed_agents is None else allowed_agents,
        allowed_workflows=["*"],
    )


def _sse_events(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    name: str | None = None
    data: list[str] = []

    for line in text.splitlines():
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif line == "":
            if name is not None:
                events.append((name, json.loads("\n".join(data) or "{}")))
            name, data = None, []

    if name is not None:
        events.append((name, json.loads("\n".join(data) or "{}")))

    return events


async def test_sync_chat_persists_user_and_assistant(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user())
    fake_runtime.assistant_text = "hello"

    response = await asgi_client.post(
        "/api/v1/agents/echo_agent/chat",
        json={"message": "hi"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["assistant_message"]["content"] == "hello"
    assert body["run_id"] == "run-1"
    assert fake_runtime.wait_calls[0]["graph_id"] == "echo_agent"
    assert fake_runtime.wait_calls[0]["input"]["messages"][0]["content"] == "hi"

    messages = await asgi_client.get(f"/api/v1/conversations/{body['conversation_id']}/messages")
    assert [item["role"] for item in messages.json()["items"]] == ["user", "assistant"]


async def test_multi_turn_reuses_conversation_and_thread(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user())
    fake_runtime.assistant_text = "first"

    first = (
        await asgi_client.post("/api/v1/agents/echo_agent/chat", json={"message": "one"})
    ).json()

    fake_runtime.assistant_text = "second"
    second = (
        await asgi_client.post(
            "/api/v1/agents/echo_agent/chat",
            json={"message": "two", "conversation_id": first["conversation_id"]},
        )
    ).json()

    assert second["conversation_id"] == first["conversation_id"]
    assert len(fake_runtime.wait_calls) == 2
    assert fake_runtime.wait_calls[1]["thread_id"] == "thread-1"

    messages = await asgi_client.get(f"/api/v1/conversations/{first['conversation_id']}/messages")
    items = messages.json()["items"]
    assert [item["role"] for item in items] == ["user", "assistant", "user", "assistant"]
    assert [item["content"] for item in items] == ["one", "first", "two", "second"]

    conversations = await asgi_client.get("/api/v1/conversations")
    assert len(conversations.json()["items"]) == 1


async def test_stream_emits_meta_tokens_and_done(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user())
    fake_runtime.assistant_text = "hello"

    response = await asgi_client.post(
        "/api/v1/agents/echo_agent/chat/stream",
        json={"message": "hi"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _sse_events(response.text)
    names = [name for name, _ in events]
    assert names[0] == "meta"
    assert names[-1] == "done"
    assert "error" not in names

    assert "".join(payload["delta"] for name, payload in events if name == "token") == "hello"
    assert events[-1][1]["message_id"] is not None
    assert events[-1][1]["run_id"] == "run-1"

    conversation_id = events[0][1]["conversation_id"]
    messages = await asgi_client.get(f"/api/v1/conversations/{conversation_id}/messages")
    items = messages.json()["items"]
    assert [item["role"] for item in items] == ["user", "assistant"]
    assert items[-1]["content"] == "hello"


async def test_stream_emits_tool_events(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user())
    fake_runtime.events = [
        RunStarted("run-2"),
        ToolCallEvent("call-1", "get_server_time", "{}"),
        ToolResultEvent("call-1", "get_server_time", "2026-11-20T07:00:00+00:00"),
        TokenDelta("工具返回"),
        RunFinished("success"),
    ]

    response = await asgi_client.post(
        "/api/v1/agents/echo_agent/chat/stream",
        json={"message": "现在几点"},
    )

    events = _sse_events(response.text)
    assert [name for name, _ in events] == ["meta", "tool_call", "tool_result", "token", "done"]
    assert events[1][1]["name"] == "get_server_time"
    assert events[2][1]["call_id"] == "call-1"
    assert events[3][1]["delta"] == "工具返回"


async def test_stream_failure_persists_partial_message(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user())
    fake_runtime.events = [
        RunStarted("run-3"),
        TokenDelta("部分"),
        RunFailed("upstream failed", "run_failed"),
    ]

    response = await asgi_client.post(
        "/api/v1/agents/echo_agent/chat/stream",
        json={"message": "hi"},
    )

    events = _sse_events(response.text)
    names = [name for name, _ in events]
    assert names == ["meta", "token", "error"]
    assert events[-1][1]["code"] == "run_failed"

    conversation_id = events[0][1]["conversation_id"]
    messages = await asgi_client.get(f"/api/v1/conversations/{conversation_id}/messages")
    items = messages.json()["items"]
    assert items[-1]["role"] == "assistant"
    assert items[-1]["content"] == "部分"
    assert items[-1]["status"] == "partial"


async def test_stream_runtime_error_is_reported(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user())
    fake_runtime.fail_stream = True

    response = await asgi_client.post(
        "/api/v1/agents/echo_agent/chat/stream",
        json={"message": "hi"},
    )

    events = _sse_events(response.text)
    assert [name for name, _ in events] == ["meta", "error"]
    assert events[-1][1]["code"] == "runtime_error"


async def test_cannot_chat_into_another_apps_conversation(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user("agriculture_app"))
    created = (
        await asgi_client.post("/api/v1/agents/echo_agent/chat", json={"message": "hi"})
    ).json()

    authenticated_as(_user("management_app"))
    response = await asgi_client.post(
        "/api/v1/agents/echo_agent/chat",
        json={"message": "steal", "conversation_id": created["conversation_id"]},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_chat_requires_agent_permission(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user(allowed_agents=[]))

    response = await asgi_client.post(
        "/api/v1/agents/echo_agent/chat",
        json={"message": "hi"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_chat_without_conversation_id_generates_title(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user())

    response = await asgi_client.post(
        "/api/v1/agents/echo_agent/chat",
        json={"message": "hello   world"},
    )
    conversation_id = response.json()["conversation_id"]

    conversation = await asgi_client.get(f"/api/v1/conversations/{conversation_id}")

    assert conversation.json()["title"] == "hello world"


async def test_each_agent_is_called_independently(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user())
    fake_runtime.assistant_text = "ok"

    echo = await asgi_client.post(
        "/api/v1/agents/echo_agent/chat",
        json={"message": "hi"},
    )
    reverse = await asgi_client.post(
        "/api/v1/agents/reverse_agent/chat",
        json={"message": "hi"},
    )

    assert echo.status_code == 200
    assert reverse.status_code == 200
    assert fake_runtime.wait_calls[0]["graph_id"] == "echo_agent"
    assert fake_runtime.wait_calls[1]["graph_id"] == "reverse_agent"
    assert echo.json()["conversation_id"] != reverse.json()["conversation_id"]


async def test_sync_runtime_failure_returns_502(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user())
    fake_runtime.fail_wait = True

    response = await asgi_client.post(
        "/api/v1/agents/echo_agent/chat",
        json={"message": "hi"},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "runtime_error"


async def test_client_disconnect_keeps_the_partial_reply(
    seeded_apps, agri_context, session, fake_runtime, workflow_launcher
):
    """Closing the stream mid-flight must not leave a half reply unsaved."""
    from app.api.v1.agents import chat_stream
    from app.api.v1.schemas import ChatRequest
    from app.repositories.conversations import MessageRepository
    from app.runtime.adapter import RuntimeCredentials

    response = await chat_stream(
        agent_id="echo_agent",
        payload=ChatRequest(message="hi"),
        user=_user(),
        context=agri_context,
        credentials=RuntimeCredentials(
            app_id="agriculture_app", authorization="Bearer test-token"
        ),
        runtime=fake_runtime,
        session=session,
        launcher=workflow_launcher,
    )

    iterator = response.body_iterator
    meta = await anext(iterator)
    first_token = await anext(iterator)
    assert "event: meta" in meta
    assert "event: token" in first_token

    # The client hangs up: Starlette closes the generator under our feet.
    await iterator.aclose()
    await workflow_launcher.drain()

    conversation_id = json.loads(meta.split("data: ", 1)[1].strip())["conversation_id"]
    messages = await MessageRepository(session, agri_context).list(conversation_id)

    assert [(m.role, m.status, m.content) for m in messages] == [
        ("user", "complete", "hi"),
        ("assistant", "partial", "he"),
    ]
