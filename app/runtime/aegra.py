"""Aegra HTTP implementation of the runtime adapter.

Aegra emits LangGraph's cumulative `messages/partial` snapshots. This adapter is
the only place that understands that wire format; the rest of the code receives
token deltas and tool events.
"""

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any
from uuid import uuid5

import httpx
from aegra_api.constants import ASSISTANT_NAMESPACE_UUID

from app.runtime.adapter import (
    RunFailed,
    RunFinished,
    RunHandle,
    RunSnapshot,
    RunStarted,
    RuntimeCredentials,
    RuntimeEvent,
    RunWaitResult,
    TokenDelta,
    ToolCallEvent,
    ToolResultEvent,
)

#: Aegra reports LangGraph Platform statuses; the business layer only sees the
#: normalised vocabulary in `adapter.RUN_STATUSES`.
_STATUS_MAP = {
    "pending": "pending",
    "running": "running",
    "success": "succeeded",
    "error": "failed",
    "timeout": "timed_out",
    "interrupted": "cancelled",
}


def normalise_run_status(status: Any) -> str:
    raw = str(status or "pending")
    return _STATUS_MAP.get(raw, raw)


class RuntimeCallError(RuntimeError):
    """The runtime rejected a call. Kept internal; API routes map it to a 5xx."""

    def __init__(self, status_code: int, operation: str) -> None:
        self.status_code = status_code
        self.operation = operation
        super().__init__(f"Runtime call failed: {operation} (HTTP {status_code})")


def assistant_id_for(graph_id: str) -> str:
    """Aegra seeds one deterministic default assistant per graph."""
    return str(uuid5(ASSISTANT_NAMESPACE_UUID, graph_id))


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False)


def _run_id_from_headers(response: httpx.Response) -> str | None:
    location = response.headers.get("content-location") or response.headers.get("location")
    if not location:
        return None
    return location.rstrip("/").rsplit("/", 1)[-1] or None


