"""The workflow graphs themselves, executed directly.

These tests do not touch HTTP or PostgreSQL: they prove the graph logic that the
API layer delegates to the runtime.
"""

from app.workflows.implementations.sum_workflow import graph as sum_graph
from app.workflows.implementations.text_stats_workflow import graph as stats_graph


async def test_sum_workflow_adds_operands():
    result = await sum_graph.ainvoke({"a": 2, "b": 3})

    assert result["total"] == 5


async def test_text_stats_takes_the_brief_branch_for_short_text():
    result = await stats_graph.ainvoke({"text": "one two three", "top_n": 3})

    assert result["mode"] == "brief"
    assert result["words"] == 3
    assert result["unique_words"] == 3
    assert result["top_words"] == ["one"]


async def test_text_stats_takes_the_detailed_branch_for_long_text():
    text = " ".join(f"word{index % 5}" for index in range(25))

    result = await stats_graph.ainvoke({"text": text, "top_n": 2})

    assert result["mode"] == "detailed"
    assert result["words"] == 25
    assert len(result["top_words"]) == 2
    assert set(result["top_words"]) <= {f"word{index}" for index in range(5)}
