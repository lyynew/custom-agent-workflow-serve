"""Tools shared by the example agents.

Kept in one module so the `create_agent` version and the hand-written
`StateGraph` version use exactly the same tools — the difference between the two
implementations is the orchestration style, not the capabilities.
"""

import ast
import operator
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime

from langchain_core.tools import BaseTool, tool

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

# Full-width punctuation is common in Chinese input; normalise before matching.
FULLWIDTH = str.maketrans(
    {"（": "(", "）": ")", "＋": "+", "－": "-", "＊": "*", "／": "/", "％": "%", "　": " "}
)

__all__ = [
    "FULLWIDTH",
    "TOOLS",
    "calculator",
    "current_time",
    "text_stats",
    "MAX_EXPRESSION_LENGTH",
    "MAX_EXPONENT",
]
