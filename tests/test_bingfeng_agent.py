"""兵峰耘农：提示词装载、联网搜索工具与检索预算。"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents import web_search as search_module
from app.agents.implementations import bingfeng_agent
from app.agents.implementations.bingfeng_agent import (
    SYSTEM_PROMPT,
    graph,
    search_uses,
    trim_search_calls,
)
from app.agents.web_search import web_search
from app.repositories.model_configs import ModelConfigRepository
from app.services.models import model_resolver


def test_prompt_is_loaded_from_the_markdown_file():
    assert "兵峰耘农" in SYSTEM_PROMPT
    assert "PesticideRecommendation" in SYSTEM_PROMPT
    # The safety section must survive any future edit of the prompt file.
    assert "安全间隔期" in SYSTEM_PROMPT


def test_search_uses_counts_only_search_tool_messages():
    messages = [
        HumanMessage(content="小麦叶子发黄"),
        ToolMessage(content="结果", name="web_search", tool_call_id="1"),
        ToolMessage(content="3+4=7", name="calculator", tool_call_id="2"),
        ToolMessage(content="结果2", name="web_search", tool_call_id="3"),
    ]

    assert search_uses(messages) == 2


def _search_call(call_id: str) -> dict:
    return {"name": "web_search", "args": {"query": "x"}, "id": call_id, "type": "tool_call"}


def test_calls_over_the_remaining_budget_are_dropped():
    reply = AIMessage(content="", tool_calls=[_search_call("1"), _search_call("2"), _search_call("3")])

    trimmed = trim_search_calls(reply, remaining=1)

    assert [call["id"] for call in trimmed.tool_calls] == ["1"]


def test_non_search_calls_are_never_dropped():
    reply = AIMessage(
        content="",
        tool_calls=[
            _search_call("1"),
            {"name": "calculator", "args": {"expression": "1+1"}, "id": "2", "type": "tool_call"},
        ],
    )

    trimmed = trim_search_calls(reply, remaining=0)

    assert [call["name"] for call in trimmed.tool_calls] == ["calculator"]


class _FakeTavily:
    def __init__(self, results=None, error: Exception | None = None) -> None:
        self._results = results or []
        self._error = error

    def search(self, **_kwargs):
        if self._error:
            raise self._error
        return {"results": self._results}


def test_web_search_returns_readable_results(monkeypatch):
    monkeypatch.setattr(
        search_module,
        "_client",
        _FakeTavily(
            [
                {
                    "title": "河南小麦赤霉病预警",
                    "url": "https://example.com/a",
                    "content": "预计 4 月下旬进入流行期。",
                    "published_date": "2026-04-20",
                }
            ]
        ),
    )

    output = web_search.func("河南 小麦 赤霉病 预警")

    assert "河南小麦赤霉病预警" in output
    assert "https://example.com/a" in output
    assert "2026-04-20" in output


def test_web_search_degrades_to_text_on_provider_error(monkeypatch):
    monkeypatch.setattr(search_module, "_client", _FakeTavily(error=RuntimeError("boom")))

    output = web_search.func("any query")

    # The agent must still be able to answer, so this is text, not an exception.
    assert output.startswith("搜索失败")


async def test_agent_answers_without_tools_on_a_non_tool_provider(seeded_apps, session):
    await ModelConfigRepository(session).upsert(
        {
            "name": "demo",
            "provider": "echo",
            "model_name": "echo-1",
            "temperature": 0.0,
            "timeout_seconds": 30,
            "extra": {"prefix": "model"},
            "enabled": True,
        }
    )
    await session.commit()
    model_resolver.clear()

    result = await graph.ainvoke({"messages": [{"role": "user", "content": "小麦叶子发黄怎么办"}]})

    assert result["messages"][-1].content == "model: 小麦叶子发黄怎么办"
    assert not any(isinstance(m, ToolMessage) for m in result["messages"])


def test_graph_is_registered_under_the_expected_name():
    assert graph.name == bingfeng_agent.AGENT_ID


@pytest.mark.parametrize("remaining", [0, 1, 3])
def test_trimming_is_idempotent(remaining):
    reply = AIMessage(content="done")

    assert trim_search_calls(reply, remaining).content == "done"
