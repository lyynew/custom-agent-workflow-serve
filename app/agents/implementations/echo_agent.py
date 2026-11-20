"""Deterministic demo agent with one real tool.

It exists to exercise the engine end to end without an external model API:
multi-turn state, SSE deltas, tool call/result events. Real agents can be added
next to it with the same LangGraph contract.
"""

from datetime import UTC, datetime
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode


@tool
def get_server_time() -> str:
    """Return the server's current UTC time in ISO 8601 format."""
    return datetime.now(UTC).isoformat()


def agent_node(state: MessagesState) -> dict:
    last = state["messages"][-1]

    if isinstance(last, ToolMessage):
        return {"messages": [AIMessage(content=f"工具返回：{last.content}")]}

    if isinstance(last, HumanMessage):
        content = last.content if isinstance(last.content, str) else str(last.content)
        if "时间" in content or "time" in content.lower():
            call = {
                "name": "get_server_time",
                "args": {},
                "id": f"call-{uuid4().hex[:8]}",
                "type": "tool_call",
            }
            return {"messages": [AIMessage(content="", tool_calls=[call])]}
        return {"messages": [AIMessage(content=f"Echo: {content}")]}

    return {"messages": [AIMessage(content="Echo agent 已收到。")]}


def route_after_agent(state: MessagesState) -> str:
    last = state["messages"][-1]
    return "tools" if isinstance(last, AIMessage) and last.tool_calls else END


builder = StateGraph(MessagesState)
builder.add_node("agent", agent_node)
builder.add_node("tools", ToolNode([get_server_time]))
builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", route_after_agent, {"tools": "tools", END: END})
builder.add_edge("tools", "agent")

graph = builder.compile(name="echo_agent")
