"""The demo graphs themselves: reply, tool call and independent behavior."""

from app.agents.implementations.echo_agent import graph
from app.agents.implementations.reverse_agent import graph as reverse_graph


async def test_echo_graph_replies_without_tool():
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "hello"}]})

    assert result["messages"][-1].content == "Echo: hello"


async def test_echo_graph_calls_server_time_tool():
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "现在时间"}]})

    assert "工具返回" in result["messages"][-1].content


async def test_reverse_graph_reverses_text():
    result = await reverse_graph.ainvoke({"messages": [{"role": "user", "content": "abcd"}]})

    assert result["messages"][-1].content == "dcba"
