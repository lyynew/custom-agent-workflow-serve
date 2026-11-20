"""The reference agent: planning, tool loop and its guard rails."""

from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.implementations import assistant_agent
from app.agents.implementations.assistant_agent import (
    LOOP_GUARD_MESSAGE,
    build_graph,
    calculator,
    graph,
)
from app.config import get_settings
from app.repositories.model_configs import ModelConfigRepository
from app.services.models import model_resolver


@pytest.fixture(autouse=True)
def clear_model_cache():
    model_resolver.clear()
    yield
    model_resolver.clear()


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


async def test_plain_chat_goes_through_the_configured_model(seeded_apps, session):
    await _seed_demo_model(session)

    result = await graph.ainvoke({"messages": [{"role": "user", "content": "你好"}]})

    assert result["messages"][-1].content == "model: 你好"
    # No tool was needed, so the loop ended on the first plan step.
    assert not any(isinstance(m, ToolMessage) for m in result["messages"])


async def test_calculator_is_called_and_its_result_is_reported(seeded_apps, session):
    await _seed_demo_model(session)

    result = await graph.ainvoke({"messages": [{"role": "user", "content": "帮我计算 12*(3+4)"}]})

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].name == "calculator"
    assert tool_messages[0].content == "12*(3+4) = 84"
    assert result["messages"][-1].content == "12*(3+4) = 84"


async def test_time_question_uses_the_clock_tool(seeded_apps, session):
    await _seed_demo_model(session)

    result = await graph.ainvoke({"messages": [{"role": "user", "content": "现在几点了？"}]})

    assert "当前时间（UTC）" in result["messages"][-1].content


async def test_text_stats_uses_the_supplied_payload(seeded_apps, session):
    await _seed_demo_model(session)

    result = await graph.ainvoke({"messages": [{"role": "user", "content": "统计一下：hello world hello"}]})

    reply = result["messages"][-1].content
    assert "词 3" in reply
    assert "hello(2)" in reply


async def test_expression_with_leading_parenthesis_is_not_truncated(seeded_apps, session):
    """Regression: the regex used to start at the digit and drop the '('."""
    await _seed_demo_model(session)

    result = await graph.ainvoke({"messages": [{"role": "user", "content": "帮我计算 (12+8)*3/2"}]})

    assert result["messages"][-1].content == "(12+8)*3/2 = 30"


async def test_colloquial_wording_still_routes_to_the_calculator(seeded_apps, session):
    """Regression: "帮我算 6*7" used to fall through to plain chat."""
    await _seed_demo_model(session)

    result = await graph.ainvoke({"messages": [{"role": "user", "content": "帮我算 6*7"}]})

    assert result["messages"][-1].content == "6*7 = 42"


async def test_bare_expression_without_a_verb_is_still_calculated(seeded_apps, session):
    await _seed_demo_model(session)

    result = await graph.ainvoke({"messages": [{"role": "user", "content": "12*(3+4) 是多少"}]})

    assert result["messages"][-1].content == "12*(3+4) = 84"


async def test_expression_inside_a_comparison_is_left_to_the_model(seeded_apps, session):
    await _seed_demo_model(session)

    result = await graph.ainvoke({"messages": [{"role": "user", "content": "1+1 和 2+2 哪个大"}]})

    assert not any(isinstance(m, ToolMessage) for m in result["messages"])


async def test_number_without_operator_does_not_trigger_the_tool(seeded_apps, session):
    await _seed_demo_model(session)

    result = await graph.ainvoke({"messages": [{"role": "user", "content": "预算 2024 年"}]})

    assert result["messages"][-1].content == "model: 预算 2024 年"
    assert not any(isinstance(m, ToolMessage) for m in result["messages"])


async def test_fullwidth_expression_is_normalised(seeded_apps, session):
    await _seed_demo_model(session)

    result = await graph.ainvoke({"messages": [{"role": "user", "content": "计算（12＋8）＊3"}]})

    assert result["messages"][-1].content == "(12+8)*3 = 60"


async def test_calculator_refuses_non_arithmetic_input():
    # The tool must never reach the interpreter, even when a model asks it to.
    assert "无法计算" in calculator.func("__import__('os').system('ls')")
    assert "除数不能为 0" in calculator.func("1/0")
    assert "指数过大" in calculator.func("9**9999")


async def test_multi_turn_keeps_prior_messages(seeded_apps, session):
    await _seed_demo_model(session)

    first = await graph.ainvoke({"messages": [{"role": "user", "content": "你好"}]})
    second = await graph.ainvoke(
        {
            "messages": [
                *first["messages"],
                HumanMessage(content="再算一下 2+2"),
            ]
        }
    )

    assert second["messages"][-1].content == "2+2 = 4"
    # previous turn (2) + question + tool call + tool result + answer
    assert len(second["messages"]) == 6
    assert isinstance(second["messages"][0], HumanMessage)


async def test_loop_guard_stops_a_planner_that_never_finishes(seeded_apps, session):
    await _seed_demo_model(session)

    class StuckPlanner:
        async def plan(self, messages):
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "current_time",
                        "args": {},
                        "id": f"call-{uuid4().hex[:8]}",
                        "type": "tool_call",
                    }
                ],
            )

    stuck_graph = build_graph(planner_factory=_stuck_factory(StuckPlanner))

    result = await stuck_graph.ainvoke({"messages": [{"role": "user", "content": "loop"}]})

    rounds = sum(1 for m in result["messages"] if isinstance(m, ToolMessage))
    assert rounds == get_settings().agent_max_tool_iterations
    assert result["messages"][-1].content == LOOP_GUARD_MESSAGE


def _stuck_factory(planner_type):
    async def factory():
        return planner_type()

    return factory


async def test_rule_planner_falls_back_to_the_model_when_no_tool_matches(seeded_apps, session):
    await _seed_demo_model(session)

    planner = await assistant_agent._default_planner()
    reply = await planner.plan([HumanMessage(content="随便聊聊")])

    assert reply.content == "model: 随便聊聊"
    assert not reply.tool_calls
