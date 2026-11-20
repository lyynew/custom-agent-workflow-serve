"""Minimal deterministic workflow.

It exists to prove the whole workflow path (input validation, run record,
sync / async execution, result retrieval) without depending on a model API.
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field


class SumInput(BaseModel):
    # Reject unknown keys: a misspelled parameter must not be silently ignored.
    model_config = ConfigDict(extra="forbid")

    a: int = Field(description="First addend")
    b: int = Field(description="Second addend")


class SumState(TypedDict, total=False):
    a: int
    b: int
    total: int


def add_node(state: SumState) -> SumState:
    return {"total": int(state.get("a", 0)) + int(state.get("b", 0))}


builder = StateGraph(SumState)
builder.add_node("add", add_node)
builder.add_edge(START, "add")
builder.add_edge("add", END)

graph = builder.compile(name="sum_workflow")
