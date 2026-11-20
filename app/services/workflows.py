"""Workflow orchestration.

One path serves both execution modes: a run is always started by the runtime in
the background, then a poller waits for it. `mode=sync` waits inside the request;
`mode=async` hands the same wait to a background task.

Multi-replica correctness does not depend on that poller surviving: every read of
a non-terminal run reconciles against the runtime using the *caller's*
credentials. If the replica that started a run dies, the next status read
finalises it. This is why no cross-tenant "service token" exists anywhere here.

The poller owns its own short-lived sessions on purpose — a run outlives the
request, and holding a request session for minutes would starve the pool.
"""

import asyncio
import logging
from collections.abc import Awaitable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import AuthContext
from app.auth.store import application_store
from app.config import get_settings
from app.database.session import get_session_factory
from app.models.workflow_run import WorkflowRun
from app.repositories.workflow_runs import ACTIVE_STATUSES, WorkflowRunRepository
from app.runtime.adapter import RunSnapshot, RuntimeAdapter, RuntimeCredentials
from app.runtime.aegra import RuntimeCallError

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "timed_out", "cancelled"})


class WorkflowCapacityReached(Exception):
    """Too many unfinished runs, for this application or for the whole instance."""

    def __init__(self, limit: int, scope: str) -> None:
        self.limit = limit
        self.scope = scope
        super().__init__(f"{scope} workflow concurrency limit reached: {limit}")


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


#: Process-wide launcher. Background work is an optimisation here: correctness
#: comes from read-time reconciliation, so losing these tasks does not lose a run.
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

        per_app_limit = await self._per_app_limit()
        if await repository.count_active_for_app() >= per_app_limit:
            raise WorkflowCapacityReached(per_app_limit, "application")
        if await repository.count_active() >= settings.workflow_instance_max_runs:
            raise WorkflowCapacityReached(settings.workflow_instance_max_runs, "instance")

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
        observed here if a cancel (or another replica) won the race.
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
        next_heartbeat = loop.time() + settings.workflow_heartbeat_seconds

        while True:
            snapshot = await self._poll(thread_id, runtime_run_id)

            if snapshot is None:
                return await self._finish_quietly(
                    run_id,
                    status="failed",
                    error=_error("runtime_lost", "Runtime no longer knows this run"),
                )

            if snapshot.status in TERMINAL_STATUSES:
                status, output, error = self._outcome(snapshot)
                return await self._finish_quietly(run_id, status=status, output=output, error=error)

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

            if loop.time() >= next_heartbeat:
                # Also returns False when the run is no longer active, which is
                # how a cancel from another request stops this poller.
                if not await self._heartbeat(run_id):
                    return await self._load_status(run_id) or "cancelled"
                next_heartbeat = loop.time() + settings.workflow_heartbeat_seconds

            await asyncio.sleep(interval)

    async def refresh(self, run_id: UUID) -> WorkflowRun:
        """Reconcile a non-terminal run with the runtime using caller credentials.

        This is what makes the API replica-safe: any instance can serve a status
        read and will finalise a run whose poller died with another process.
        """
        repository = WorkflowRunRepository(self._session, self._context)
        run = await repository.require(run_id)

        if run.status not in ACTIVE_STATUSES or not run.aegra_run_id or not run.thread_id:
            return run

        settings = get_settings()

        # A run nobody watches still has to time out.
        if self._past_deadline(run, settings.workflow_run_timeout_seconds):
            await self._finish_in_session(
                run_id,
                status="timed_out",
                error=_error(
                    "workflow_timeout",
                    f"Run exceeded {settings.workflow_run_timeout_seconds:.0f}s",
                ),
            )
            return await repository.require(run_id)

        try:
            snapshot = await self._runtime.get_run(
                self._credentials,
                thread_id=run.thread_id,
                run_id=run.aegra_run_id,
            )
        except RuntimeCallError as exc:
            # Never fail a read because the runtime is unhappy: the client is
            # polling and would otherwise lose sight of the run entirely.
            logger.warning("Could not reconcile workflow run %s: %s", run_id, exc)
            return run

        if snapshot is None:
            await self._finish_in_session(
                run_id,
                status="failed",
                error=_error("runtime_lost", "Runtime no longer knows this run"),
            )
            return await repository.require(run_id)

        if snapshot.status in TERMINAL_STATUSES:
            status, output, error = self._outcome(snapshot)
            await self._finish_in_session(run_id, status=status, output=output, error=error)
            return await repository.require(run_id)

        await repository.heartbeat(run_id)
        await self._session.commit()
        return run

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

    async def _per_app_limit(self) -> int:
        settings = get_settings()
        config = await application_store.get(self._context.app_id)
        if config is not None and config.max_concurrent_workflow_runs:
            return config.max_concurrent_workflow_runs
        return settings.workflow_max_concurrent_runs

    @staticmethod
    def _past_deadline(run: WorkflowRun, timeout_seconds: float) -> bool:
        if run.created_at is None:
            return False
        return datetime.now(UTC) >= run.created_at + timedelta(seconds=timeout_seconds)

    @staticmethod
    def _outcome(snapshot: RunSnapshot) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        if snapshot.status == "succeeded":
            return "succeeded", snapshot.output or {}, None

        if snapshot.status == "failed":
            return (
                "failed",
                None,
                _error("workflow_failed", snapshot.error_message or "Workflow execution failed"),
            )

        if snapshot.status == "timed_out":
            return (
                "timed_out",
                None,
                _error("workflow_timeout", snapshot.error_message or "Workflow execution timed out"),
            )

        return "cancelled", None, _error("workflow_cancelled", "Run was cancelled")

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

    async def _load_handle(self, run_id: UUID) -> tuple[str, str] | None:
        async with get_session_factory()() as session:
            repository = WorkflowRunRepository(session, self._context)
            run = await repository.get(run_id)

        if run is None or not run.aegra_run_id or not run.thread_id:
            return None
        return run.thread_id, run.aegra_run_id

    async def _load_status(self, run_id: UUID) -> str | None:
        async with get_session_factory()() as session:
            repository = WorkflowRunRepository(session, self._context)
            run = await repository.get(run_id)
        return run.status if run is not None else None

    async def _mark_running(self, run_id: UUID) -> None:
        try:
            async with get_session_factory()() as session:
                repository = WorkflowRunRepository(session, self._context)
                await repository.mark_running(run_id)
                await session.commit()
        except Exception:
            logger.exception("Failed to mark workflow run %s as running", run_id)

    async def _heartbeat(self, run_id: UUID) -> bool:
        try:
            async with get_session_factory()() as session:
                repository = WorkflowRunRepository(session, self._context)
                alive = await repository.heartbeat(run_id)
                await session.commit()
                return alive
        except Exception:
            logger.exception("Failed to heartbeat workflow run %s", run_id)
            return True

    async def _finish_in_session(
        self,
        run_id: UUID,
        *,
        status: str,
        output: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> bool:
        """Record an outcome on the request session (used by `refresh`)."""
        repository = WorkflowRunRepository(self._session, self._context)
        updated = await repository.finish(run_id, status=status, output=output, error=error)
        await self._session.commit()
        return updated

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
