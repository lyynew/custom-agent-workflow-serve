"""Reference agent: one graph that loops between planning and tool use.

This is the template real business agents should follow. It demonstrates the
four things every agent in this service needs:

1. A model chosen by configuration (`biz.model_configs`), never hard-coded.
2. A tool loop expressed as graph edges: `plan -> tools -> plan -> ... -> END`.
3. A guard against unbounded tool usage.
4. A deterministic fallback so the graph still runs without tool calling.

The fallback matters on a practical level: the `echo` provider cannot emit tool
calls, so rule-based routing keeps the same nodes, edges and tool loop
executable in CI and in local demos, with no API key present.
"""

import ast
import logging
import operator
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool, tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from app.config import get_settings
from app.services.models import model_resolver

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是企业内部助手。需要精确结果时优先调用工具，不要凭记忆做算术或猜测当前时间。"
    "确认信息不足时直接说明，不要编造。回答简洁。"
)

LOOP_GUARD_MESSAGE = "工具调用次数已达上限，已停止执行。请把问题拆成更具体的小问题。"

#: Providers whose models support native tool calling. Anything else falls back
#: to deterministic routing.
TOOL_CALLING_PROVIDERS = frozenset({"openai"})

MAX_EXPRESSION_LENGTH = 200
MAX_EXPONENT = 100

_OPERATORS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_EXPRESSION_RE = re.compile(r"[-+(]*\d[\d\s.+\-*/%()]*")
_OPERATOR_CHARS = "+-*/%"

