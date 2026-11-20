from aegra_api.models.auth import User

from app.agents import agent_registry
from app.agents.base import AgentDefinition
from app.agents.registry import AgentNotFound
from app.api.errors import APIError
from app.auth.permissions import ensure_allowed


def ensure_registered_agent(agent_id: str) -> AgentDefinition:
    try:
        return agent_registry.get(agent_id)
    except AgentNotFound as exc:
        raise APIError(404, "agent_not_found", "Agent not found") from exc


def ensure_agent_allowed(user: User, agent_id: str) -> None:
    ensure_allowed(list(getattr(user, "allowed_agents", [])), agent_id)
