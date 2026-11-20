"""Second demo agent: reverses the last human message.

Kept trivial on purpose; it proves multiple registered agents are independently
addressable without needing an external model.
"""

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph


def reverse_node(state: MessagesState) -> dict:
    last = state["messages"][-1]
    if not isinstance(last, HumanMessage):
        return {"messages": [AIMessage(content="Reverse agent 只处理用户消息。")]}

    content = last.content if isinstance(last.content, str) else str(last.content)
    return {"messages": [AIMessage(content=content[::-1])]}


builder = StateGraph(MessagesState)
builder.add_node("reverse", reverse_node)
builder.add_edge(START, "reverse")
builder.add_edge("reverse", END)

graph = builder.compile(name="reverse_agent")
