"""兵峰耘农：农业助手，带联网搜索与每次对话的检索次数上限。

提示词单独放在 `app/agents/prompts/bingfeng_yunnong.md`，方便产品/农技人员直接改，
不必碰代码。

关于 `AGENT_MAX_WEB_SEARCHES`：仅靠提示词约束"最多搜 3 次"不可靠，模型会超。
这里做成硬约束——预算用尽后不再把 `web_search` 绑定给模型，模型即使想调也没有
这个工具；同时对单条回复里超出预算的调用再做一次裁剪。
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from app.agents.tools import TOOL_CALLING_PROVIDERS
from app.agents.web_search import web_search
from app.config import get_settings
from app.services.models import model_resolver

logger = logging.getLogger(__name__)

AGENT_ID = "bingfeng_agent"
SEARCH_TOOL_NAME = web_search.name
TOOLS = [web_search]

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "bingfeng_yunnong.md"
SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8").strip()


def _text_of(message: BaseMessage) -> str:
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else str(content)


def search_uses(messages: Sequence[BaseMessage]) -> int:
    """How many web searches this conversation turn has already spent.

    Read from state rather than a counter field, so the budget survives
    checkpoint restores and never leaks across conversations.
    """
    return sum(
        1 for message in messages if isinstance(message, ToolMessage) and message.name == SEARCH_TOOL_NAME
    )


def trim_search_calls(message: AIMessage, remaining: int) -> AIMessage:
    """Drop search calls that exceed what is left of the budget."""
    if not message.tool_calls:
        return message

    kept: list[dict[str, Any]] = []
    used = 0
    for call in message.tool_calls:
        if call.get("name") == SEARCH_TOOL_NAME:
            if used >= remaining:
                continue
            used += 1
        kept.append(call)

    if len(kept) == len(message.tool_calls):
        return message
    return message.model_copy(update={"tool_calls": kept})


def _with_clock(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """Tell the model today's date; otherwise it guesses and searches for stale years."""
    today = datetime.now(UTC).astimezone().strftime("%Y-%m-%d")
    return [SystemMessage(content=f"{SYSTEM_PROMPT}\n\n今天是 {today}。"), *messages]


def build_graph():
    async def plan_node(state: MessagesState) -> dict[str, Any]:
        settings = get_settings()
        remaining = max(0, settings.agent_max_web_searches - search_uses(state["messages"]))

        resolved = await model_resolver.resolve_full(settings.model_config_for(AGENT_ID))
        supports_tools = resolved.provider in TOOL_CALLING_PROVIDERS

        # Budget spent (or a provider without tool calling): offer no tools at
        # all, so the model physically cannot exceed the limit.
        can_search = supports_tools and remaining > 0
        model = resolved.model.bind_tools(TOOLS) if can_search else resolved.model

        prompt = _with_clock(state["messages"])
        reply = await model.ainvoke(prompt)

        if not isinstance(reply, AIMessage):
            reply = AIMessage(content=_text_of(reply))

        if supports_tools:
            reply = trim_search_calls(reply, remaining)

        # Budget exhausted and the model produced neither an answer nor a usable
        # call: ask once more with no tools so the user still gets an answer.
        if not reply.tool_calls and not _text_of(reply).strip():
            reply = await resolved.model.ainvoke(prompt)
            if not isinstance(reply, AIMessage):
                reply = AIMessage(content=_text_of(reply))

        return {"messages": [reply]}

    def route(state: MessagesState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    builder = StateGraph(MessagesState)
    builder.add_node("plan", plan_node)
    builder.add_node("tools", ToolNode(TOOLS))
    builder.add_edge(START, "plan")
    builder.add_conditional_edges("plan", route, {"tools": "tools", END: END})
    builder.add_edge("tools", "plan")

    return builder.compile(name=AGENT_ID)


graph = build_graph()
