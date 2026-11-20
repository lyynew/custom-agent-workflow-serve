"""The demo graph itself: plain reply and tool-assisted reply."""

from app.agents.implementations.echo_agent import graph


async def test_echo_graph_replies_without_tool():
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "hello"}]})

    assert result["messages"][-1].content == "Echo: hello"


async def test_echo_graph_calls_server_time_tool():
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "现在时间"}]})

    assert "工具返回" in result["messages"][-1].content
