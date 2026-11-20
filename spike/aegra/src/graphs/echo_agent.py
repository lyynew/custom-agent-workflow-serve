"""Deterministic streaming agent used to validate SSE passthrough.

No external model API is involved: GenericFakeChatModel splits on whitespace so
Aegra emits several message chunks, which is exactly what the SSE layer needs
to carry.
"""

import itertools

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph

_model = GenericFakeChatModel(
    messages=itertools.cycle(
        [AIMessage(content="你好 这是 来自 echo agent 的 流式 回复 收到 你的 消息")]
    )
)


async def respond(state: MessagesState) -> dict:
    response = await _model.ainvoke(state["messages"])
    return {"messages": [response]}


builder = StateGraph(MessagesState)
builder.add_node("respond", respond)
builder.add_edge(START, "respond")
builder.add_edge("respond", END)

graph = builder.compile(name="echo_agent")
