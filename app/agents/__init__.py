from app.agents.base import AgentDefinition
from app.agents.registry import agent_registry

agent_registry.register(
    AgentDefinition(
        agent_id="echo_agent",
        name="Echo Agent",
        description="Deterministic demo agent with a server-time tool.",
        tags=("demo", "tool"),
    )
)

__all__ = ["AgentDefinition", "agent_registry"]
