"""Runtime adapter tests with a mocked Aegra transport."""

import json

import httpx
import pytest

from app.runtime.adapter import RuntimeCredentials
from app.runtime.aegra import AegraRuntimeAdapter, RuntimeCallError, assistant_id_for


def _credentials() -> RuntimeCredentials:
    return RuntimeCredentials(app_id="agriculture_app", authorization="Bearer test-token")


async def test_adapter_forwards_auth_and_calls_expected_paths():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/threads" and request.method == "POST":
            return httpx.Response(200, json={"thread_id": "thread-1"})
        if request.url.path == "/threads/thread-1" and request.method == "GET":
            return httpx.Response(200, json={"thread_id": "thread-1", "metadata": {}})
        if request.url.path == "/threads/thread-1" and request.method == "DELETE":
            return httpx.Response(204)
        if request.url.path == "/runs/wait" and request.method == "POST":
            body = json.loads(request.content)
            assert body["assistant_id"] == assistant_id_for("sum_workflow")
            assert body["input"] == {"a": 1, "b": 2}
            return httpx.Response(200, json={"total": 3})
        return httpx.Response(404)

    adapter = AegraRuntimeAdapter(
        "http://runtime",
        transport=httpx.MockTransport(handler),
    )

    thread_id = await adapter.create_thread(_credentials(), metadata={"graph_id": "echo_agent"})
    thread = await adapter.get_thread(_credentials(), thread_id)
    deleted = await adapter.delete_thread(_credentials(), thread_id)
    result = await adapter.run_wait(
        _credentials(),
        graph_id="sum_workflow",
        input={"a": 1, "b": 2},
    )

    assert thread_id == "thread-1"
    assert thread is not None and thread["thread_id"] == "thread-1"
    assert deleted is True
    assert result == {"total": 3}

    assert len(requests) == 4
    for request in requests:
        assert request.headers["x-app-id"] == "agriculture_app"
        assert request.headers["authorization"] == "Bearer test-token"


async def test_get_thread_returns_none_for_other_tenant():
    adapter = AegraRuntimeAdapter(
        "http://runtime",
        transport=httpx.MockTransport(lambda _request: httpx.Response(404)),
    )

    assert await adapter.get_thread(_credentials(), "someone-elses-thread") is None


async def test_runtime_error_does_not_leak_body():
    adapter = AegraRuntimeAdapter(
        "http://runtime",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(500, json={"detail": "internal stack trace"})
        ),
    )

    with pytest.raises(RuntimeCallError) as exc:
        await adapter.create_thread(_credentials())

    assert exc.value.status_code == 500
    assert "internal stack trace" not in str(exc.value)