class AegraRuntimeAdapter:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def _client(self, timeout_seconds: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout_seconds or self._timeout_seconds,
            transport=self._transport,
        )

    async def create_thread(
        self,
        credentials: RuntimeCredentials,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        async with self._client() as client:
            response = await client.post(
                "/threads",
                json={"metadata": dict(metadata or {})},
                headers=credentials.headers(),
            )
        if response.status_code >= 400:
            raise RuntimeCallError(response.status_code, "create_thread")
        return str(response.json()["thread_id"])

    async def get_thread(
        self,
        credentials: RuntimeCredentials,
        thread_id: str,
    ) -> dict[str, Any] | None:
        async with self._client() as client:
            response = await client.get(f"/threads/{thread_id}", headers=credentials.headers())

        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise RuntimeCallError(response.status_code, "get_thread")
        return dict(response.json())

    async def delete_thread(
        self,
        credentials: RuntimeCredentials,
        thread_id: str,
    ) -> bool:
        async with self._client() as client:
            response = await client.delete(f"/threads/{thread_id}", headers=credentials.headers())

        if response.status_code == 404:
            return False
        if response.status_code >= 400:
            raise RuntimeCallError(response.status_code, "delete_thread")
        return True

    async def run_wait(
        self,
        credentials: RuntimeCredentials,
        *,
        graph_id: str,
        input: Mapping[str, Any],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        body = {"assistant_id": assistant_id_for(graph_id), "input": dict(input)}
        async with self._client(timeout_seconds) as client:
            response = await client.post("/runs/wait", json=body, headers=credentials.headers())

        if response.status_code >= 400:
            raise RuntimeCallError(response.status_code, "run_wait")
        return dict(response.json())

    async def run_wait_on_thread(
        self,
        credentials: RuntimeCredentials,
        *,
        thread_id: str,
        graph_id: str,
        input: Mapping[str, Any],
        timeout_seconds: float | None = None,
    ) -> RunWaitResult:
        body = {
            "assistant_id": assistant_id_for(graph_id),
            "input": dict(input),
        }
        headers = {**credentials.headers(), "accept-encoding": "identity"}

        async with self._client(timeout_seconds) as client:
            response = await client.post(
                f"/threads/{thread_id}/runs/wait",
                json=body,
                headers=headers,
            )

        if response.status_code >= 400:
            raise RuntimeCallError(response.status_code, "run_wait_on_thread")

        state = response.json()
        if not isinstance(state, dict):
            state = {"result": state}

        return RunWaitResult(state=state, run_id=_run_id_from_headers(response))

    def _adapt_message_event(
        self,
        payload: str,
        last_content: dict[str, str],
        seen_calls: set[str],
        seen_results: set[str],
    ) -> list[RuntimeEvent]:
        try:
            items = json.loads(payload)
        except json.JSONDecodeError:
            return []
        if not isinstance(items, list):
            return []

        events: list[RuntimeEvent] = []
        for item in items:
            if not isinstance(item, dict):
                continue

            message_id = str(item.get("id") or "")
            item_type = item.get("type")

            if item_type == "tool":
                result_key = message_id or f"tool:{item.get('tool_call_id') or ''}"
                if result_key not in seen_results:
                    seen_results.add(result_key)
                    events.append(
                        ToolResultEvent(
                            call_id=item.get("tool_call_id"),
                            name=str(item.get("name") or ""),
                            content=_stringify(item.get("content")),
                        )
                    )
                continue

            content = item.get("content")
            if isinstance(content, str):
                previous = last_content.get(message_id, "")
                delta = content[len(previous) :] if message_id and content.startswith(previous) else content
                if message_id:
                    last_content[message_id] = content
                if delta:
                    events.append(TokenDelta(delta))

            for call in item.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                call_id = str(call.get("id") or "")
                if call_id and call_id in seen_calls:
                    continue
                if call_id:
                    seen_calls.add(call_id)
                arguments = call.get("args", {})
                events.append(
                    ToolCallEvent(
                        call_id=call_id,
                        name=str(call.get("name") or ""),
                        arguments=arguments if isinstance(arguments, str) else _stringify(arguments),
                    )
                )

        return events

    def _adapt_sse(
        self,
        event_name: str | None,
        payload: str,
        last_content: dict[str, str],
        seen_calls: set[str],
        seen_results: set[str],
    ) -> list[RuntimeEvent]:
        if event_name in {"messages/partial", "messages/complete"}:
            return self._adapt_message_event(payload, last_content, seen_calls, seen_results)

        if event_name == "metadata":
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                return []
            run_id = str(data.get("run_id") or "") if isinstance(data, dict) else ""
            return [RunStarted(run_id)] if run_id else []

        if event_name == "end":
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                data = {}
            status = str(data.get("status") or "success") if isinstance(data, dict) else "success"
            if status in {"success", "completed"}:
                return [RunFinished(status)]
            return [RunFailed(f"Run ended with status: {status}", "run_failed")]

        if event_name in {"error", "run/error"}:
            return [RunFailed(payload[:500], "runtime_error")]

        return []

    async def stream_thread_run(
        self,
        credentials: RuntimeCredentials,
        *,
        thread_id: str,
        graph_id: str,
        input: Mapping[str, Any],
    ) -> AsyncIterator[RuntimeEvent]:
        body = {
            "assistant_id": assistant_id_for(graph_id),
            "input": dict(input),
            "stream_mode": ["messages"],
        }
        headers = {**credentials.headers(), "accept-encoding": "identity"}

        last_content: dict[str, str] = {}
        seen_calls: set[str] = set()
        seen_results: set[str] = set()

        async with self._client(None) as client, client.stream(
            "POST",
            f"/threads/{thread_id}/runs/stream",
            json=body,
            headers=headers,
        ) as response:
            if response.status_code >= 400:
                await response.aread()
                raise RuntimeCallError(response.status_code, "stream_thread_run")

            event_name: str | None = None
            data_lines: list[str] = []

            async for line in response.aiter_lines():
                if line == "":
                    if data_lines:
                        for event in self._adapt_sse(
                            event_name,
                            "\n".join(data_lines),
                            last_content,
                            seen_calls,
                            seen_results,
                        ):
                            yield event
                    event_name, data_lines = None, []
                    continue

                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())

            if data_lines:
                for event in self._adapt_sse(
                    event_name,
                    "\n".join(data_lines),
                    last_content,
                    seen_calls,
                    seen_results,
                ):
                    yield event

    async def start_run(
        self,
        credentials: RuntimeCredentials,
        *,
        thread_id: str,
        graph_id: str,
        input: Mapping[str, Any],
    ) -> RunHandle:
        body = {
            "assistant_id": assistant_id_for(graph_id),
            "input": dict(input),
        }
        async with self._client() as client:
            response = await client.post(
                f"/threads/{thread_id}/runs",
                json=body,
                headers=credentials.headers(),
            )

        if response.status_code >= 400:
            raise RuntimeCallError(response.status_code, "start_run")

        data = response.json()
        run_id = str(data.get("run_id") or "")
        if not run_id:
            raise RuntimeCallError(502, "start_run")
        return RunHandle(run_id=run_id, status=normalise_run_status(data.get("status")))

    async def get_run(
        self,
        credentials: RuntimeCredentials,
        *,
        thread_id: str,
        run_id: str,
    ) -> RunSnapshot | None:
        async with self._client() as client:
            response = await client.get(
                f"/threads/{thread_id}/runs/{run_id}",
                headers=credentials.headers(),
            )

        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise RuntimeCallError(response.status_code, "get_run")

        data = response.json()
        output = data.get("output")
        return RunSnapshot(
            run_id=str(data.get("run_id") or run_id),
            status=normalise_run_status(data.get("status")),
            output=output if isinstance(output, dict) else None,
            error_message=data.get("error_message"),
        )

    async def cancel_run(
        self,
        credentials: RuntimeCredentials,
        *,
        thread_id: str,
        run_id: str,
    ) -> bool:
        async with self._client() as client:
            response = await client.post(
                f"/threads/{thread_id}/runs/{run_id}/cancel",
                headers=credentials.headers(),
            )

        if response.status_code == 404:
            return False
        if response.status_code >= 400:
            raise RuntimeCallError(response.status_code, "cancel_run")
        return True
