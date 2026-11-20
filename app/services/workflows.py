"""Workflow orchestration.

One code path serves both execution modes: a run is always started in the
background by the runtime, then a poller waits for it. `mode=sync` simply waits
inside the request; `mode=async` hands the same wait to a background task.

The poller owns its own short-lived sessions on purpose — a run outlives the
request, and holding a request session for minutes would starve the pool.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Mapping
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import AuthContext
from app.config import get_settings
from app.database.session import get_session_factory
from app.models.workflow_run import WorkflowRun
from app.repositories.workflow_runs import ACTIVE_STATUSES, WorkflowRunRepository
from app.runtime.adapter import RunSnapshot, RuntimeAdapter, RuntimeCredentials
from app.runtime.aegra import RuntimeCallError

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "timed_out", "cancelled"})


class WorkflowCapacityReached(Exception):
    """Too many unfinished runs on this instance."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"Workflow concurrency limit reached: {limit}")


def _error(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


class TaskLauncher(Protocol):
    def launch(self, name: str, coro: Awaitable[None]) -> None: ...


class AsyncioTaskLauncher:
    """Runs background work in the current loop.

    A strong reference is kept because the event loop only holds weak ones, so
    an unreferenced task can be garbage-collected mid-flight.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def launch(self, name: str, coro: Awaitable[None]) -> None:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


#: Process-wide launcher. Single-instance deployment, no queue (see ADR 0001).
task_launcher = AsyncioTaskLauncher()


class WorkflowService:
    def __init__(
        self,
        session: AsyncSession,
        context: AuthContext,
        credentials: RuntimeCredentials,
        runtime: RuntimeAdapter,
    ) -> None:
        self._session = session
        self._context = context
        self._credentials = credentials
        self._runtime = runtime

    async def start(self, workflow_id: str, payload: Mapping[str, Any]) -> WorkflowRun:
        settings = get_settings()
        repository = WorkflowRunRepository(self._session, self._context)

        if await repository.count_active() >= settings.workflow_max_concurrent_runs:
            raise WorkflowCapacityReached(settings.workflow_max_concurrent_runs)

        thread_id = await self._runtime.create_thread(
            self._credentials,
            metadata={"workflow_id": workflow_id, "kind": "workflow"},
        )
        try:
            handle = await self._runtime.start_run(
                self._credentials,
                thread_id=thread_id,
                graph_id=workflow_id,
                input=payload,
            )
        except Exception:
            await self._discard_thread(thread_id)
            raise

        run = await repository.create(
            workflow_id=workflow_id,
            runtime_run_id=handle.run_id,
            thread_id=thread_id,
            input=dict(payload),
        )
        try:
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            await self._cancel_quietly(thread_id, handle.run_id)
            raise

        return run

    async def wait_for(self, run_id: UUID) -> str:
        """Poll until the run is terminal and persist the outcome.

        Returns the status actually recorded, which may differ from the outcome
        observed here if a cancel request won the race.
        """
        settings = get_settings()
        handle = await self._load_handle(run_id)
        if handle is None:
            return await self._finish_quietly(
                run_id,
                status="failed",
                error=_error("runtime_lost", "Run has no runtime handle"),
            )

        thread_id, runtime_run_id = handle
        await self._mark_running(run_id)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.workflow_run_timeout_seconds
        interval = settings.workflow_poll_interval_seconds

        try:
            while True:
                snapshot = await self._poll(thread_id, runtime_run_id)

                if snapshot is None:
                    return await self._finish_quietly(
                        run_id,
                        status="failed",
                        error=_error("runtime_lost", "Runtime no longer knows this run"),
                    )

                if snapshot.status in TERMINAL_STATUSES:
                    return await self._finish_from_snapshot(run_id, snapshot)

                if loop.time() >= deadline:
                    await self._cancel_quietly(thread_id, runtime_run_id)
                    return await self._finish_quietly(
                        run_id,
                        status="timed_out",
                        error=_error(
                            "workflow_timeout",
                            f"Run exceeded {settings.workflow_run_timeout_seconds:.0f}s",
                        ),
                    )

                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            # Service shutdown. Record it now instead of waiting for the next
            # startup reconciliation, but never let cleanup mask the cancel.
            with contextlib.suppress(Exception):
                await self._finish_quietly(
                    run_id,
                    status="interrupted",
                    error=_error("interrupted", "Service stopped before the run finished"),
                )
            raise

    async def cancel(self, run_id: UUID) -> WorkflowRun:
        """Cancel an active run. Returns the row whatever the outcome."""
        repository = WorkflowRunRepository(self._session, self._context)
        run = await repository.require(run_id)

        if run.status not in ACTIVE_STATUSES:
            return run

        if run.thread_id and run.aegra_run_id:
            await self._cancel_quietly(run.thread_id, run.aegra_run_id)

        await repository.finish(
            run_id,
            status="cancelled",
            error=_error("workflow_cancelled", "Cancelled by client"),
        )
        await self._session.commit()

        reloaded = await repository.get(run_id)
        return reloaded or run

    # -- internals ---------------------------------------------------------

    async def _poll(self, thread_id: str, runtime_run_id: str) -> RunSnapshot | None:
        """One status read. A transient runtime error is retried by the caller."""
        try:
            return await self._runtime.get_run(
                self._credentials,
                thread_id=thread_id,
                run_id=runtime_run_id,
            )
        except RuntimeCallError as exc:
            logger.warning("Workflow run poll failed (%s); will retry", exc)
            return RunSnapshot(run_id=runtime_run_id, status="running")

    async def _finish_from_snapshot(self, run_id: UUID, snapshot: RunSnapshot) -> str:
        if snapshot.status == "succeeded":
            return await self._finish_quietly(run_id, status="succeeded", output=snapshot.output or {})

        if snapshot.status == "failed":
            return await self._finish_quietly(
                run_id,
                status="failed",
                error=_error("workflow_failed", snapshot.error_message or "Workflow execution failed"),
            )

        if snapshot.status == "timed_out":
            return await self._finish_quietly(
                run_id,
                status="timed_out",
                error=_error("workflow_timeout", snapshot.error_message or "Workflow execution timed out"),
            )

        return await self._finish_quietly(
            run_id,
            status="cancelled",
            error=_error("workflow_cancelled", "Run was cancelled"),
        )

    async def _load_handle(self, run_id: UUID) -> tuple[str, str] | None:
        async with get_session_factory()() as session:
            repository = WorkflowRunRepository(session, self._context)
            run = await repository.get(run_id)

        if run is None or not run.aegra_run_id or not run.thread_id:
            return None
        return run.thread_id, run.aegra_run_id

    async def _mark_running(self, run_id: UUID) -> None:
        try:
            async with get_session_factory()() as session:
                repository = WorkflowRunRepository(session, self._context)
                await repository.mark_running(run_id)
                await session.commit()
        except Exception:
            logger.exception("Failed to mark workflow run %s as running", run_id)

    async def _finish_quietly(
        self,
        run_id: UUID,
        *,
        status: str,
        output: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> str:
        try:
            async with get_session_factory()() as session:
                repository = WorkflowRunRepository(session, self._context)
                await repository.finish(run_id, status=status, output=output, error=error)
                await session.commit()

                current = await repository.get(run_id)
                return current.status if current is not None else status
        except Exception:
            logger.exception("Failed to record workflow run %s outcome", run_id)
            return status

    async def _discard_thread(self, thread_id: str) -> None:
        try:
            await self._runtime.delete_thread(self._credentials, thread_id)
        except Exception:
            logger.warning("Failed to delete orphan thread %s", thread_id, exc_info=True)

    async def _cancel_quietly(self, thread_id: str, runtime_run_id: str) -> None:
        try:
            await self._runtime.cancel_run(
                self._credentials,
                thread_id=thread_id,
                run_id=runtime_run_id,
            )
        except Exception:
            logger.warning("Failed to cancel runtime run %s", runtime_run_id, exc_info=True)
