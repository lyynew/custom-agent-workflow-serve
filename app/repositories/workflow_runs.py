"""Workflow run records.

Two rules are encoded here rather than in the service:

* every read and write is scoped by `(app_id, user_id)` — a run id alone never
  authorises access;
* status transitions are conditional updates, so a background poller can never
  overwrite a cancellation (and vice versa).
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update

from app.models.workflow_run import WorkflowRun
from app.repositories.base import ScopedRepository

MAX_PAGE_SIZE = 100

#: Statuses that still accept a transition. `interrupted` only exists on rows
#: written before read-time reconciliation (P5) and is already terminal.
ACTIVE_STATUSES = ("pending", "running")


class WorkflowRunRepository(ScopedRepository[WorkflowRun]):
    model = WorkflowRun

    async def create(
        self,
        *,
        workflow_id: str,
        runtime_run_id: str | None = None,
        thread_id: str | None = None,
        input: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        run = WorkflowRun(
            app_id=self.context.app_id,
            user_id=self.context.user_id,
            workflow_id=workflow_id,
            aegra_run_id=runtime_run_id,
            thread_id=thread_id,
            status="pending",
            input=input,
            # Fresh lease: a poller is expected to pick this up immediately.
            # Set in Python (not `now()`) so the value is readable right away
            # without a second round-trip.
            heartbeat_at=datetime.now(UTC),
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def list(
        self,
        *,
        workflow_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
        before: datetime | None = None,
    ) -> Sequence[WorkflowRun]:
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        statement = self._scoped_select()

        if workflow_id is not None:
            statement = statement.where(WorkflowRun.workflow_id == workflow_id)
        if status is not None:
            statement = statement.where(WorkflowRun.status == status)
        if before is not None:
            statement = statement.where(WorkflowRun.created_at < before)

        statement = statement.order_by(WorkflowRun.created_at.desc()).limit(limit)
        return list((await self.session.scalars(statement)).all())

    async def count_active(self) -> int:
        """Instance-wide count of unfinished runs, across all tenants.

        Deliberately unscoped: this is a capacity guard, not a data read, and a
        caller must not be able to infer another tenant's load from it — the
        result is only used to reject work, never returned.
        """
        statement = (
            select(func.count())
            .select_from(WorkflowRun)
            .where(WorkflowRun.status.in_(ACTIVE_STATUSES))
        )
        return int((await self.session.scalar(statement)) or 0)

    async def count_active_for_app(self) -> int:
        """Unfinished runs owned by the current application (all its users)."""
        statement = (
            select(func.count())
            .select_from(WorkflowRun)
            .where(
                WorkflowRun.app_id == self.context.app_id,
                WorkflowRun.status.in_(ACTIVE_STATUSES),
            )
        )
        return int((await self.session.scalar(statement)) or 0)

    async def mark_running(self, run_id: UUID | str) -> bool:
        statement = (
            update(WorkflowRun)
            .where(
                WorkflowRun.id == run_id,
                WorkflowRun.app_id == self.context.app_id,
                WorkflowRun.user_id == self.context.user_id,
                WorkflowRun.status == "pending",
            )
            .values(status="running", started_at=func.now(), heartbeat_at=func.now())
        )
        return (await self.session.execute(statement)).rowcount > 0

    async def heartbeat(self, run_id: UUID | str) -> bool:
        """Extend the lease. False means the run is no longer ours to poll."""
        statement = (
            update(WorkflowRun)
            .where(
                WorkflowRun.id == run_id,
                WorkflowRun.app_id == self.context.app_id,
                WorkflowRun.user_id == self.context.user_id,
                WorkflowRun.status == "running",
            )
            .values(heartbeat_at=func.now())
        )
        return (await self.session.execute(statement)).rowcount > 0

    async def finish(
        self,
        run_id: UUID | str,
        *,
        status: str,
        output: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> bool:
        """Move an active run to a terminal state.

        Returns False when the run already moved on (for example it was
        cancelled while the poller was waiting), which tells the caller not to
        treat its own outcome as authoritative.
        """
        values: dict[str, Any] = {"status": status, "finished_at": func.now()}
        if output is not None:
            values["output"] = output
        if error is not None:
            values["error"] = error

        statement = (
            update(WorkflowRun)
            .where(
                WorkflowRun.id == run_id,
                WorkflowRun.app_id == self.context.app_id,
                WorkflowRun.user_id == self.context.user_id,
                WorkflowRun.status.in_(ACTIVE_STATUSES),
            )
            .values(**values)
        )
        return (await self.session.execute(statement)).rowcount > 0
