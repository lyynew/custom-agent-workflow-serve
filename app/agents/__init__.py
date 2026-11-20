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
        description="Hand-written StateGraph: plan/tools loop with a loop guard.",
        tags=("llm", "tool", "reference"),
    )
)

agent_registry.register(
    AgentDefinition(
        agent_id="simple_agent",
        name="Simple Agent",
        description="Built with LangChain create_agent: model + tools + system prompt.",
        tags=("llm", "tool", "demo"),
    )
)

__all__ = ["AgentDefinition", "agent_registry"]
