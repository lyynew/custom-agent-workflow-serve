"""Workflow listing, execution and run inspection."""

import logging
from datetime import datetime
from uuid import UUID

from aegra_api.core.auth_deps import require_auth
from aegra_api.models.auth import User
from fastapi import APIRouter, Depends, Query, Response
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_auth_context,
    get_runtime_adapter,
    get_runtime_credentials,
    get_task_launcher,
)
from app.api.errors import APIError
from app.api.v1.helpers import ensure_registered_workflow, ensure_workflow_allowed
from app.api.v1.schemas import (
    WorkflowExecuteRequest,
    WorkflowOut,
    WorkflowResultOut,
    WorkflowRunList,
    WorkflowRunOut,
)
from app.auth.context import AuthContext
from app.auth.permissions import is_allowed
from app.database.session import get_session
from app.repositories.workflow_runs import WorkflowRunRepository
from app.runtime.adapter import RuntimeAdapter, RuntimeCredentials
from app.runtime.aegra import RuntimeCallError
from app.services.workflows import (
    TERMINAL_STATUSES,
    TaskLauncher,
    WorkflowCapacityReached,
    WorkflowService,
)
from app.workflows import workflow_registry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["workflows"])


def _summarise(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors()[:3]:
        location = ".".join(str(item) for item in error.get("loc", ()))
        message = str(error.get("msg", "invalid value"))
        parts.append(f"{location}: {message}" if location else message)
    return "; ".join(parts)


@router.get("/workflows", response_model=list[WorkflowOut])
async def list_workflows(user: User = Depends(require_auth)) -> list[WorkflowOut]:
    allowed = list(getattr(user, "allowed_workflows", []))
    return [
        WorkflowOut(
            workflow_id=workflow.workflow_id,
            name=workflow.name,
            description=workflow.description,
            tags=list(workflow.tags),
            input_schema=workflow.input_schema(),
        )
        for workflow in workflow_registry.list()
        if is_allowed(allowed, workflow.workflow_id)
    ]


# Run routes are declared before `/{workflow_id}/execute` so the literal
# `runs` segment is never mistaken for a workflow id.


@router.get("/workflows/runs", response_model=WorkflowRunList)
async def list_runs(
    workflow_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    before: datetime | None = None,
    context: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> WorkflowRunList:
    repository = WorkflowRunRepository(session, context)
    runs = await repository.list(workflow_id=workflow_id, status=status, limit=limit, before=before)
    next_before = runs[-1].created_at if len(runs) == limit else None
    return WorkflowRunList(
        items=[WorkflowRunOut.model_validate(run) for run in runs],
        next_before=next_before,
    )


@router.get("/workflows/runs/{run_id}", response_model=WorkflowRunOut)
async def get_run(
    run_id: UUID,
    context: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> WorkflowRunOut:
    repository = WorkflowRunRepository(session, context)
    run = await repository.require(run_id)
    return WorkflowRunOut.model_validate(run)


@router.get("/workflows/runs/{run_id}/result", response_model=WorkflowResultOut)
async def get_run_result(
    run_id: UUID,
    context: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> WorkflowResultOut:
    repository = WorkflowRunRepository(session, context)
    run = await repository.require(run_id)

    if run.status == "succeeded":
        return WorkflowResultOut(
            run_id=run.id,
            workflow_id=run.workflow_id,
            status=run.status,
            output=run.output or {},
        )

    if run.status not in TERMINAL_STATUSES:
        raise APIError(409, "run_not_finished", f"Run is still {run.status}")

    error = run.error or {}
    code = {
        "failed": "workflow_failed",
        "timed_out": "workflow_timeout",
    }.get(run.status, "workflow_cancelled")
    message = str(error.get("message") or f"Run ended with status {run.status}")
    raise APIError(409, code, message)


@router.post("/workflows/runs/{run_id}/cancel", response_model=WorkflowRunOut)
async def cancel_run(
    run_id: UUID,
    context: AuthContext = Depends(get_auth_context),
    credentials: RuntimeCredentials = Depends(get_runtime_credentials),
    runtime: RuntimeAdapter = Depends(get_runtime_adapter),
    session: AsyncSession = Depends(get_session),
) -> WorkflowRunOut:
    service = WorkflowService(session, context, credentials, runtime)
    run = await service.cancel(run_id)

    if run.status != "cancelled":
        raise APIError(409, "run_not_cancellable", f"Run is already {run.status}")

    return WorkflowRunOut.model_validate(run)


@router.post("/workflows/{workflow_id}/execute", response_model=WorkflowRunOut)
async def execute_workflow(
    workflow_id: str,
    payload: WorkflowExecuteRequest,
    response: Response,
    user: User = Depends(require_auth),
    context: AuthContext = Depends(get_auth_context),
    credentials: RuntimeCredentials = Depends(get_runtime_credentials),
    runtime: RuntimeAdapter = Depends(get_runtime_adapter),
    launcher: TaskLauncher = Depends(get_task_launcher),
    session: AsyncSession = Depends(get_session),
) -> WorkflowRunOut:
    ensure_workflow_allowed(user, workflow_id)
    definition = ensure_registered_workflow(workflow_id)

    try:
        normalised = definition.validate_input(payload.input)
    except ValidationError as exc:
        raise APIError(422, "invalid_input", _summarise(exc)) from exc

    service = WorkflowService(session, context, credentials, runtime)
    try:
        run = await service.start(workflow_id, normalised)
    except WorkflowCapacityReached as exc:
        raise APIError(429, "too_many_runs", "Too many workflow runs in progress, retry later") from exc
    except RuntimeCallError as exc:
        logger.warning("Workflow runtime call failed: %s", exc)
        raise APIError(502, "runtime_error", "Workflow runtime call failed") from exc

    if payload.mode == "sync":
        status = await service.wait_for(run.id)
        await session.refresh(run)

        if status == "timed_out":
            # The run is cancelled, not silently abandoned; poll mode is the
            # supported way to run anything longer than the sync window.
            raise APIError(
                504,
                "workflow_timeout",
                "Workflow exceeded the synchronous limit; use mode=async",
            )
        return WorkflowRunOut.model_validate(run)

    launcher.launch(f"workflow-run-{run.id}", service.wait_for(run.id))
    response.status_code = 202
    return WorkflowRunOut.model_validate(run)