# Full-width punctuation is common in Chinese input; normalise before matching.
_FULLWIDTH = str.maketrans(
    {"（": "(", "）": ")", "＋": "+", "－": "-", "＊": "*", "／": "/", "％": "%", "　": " "}
)


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
def _eval_node(node: ast.AST) -> float:
    """Evaluate an arithmetic AST without touching `eval`.

    Only numeric literals, unary signs and the four operations plus `%`/`**` are
    allowed, so a prompt cannot reach the interpreter.
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            raise ValueError("只支持数字")
        return float(node.value)

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd | ast.USub):
        operand = _eval_node(node.operand)
        return operand if isinstance(node.op, ast.UAdd) else -operand

    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise ValueError("指数过大")
        if isinstance(node.op, ast.Div | ast.Mod) and right == 0:
            raise ValueError("除数不能为 0")
        return _OPERATORS[type(node.op)](left, right)

    raise ValueError("只支持数字与四则运算")


def _format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.10f}".rstrip("0").rstrip(".")


@tool
def calculator(expression: str) -> str:
    """计算算术表达式。仅支持数字、+-*/%、** 和括号，例如 "12*(3+4)"。"""
    text = expression.strip()
    if not text:
        return "无法计算：表达式为空。"
    if len(text) > MAX_EXPRESSION_LENGTH:
        return "无法计算：表达式过长。"

    try:
        tree = ast.parse(text, mode="eval")
        value = _eval_node(tree.body)
    except SyntaxError:
        return f"无法计算 {text!r}：语法错误。"
    except ValueError as exc:
        return f"无法计算 {text!r}：{exc}"

    return f"{text} = {_format_number(value)}"


@tool
def current_time() -> str:
    """返回服务器当前的 UTC 时间，ISO 8601 格式。"""
    return f"当前时间（UTC）：{datetime.now(UTC).isoformat(timespec='seconds')}"


@tool
def text_stats(text: str) -> str:
    """统计一段文本的字符数、词数、行数和前三个高频词。"""
    stripped = text.strip()
    if not stripped:
        return "文本为空，无法统计。"

    words = stripped.split()
    lines = [line for line in stripped.splitlines() if line.strip()]
    counter = Counter(
        word.strip(".,!?;:，。！？；：、\"'()（）[]【】").lower() for word in words
    )
    top = [(word, count) for word, count in counter.most_common(3) if word]
    top_text = "、".join(f"{word}({count})" for word, count in top) or "无"

    return f"字符 {len(stripped)}，词 {len(words)}，行 {len(lines)}；高频词：{top_text}"


TOOLS: list[BaseTool] = [calculator, current_time, text_stats]


# --------------------------------------------------------------------------- #
# Planners: the decision step behind the `plan` node
# --------------------------------------------------------------------------- #
class Planner(Protocol):
    """Decides the next assistant message: a tool call or a final answer."""

    async def plan(self, messages: list[BaseMessage]) -> AIMessage: ...


class ModelPlanner:
    """Native tool calling: the provider decides which tool to use."""

    def __init__(self, model: Any) -> None:
        self._model = model

    async def plan(self, messages: list[BaseMessage]) -> AIMessage:
        reply = await self._model.ainvoke([SystemMessage(content=SYSTEM_PROMPT), *messages])
        return reply if isinstance(reply, AIMessage) else AIMessage(content=_text_of(reply))


class RulePlanner:
    """Deterministic routing for providers without tool calling.

    It keeps the graph honest: same nodes, same tool loop, same state — only the
    decision step is rule based instead of model based.
    """

    def __init__(self, model: Any) -> None:
        self._model = model

    async def plan(self, messages: list[BaseMessage]) -> AIMessage:
        last = messages[-1] if messages else None

        # A tool already ran: report its output instead of looping forever.
        if isinstance(last, ToolMessage):
            return AIMessage(content=_text_of(last))

        call = _rule_tool_call(_last_human_text(messages))
        if call is not None:
            return AIMessage(content="", tool_calls=[call])

        reply = await self._model.ainvoke([SystemMessage(content=SYSTEM_PROMPT), *messages])
        return AIMessage(content=_text_of(reply))


async def _default_planner() -> Planner:
    settings = get_settings()
    resolved = await model_resolver.resolve_full(settings.default_model_config)
    if resolved.provider in TOOL_CALLING_PROVIDERS:
        return ModelPlanner(resolved.model.bind_tools(TOOLS))
    return RulePlanner(resolved.model)


# --------------------------------------------------------------------------- #
# Rule routing helpers
# --------------------------------------------------------------------------- #
_TIME_WORDS = ("时间", "几点", "日期", "今天", "time", "date", "today")
_STATS_WORDS = ("统计", "字数", "词频", "多少字", "stats")
_CALC_WORDS = ("算", "等于", "calculate", "=")


def _text_of(message: BaseMessage) -> str:
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else str(content)


def _last_human_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return _text_of(message)
    return ""


def _extract_expression(text: str) -> str | None:
    """Pull the arithmetic part out of a sentence.

    Requires both a digit and an operator, otherwise "计算第 3 句" would be read
    as an expression. Unbalanced parentheses are rejected: a broken slice would
    only produce a confusing tool error.
    """
    for match in _EXPRESSION_RE.finditer(text.translate(_FULLWIDTH)):
        candidate = match.group(0).strip()
        while candidate.startswith(")"):
            candidate = candidate[1:]
        candidate = candidate.rstrip(_OPERATOR_CHARS + "(").strip()
        if not candidate:
            continue
        if not any(char.isdigit() for char in candidate):
            continue
        if not any(char in candidate for char in _OPERATOR_CHARS):
            continue
        if candidate.count("(") != candidate.count(")"):
            continue
        return candidate
    return None


def _is_mostly_expression(text: str, expression: str) -> bool:
    """True when the message is essentially just the expression.

    "12*(3+4) 是多少" is a calculation request; "1+1 和 2+2 哪个大" is not, and
    should be left to the model.
    """
    residue = text.replace(expression, "", 1)
    residue = re.sub(r"[\s\W_]+", "", residue)
    return len(residue) <= 4


def _rule_tool_call(text: str) -> dict[str, Any] | None:
    lowered = text.lower()

    def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"name": name, "args": args, "id": f"call-{uuid4().hex[:8]}", "type": "tool_call"}

    if any(word in lowered for word in _TIME_WORDS):
        return call("current_time", {})

    # Statistics before arithmetic: "统计这段话的字数" also contains 计算-like words.
    if any(word in lowered for word in _STATS_WORDS):
        payload = text.split("：", 1)[-1] if "：" in text else text
        return call("text_stats", {"text": payload})

    if any(word in lowered for word in _CALC_WORDS):
        expression = _extract_expression(text)
        if expression is not None:
            return call("calculator", {"expression": expression})

    # A bare expression needs no verb: "12*(3+4) 是多少".
    expression = _extract_expression(text)
    if expression is not None and _is_mostly_expression(text.translate(_FULLWIDTH), expression):
        return call("calculator", {"expression": expression})

    return None


# --------------------------------------------------------------------------- #
# Graph
# --------------------------------------------------------------------------- #
def _tool_rounds(messages: list[BaseMessage]) -> int:
    """Tool executions so far.

    Derived from state instead of a counter field, so it survives checkpoint
    restores without a custom state schema.
    """
    return sum(1 for message in messages if isinstance(message, ToolMessage))


def build_graph(
    planner_factory: Callable[[], Awaitable[Planner]] | None = None,
):
    """Assemble the ReAct loop.

    `planner_factory` is injectable so tests can drive the loop deterministically
    without a model or a database.
    """
    resolve_planner = planner_factory or _default_planner

    async def plan_node(state: MessagesState) -> dict[str, Any]:
        planner = await resolve_planner()
        reply = await planner.plan(state["messages"])

        if reply.tool_calls and _tool_rounds(state["messages"]) >= get_settings().agent_max_tool_iterations:
            logger.warning(
                "Agent stopped after %d tool round(s) to avoid an unbounded loop",
                get_settings().agent_max_tool_iterations,
            )
            return {"messages": [AIMessage(content=LOOP_GUARD_MESSAGE)]}

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

    return builder.compile(name="assistant_agent")


graph = build_graph()
