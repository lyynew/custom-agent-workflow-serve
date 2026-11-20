"""Agent whose model is chosen by configuration, not by code.

It resolves a `biz.model_configs` row at call time, so the same graph can talk to
a real provider in production and to the deterministic `echo` provider in tests
and local demos, with no code change.
"""

from langchain_core.messages import SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from app.config import get_settings
from app.services.models import model_resolver

SYSTEM_PROMPT = (
    "You are an internal business assistant. Answer concisely. "
    "If you do not know something, say so instead of guessing."
)


async def agent_node(state: MessagesState) -> dict:
    model = await model_resolver.resolve(get_settings().model_config_for("llm_agent"))
    reply = await model.ainvoke([SystemMessage(content=SYSTEM_PROMPT), *state["messages"]])
    return {"messages": [reply]}


builder = StateGraph(MessagesState)
builder.add_node("agent", agent_node)
builder.add_edge(START, "agent")
builder.add_edge("agent", END)

graph = builder.compile(name="llm_agent")
