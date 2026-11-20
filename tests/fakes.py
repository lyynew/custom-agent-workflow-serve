"""In-memory runtime used by API tests.

It implements the same protocol as AegraRuntimeAdapter, so route tests exercise
real orchestration and persistence without a live Aegra process.
"""

from collections.abc import AsyncIterator, Mapping
from typing import Any

from app.runtime.adapter import (
    RunFinished,
    RunStarted,
    RuntimeCredentials,
    RuntimeEvent,
    RunWaitResult,
    TokenDelta,
)
from app.runtime.aegra import RuntimeCallError


class FakeRuntime:
    def __init__(
        self,
        *,
        assistant_text: str = "hello",
        events: list[RuntimeEvent] | None = None,
    ) -> None:
        self.assistant_text = assistant_text
        self.events = (
            list(events)
            if events is not None
            else [
                RunStarted("run-1"),
                TokenDelta("he"),
                TokenDelta("llo"),
                RunFinished("success"),
            ]
        )

        self.threads: dict[str, dict[str, Any]] = {}
        self.created_metadata: list[dict[str, Any]] = []
        self.deleted_threads: list[str] = []
        self.wait_calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []

        self.fail_create = False
        self.fail_wait = False
        self.fail_stream = False

    async def create_thread(
        self,
        credentials: RuntimeCredentials,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        if self.fail_create:
            raise RuntimeCallError(500, "create_thread")

        thread_id = f"thread-{len(self.threads) + 1}"
        self.threads[thread_id] = {"metadata": dict(metadata or {})}
        self.created_metadata.append(dict(metadata or {}))
        return thread_id

    async def get_thread(
        self,
        credentials: RuntimeCredentials,
        thread_id: str,
    ) -> dict[str, Any] | None:
        return self.threads.get(thread_id)

    async def delete_thread(
        self,
        credentials: RuntimeCredentials,
        thread_id: str,
    ) -> bool:
        self.deleted_threads.append(thread_id)
        return self.threads.pop(thread_id, None) is not None

    async def run_wait(
        self,
        credentials: RuntimeCredentials,
        *,
        graph_id: str,
        input: Mapping[str, Any],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return {"messages": [{"type": "ai", "content": self.assistant_text}]}

    async def run_wait_on_thread(
        self,
        credentials: RuntimeCredentials,
        *,
        thread_id: str,
        graph_id: str,
        input: Mapping[str, Any],
        timeout_seconds: float | None = None,
    ) -> RunWaitResult:
        if self.fail_wait:
            raise RuntimeCallError(500, "run_wait_on_thread")

        self.wait_calls.append(
            {"thread_id": thread_id, "graph_id": graph_id, "input": dict(input)}
        )
        state = {
            "messages": [
                *list(input.get("messages", [])),
                {"type": "ai", "content": self.assistant_text},
            ]
        }
        return RunWaitResult(state=state, run_id="run-1")

    async def stream_thread_run(
        self,
        credentials: RuntimeCredentials,
        *,
        thread_id: str,
        graph_id: str,
        input: Mapping[str, Any],
    ) -> AsyncIterator[RuntimeEvent]:
        if self.fail_stream:
            raise RuntimeCallError(500, "stream_thread_run")

        self.stream_calls.append(
            {"thread_id": thread_id, "graph_id": graph_id, "input": dict(input)}
        )
        for event in self.events:
            yield event
