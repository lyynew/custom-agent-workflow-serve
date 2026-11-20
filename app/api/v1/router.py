from fastapi import APIRouter

from app.api.v1 import agents, conversations, system

router = APIRouter()
router.include_router(system.router)
router.include_router(agents.router)
router.include_router(conversations.router)
