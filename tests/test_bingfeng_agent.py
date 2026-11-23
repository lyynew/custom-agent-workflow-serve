"""兵峰耘农：`create_agent` 组装、Tavily 搜索工具与每轮检索上限。"""

from collections.abc import Sequence
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool, tool
from langchain_tavily import TavilySearch

from app.agents.implementations import bingfeng_agent
from app.agents.implementations.bingfeng_agent import SYSTEM_PROMPT, build, build_agent
from app.agents.pesticide import TOOL_NAME as PESTICIDE_TOOL_NAME
from app.agents.pesticide import recommend_pesticides
from app.agents.web_search import SEARCH_TOOL_NAME, build_web_search_tool
from app.repositories.model_configs import ModelConfigRepository
from app.services.models import model_resolver


def test_prompt_is_loaded_from_the_markdown_file():
    assert "兵峰耘农" in SYSTEM_PROMPT
    assert "PesticideRecommendation" in SYSTEM_PROMPT
    # The safety section must survive any future edit of the prompt file.
    assert "安全间隔期" in SYSTEM_PROMPT


async def _seed_demo_model(session) -> None:
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


async def test_build_answers_without_tools_on_a_non_tool_provider(seeded_apps, session):
    await _seed_demo_model(session)

    graph = await build()
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "小麦叶子发黄怎么办"}]})

    assert result["messages"][-1].content == "model: 小麦叶子发黄怎么办"
    assert not any(isinstance(m, ToolMessage) for m in result["messages"])
    assert "tools" not in graph.get_graph().nodes


async def test_graph_is_registered_under_the_expected_name(seeded_apps, session):
    await _seed_demo_model(session)

    assert (await build()).name == bingfeng_agent.AGENT_ID


def test_search_tool_is_the_framework_integration(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")

    search_tool = build_web_search_tool()

    assert isinstance(search_tool, TavilySearch)
    assert search_tool.name == SEARCH_TOOL_NAME
    assert "农业" in search_tool.description


def test_missing_key_leaves_the_agent_without_a_search_tool(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    assert build_web_search_tool() is None


class _SearchAddictedModel(BaseChatModel):
    """A model that keeps asking for searches; the limiter must stop the bleeding."""

    rounds: int = 6

    @property
    def _llm_type(self) -> str:
        return "search-addicted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> BaseChatModel:  # noqa: ARG002
        return self

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        seen = sum(1 for message in messages if isinstance(message, ToolMessage))
        if seen >= self.rounds:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="答案"))])

        call = {"name": SEARCH_TOOL_NAME, "args": {"query": f"q{seen}"}, "id": f"call-{seen}"}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))])


@pytest.fixture
def counted_search_tool() -> tuple[BaseTool, dict[str, int]]:
    counter = {"calls": 0}

    @tool(SEARCH_TOOL_NAME)
    def search(query: str) -> str:  # noqa: ARG001
        """搜索。"""
        counter["calls"] += 1
        return f"结果 {counter['calls']}"

    return search, counter


@pytest.mark.parametrize("limit", [1, 3])
async def test_searches_are_capped_per_run(counted_search_tool, limit):
    search_tool, counter = counted_search_tool
    graph = build_agent(model=_SearchAddictedModel(), tools=[search_tool], max_searches=limit)

    result = await graph.ainvoke({"messages": [{"role": "user", "content": "小麦赤霉病"}]})

    assert counter["calls"] == limit
    assert result["messages"][-1].content == "答案"
    blocked = [m for m in result["messages"] if isinstance(m, ToolMessage) and m.status == "error"]
    assert len(blocked) == _SearchAddictedModel().rounds - limit


async def test_batch_of_parallel_searches_is_trimmed_to_the_limit(counted_search_tool):
    search_tool, counter = counted_search_tool

    class _BatchModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "batch"

        def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> BaseChatModel:  # noqa: ARG002
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:  # noqa: ARG002
            if any(isinstance(message, ToolMessage) for message in messages):
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="答案"))])
            calls = [
                {"name": SEARCH_TOOL_NAME, "args": {"query": f"q{i}"}, "id": f"call-{i}"} for i in range(4)
            ]
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=calls))])

    graph = build_agent(model=_BatchModel(), tools=[search_tool], max_searches=2)

    await graph.ainvoke({"messages": [{"role": "user", "content": "小麦赤霉病"}]})

    assert counter["calls"] == 2


def test_no_tools_means_no_tool_node():
    assert "tools" not in build_agent(_SearchAddictedModel(), None, 3).get_graph().nodes


def test_a_zero_search_budget_adds_no_search_limiter(counted_search_tool):
    search_tool, _ = counted_search_tool

    nodes = build_agent(_SearchAddictedModel(), [search_tool], 0).get_graph().nodes

    assert not any(SEARCH_TOOL_NAME in node for node in nodes)


def test_pesticide_and_search_limits_live_in_the_graph(counted_search_tool):
    search_tool, _ = counted_search_tool

    nodes = build_agent(
        _SearchAddictedModel(), [recommend_pesticides, search_tool], 3
    ).get_graph().nodes

    assert any(PESTICIDE_TOOL_NAME in node for node in nodes)
    assert any(SEARCH_TOOL_NAME in node for node in nodes)


async def test_system_prompt_carries_todays_date():
    class _SystemEchoModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "system-echo"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:  # noqa: ARG002
            system = next((m for m in messages if m.type == "system"), None)
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content=system.content if system else ""))]
            )

    graph = build_agent(_SystemEchoModel(), None, 3)

    result = await graph.ainvoke({"messages": [{"role": "user", "content": "你好"}]})

    text = result["messages"][-1].content
    assert "兵峰耘农" in text
    # Recomputed at call time, so a long-running server never serves a stale date.
    assert bingfeng_agent._today() in text
