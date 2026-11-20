"""Business FastAPI app mounted inside Aegra.

Aegra's lifespan wraps ours, so the runtime database is initialized before
`application_store.warmup()` runs.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import func, update

from app.agents import agent_registry
from app.api.errors import (
    APIError,
    api_error_handler,
    auth_error_handler,
    resource_not_found_handler,
)
from app.api.v1.router import router as v1_router
from app.auth.errors import AuthError
from app.auth.store import application_store
from app.database.session import dispose_engine, get_session_factory
from app.models.workflow_run import WorkflowRun
from app.repositories.errors import ResourceNotFound

logger = logging.getLogger(__name__)


async def _mark_interrupted_runs() -> None:
    """No Redis in v1: an in-flight run cannot survive a process restart.

    Marking it explicitly is better than leaving it `running` forever. This is
    safe for the single-instance deployment documented in ADR 0001.
    """
    async with get_session_factory()() as session:
        result = await session.execute(
            update(WorkflowRun)
            .where(WorkflowRun.status.in_(("pending", "running")))
            .values(status="interrupted", finished_at=func.now())
        )
        await session.commit()

    if result.rowcount:
        logger.warning("Marked %d in-flight workflow run(s) as interrupted", result.rowcount)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Fail fast on a missing migration or an unresolvable secret_ref.
    await application_store.warmup()
    agent_registry.validate_config()
    await _mark_interrupted_runs()

    logger.info("AI service started with %d application(s)", len(application_store.cached_app_ids))
    try:
        yield
    finally:
        await dispose_engine()


app = FastAPI(
    title="AI Service Engine",
    version="0.1.0",
    description="Multi-tenant Agent / Workflow service engine",
    lifespan=lifespan,
)

app.add_exception_handler(APIError, api_error_handler)
app.add_exception_handler(AuthError, auth_error_handler)
app.add_exception_handler(ResourceNotFound, resource_not_found_handler)

app.include_router(v1_router, prefix="/api/v1")
