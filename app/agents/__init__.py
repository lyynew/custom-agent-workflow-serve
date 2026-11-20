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

agent_registry.register(
    AgentDefinition(
        agent_id="reverse_agent",
        name="Reverse Agent",
        description="Deterministic demo agent that reverses the user's text.",
        tags=("demo",),
    )
)

agent_registry.register(
    AgentDefinition(
        agent_id="llm_agent",
        name="LLM Agent",
        description="Model-backed agent; the provider comes from biz.model_configs.",
        tags=("llm",),
    )
)

agent_registry.register(
    AgentDefinition(
        agent_id="assistant_agent",
        name="Assistant Agent",
        description="Reference agent: model-driven planning with a calculator, clock and text-stats tool loop.",
        tags=("llm", "tool", "reference"),
    )
)

__all__ = ["AgentDefinition", "agent_registry"]
