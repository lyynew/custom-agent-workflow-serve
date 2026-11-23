"""Business FastAPI app mounted inside Aegra.

Aegra's lifespan wraps ours, so the runtime database is initialized before
`application_store.warmup()` runs.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agents import agent_registry
from app.api.audit import AuditMiddleware
from app.api.errors import (
    APIError,
    api_error_handler,
    auth_error_handler,
    resource_not_found_handler,
)
from app.api.v1.router import router as v1_router
from app.auth.errors import AuthError
from app.auth.store import application_store
from app.cache.redis import close_redis
from app.database.session import dispose_engine
from app.repositories.errors import ResourceNotFound
from app.services.workflows import task_launcher
from app.workflows import workflow_registry

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Fail fast on a missing migration or an unresolvable secret_ref.
    await application_store.warmup()
    agent_registry.validate_config()
    workflow_registry.validate_config()

    # No blanket "mark everything interrupted" on startup: with more than one
    # replica that would kill runs owned by other processes. A run whose poller
    # died is finalised by read-time reconciliation instead (see WorkflowService).

    logger.info("AI service started with %d application(s)", len(application_store.cached_app_ids))
    try:
        yield
    finally:
        # Stop background pollers before the pool goes away.
        await task_launcher.shutdown()
        await close_redis()
        await dispose_engine()


app = FastAPI(
    title="AI Service Engine",
    version="0.1.0",
    description="Multi-tenant Agent / Workflow service engine",
    lifespan=lifespan,
)

app.add_middleware(AuditMiddleware)

app.add_exception_handler(APIError, api_error_handler)
app.add_exception_handler(AuthError, auth_error_handler)
app.add_exception_handler(ResourceNotFound, resource_not_found_handler)

app.include_router(v1_router, prefix="/api/v1")
