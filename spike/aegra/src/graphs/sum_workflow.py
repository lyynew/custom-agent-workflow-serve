"""Minimal workflow used to validate sync execution and result persistence."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class SumState(TypedDict):
    a: int
    b: int
    total: int


def add(state: SumState) -> dict:
    return {"total": state["a"] + state["b"]}


builder = StateGraph(SumState)
builder.add_node("add", add)
builder.add_edge(START, "add")
builder.add_edge("add", END)

graph = builder.compile(name="sum_workflow")
