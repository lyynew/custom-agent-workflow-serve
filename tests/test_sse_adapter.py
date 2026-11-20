"""Aegra SSE adaptation: cumulative snapshots become deltas and tool events."""

import httpx
import pytest

from app.runtime.adapter import (
    RunFinished,
    RunStarted,
    RuntimeCredentials,
    TokenDelta,
    ToolCallEvent,
    ToolResultEvent,
)
from app.runtime.aegra import AegraRuntimeAdapter, RuntimeCallError

SSE_BODY = """event: metadata
data: {"run_id":"run-1","attempt":1}

event: messages/partial
data: [{"id":"m1","type":"ai","content":"你"}]

event: messages/partial
data: [{"id":"m1","type":"ai","content":"你好"}]

event: messages/partial
data: [{"id":"m1","type":"ai","content":"你好","tool_calls":[{"id":"call-1","name":"get_server_time","args":{}}]}]

event: messages/partial
data: [{"id":"m2","type":"tool","name":"get_server_time","tool_call_id":"call-1","content":"2026-11-20T07:00:00+00:00"}]

event: messages/partial
data: [{"id":"m3","type":"ai","content":"完成"}]

event: end
data: {"status":"success"}

"""


def _adapter(handler) -> AegraRuntimeAdapter:
    return AegraRuntimeAdapter("http://runtime", transport=httpx.MockTransport(handler))


async def test_cumulative_stream_is_converted_to_deltas():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/threads/t-1/runs/stream":
            return httpx.Response(
                200,
                content=SSE_BODY.encode(),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(404)

    adapter = _adapter(handler)
    credentials = RuntimeCredentials(app_id="agriculture_app", authorization="Bearer test")

    events = [
        event
        async for event in adapter.stream_thread_run(
            credentials,
            thread_id="t-1",
            graph_id="echo_agent",
            input={"messages": []},
        )
    ]

    assert [type(event) for event in events] == [
        RunStarted,
        TokenDelta,
        TokenDelta,
        ToolCallEvent,
        ToolResultEvent,
        TokenDelta,
        RunFinished,
    ]
    assert events[0].run_id == "run-1"
    assert events[1].text == "你"
    assert events[2].text == "好"
    assert events[3].name == "get_server_time"
    assert events[3].call_id == "call-1"
    assert events[4].content.startswith("2026-11-20")
    assert events[5].text == "完成"


async def test_run_wait_on_thread_returns_state_and_run_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/threads/t-1/runs/wait"
        return httpx.Response(
            200,
            json={"messages": [{"type": "ai", "content": "ok"}]},
            headers={"Content-Location": "/threads/t-1/runs/run-9"},
        )

    adapter = _adapter(handler)
    credentials = RuntimeCredentials(app_id="agriculture_app", authorization="Bearer test")

    result = await adapter.run_wait_on_thread(
        credentials,
        thread_id="t-1",
        graph_id="echo_agent",
        input={"messages": []},
    )

    assert result.state["messages"][0]["content"] == "ok"
    assert result.run_id == "run-9"


async def test_stream_error_raises_runtime_error():
    adapter = _adapter(lambda _request: httpx.Response(500, json={"detail": "boom"}))
    credentials = RuntimeCredentials(app_id="agriculture_app", authorization="Bearer test")

    with pytest.raises(RuntimeCallError):
        async for _ in adapter.stream_thread_run(
            credentials,
            thread_id="t-1",
            graph_id="echo_agent",
            input={},
        ):
            pass


STREAMED_ARGS_BODY = """event: metadata
data: {"run_id":"run-2"}

event: messages/partial
data: [{"id":"m1","type":"ai","content":"","tool_calls":[{"id":"call-9","name":"calculator","args":{}}]}]

event: messages/partial
data: [{"id":"m1","type":"ai","content":"","tool_calls":[{"id":"call-9","name":"calculator","args":{"expression":"25"}}]}]

event: messages/partial
data: [{"id":"m1","type":"ai","content":"","tool_calls":[{"id":"call-9","name":"calculator","args":{"expression":"25*4"}}]}]

event: messages/complete
data: [{"id":"m1","type":"ai","content":"","tool_calls":[{"id":"call-9","name":"calculator","args":{"expression":"25*4"}}]}]

event: messages/partial
data: [{"id":"m2","type":"tool","name":"calculator","tool_call_id":"call-9","content":"25*4 = 100"}]

"""


async def test_tool_call_is_announced_once_with_complete_arguments():
    """Streaming providers send the call before its arguments.

    Regression: the first snapshot has `args={}`, and announcing it there gave
    the caller an empty argument object.
    """
    adapter = _adapter(
        lambda _request: httpx.Response(
            200,
            content=STREAMED_ARGS_BODY.encode(),
            headers={"content-type": "text/event-stream"},
        )
    )
    credentials = RuntimeCredentials(app_id="agriculture_app", authorization="Bearer test")

    events = [
        event
        async for event in adapter.stream_thread_run(
            credentials,
            thread_id="t-2",
            graph_id="assistant_agent",
            input={"messages": []},
        )
    ]

    calls = [event for event in events if isinstance(event, ToolCallEvent)]
    assert len(calls) == 1
    assert '"expression"' in calls[0].arguments
    assert "25*4" in calls[0].arguments

    # Ordering matters: the call must precede the result.
    assert isinstance(events[events.index(calls[0]) + 1], ToolResultEvent)


PARTIAL_ONLY_ARGS_BODY = """event: metadata
data: {"run_id":"run-3"}

event: messages/partial
data: [{"id":"m1","type":"ai","content":"","tool_calls":[{"id":"call-7","name":"calculator","args":{}}]}]

event: messages/partial
data: [{"id":"m1","type":"ai","content":"","tool_calls":[{"id":"call-7","name":"calculator","args":{"expression":"25"}}]}]

event: messages/partial
data: [{"id":"m1","type":"ai","content":"","tool_calls":[{"id":"call-7","name":"calculator","args":{"expression":"25*4"}}]}]

event: messages/complete
data: [{"content":"25*4 = 100","type":"tool","name":"calculator","id":"m2","tool_call_id":"call-7"}]

"""


async def test_tool_call_arguments_survive_a_partial_only_stream():
    """The runtime only sends assistant messages as cumulative partials.

    `messages/complete` is emitted for the tool message, never for the AI
    message, so the last partial snapshot is the only source of final arguments.
    """
    adapter = _adapter(
        lambda _request: httpx.Response(
            200,
            content=PARTIAL_ONLY_ARGS_BODY.encode(),
            headers={"content-type": "text/event-stream"},
        )
    )
    credentials = RuntimeCredentials(app_id="agriculture_app", authorization="Bearer test")

    events = [
        event
        async for event in adapter.stream_thread_run(
            credentials,
            thread_id="t-3",
            graph_id="simple_agent",
            input={"messages": []},
        )
    ]

    calls = [event for event in events if isinstance(event, ToolCallEvent)]
    assert len(calls) == 1
    assert "25*4" in calls[0].arguments
    assert isinstance(events[events.index(calls[0]) + 1], ToolResultEvent)
