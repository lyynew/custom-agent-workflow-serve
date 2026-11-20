from fastapi import APIRouter, Depends

from app.api.quota import enforce_quota
from app.api.v1 import agents, conversations, system, workflows

router = APIRouter()

# Quotas are enforced once, at router level, so a new route cannot forget them.
_quota = [Depends(enforce_quota)]

router.include_router(system.router, dependencies=_quota)
router.include_router(agents.router, dependencies=_quota)
router.include_router(conversations.router, dependencies=_quota)
router.include_router(workflows.router, dependencies=_quota)
