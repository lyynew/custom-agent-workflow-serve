"""In-memory runtime used by API tests.

It implements the same protocol as AegraRuntimeAdapter, so route tests exercise
real orchestration and persistence without a live Aegra process.
"""

from collections.abc import AsyncIterator, Mapping
from typing import Any

from app.runtime.adapter import (
    RunFinished,
    RunHandle,
    RunSnapshot,
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
        workflow_statuses: list[str] | None = None,
        workflow_output: dict[str, Any] | None = None,
        workflow_error: str | None = None,
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

        # Workflow runs: the last status is sticky so a "still running" script
        # keeps reporting running until the caller gives up.
        self.workflow_statuses = list(workflow_statuses or ["succeeded"])
        self.workflow_output = {"total": 5} if workflow_output is None else workflow_output
        self.workflow_error = workflow_error
        self.started_runs: list[dict[str, Any]] = []
        self.run_polls: list[str] = []
        self.cancelled_runs: list[str] = []

        self.fail_create = False
        self.fail_wait = False
        self.fail_stream = False
        self.fail_start_run = False

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

    async def start_run(
        self,
        credentials: RuntimeCredentials,
        *,
        thread_id: str,
        graph_id: str,
        input: Mapping[str, Any],
    ) -> RunHandle:
        if self.fail_start_run:
            raise RuntimeCallError(500, "start_run")

        run_id = f"wf-run-{len(self.started_runs) + 1}"
        self.started_runs.append(
            {"thread_id": thread_id, "graph_id": graph_id, "input": dict(input), "run_id": run_id}
        )
        return RunHandle(run_id=run_id, status="pending")

    def _next_status(self) -> str:
        if len(self.workflow_statuses) > 1:
            return self.workflow_statuses.pop(0)
        return self.workflow_statuses[0]

    async def get_run(
        self,
        credentials: RuntimeCredentials,
        *,
        thread_id: str,
        run_id: str,
    ) -> RunSnapshot | None:
        status = self._next_status()
        self.run_polls.append(status)

        if status == "running":
            return RunSnapshot(run_id=run_id, status="running")

        return RunSnapshot(
            run_id=run_id,
            status=status,
            output=self.workflow_output if status == "succeeded" else None,
            error_message=self.workflow_error,
        )

    async def cancel_run(
        self,
        credentials: RuntimeCredentials,
        *,
        thread_id: str,
        run_id: str,
    ) -> bool:
        self.cancelled_runs.append(run_id)
        return True


class CollectingLauncher:
    """Test double for `TaskLauncher`: records coroutines instead of racing them.

    Tests call `await drain()` to run them deterministically, or leave them
    pending to observe the 202 / pending state.
    """

    def __init__(self) -> None:
        self.pending: list[tuple[str, Any]] = []

    def launch(self, name: str, coro: Any) -> None:
        self.pending.append((name, coro))

    async def drain(self) -> None:
        while self.pending:
            _, coro = self.pending.pop(0)
            await coro

    def close(self) -> None:
        for _, coro in self.pending:
            close = getattr(coro, "close", None)
            if close is not None:
                close()
        self.pending.clear()
