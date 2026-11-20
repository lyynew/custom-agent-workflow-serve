"""Two-branch text statistics workflow.

`analyze` always runs; a conditional edge then picks `detailed` for long input
and `brief` for short input. It is the smallest graph that still exercises a
conditional edge, which is what real workflows rely on.
"""

import re
from collections import Counter
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

LONG_TEXT_WORDS = 20


class TextStatsInput(BaseModel):
    # Reject unknown keys: a misspelled parameter must not be silently ignored.
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=20_000, description="Text to analyse")
    top_n: int = Field(default=3, ge=1, le=10, description="Keywords kept when the text is long")


class TextStatsState(TypedDict, total=False):
    text: str
    top_n: int
    chars: int
    words: int
    unique_words: int
    top_words: list[str]
    mode: str


def _tokens(text: str) -> list[str]:
    return [token for token in re.split(r"\s+", text.strip()) if token]


def analyze(state: TextStatsState) -> TextStatsState:
    text = state.get("text", "")
    tokens = _tokens(text)
    counts = Counter(token.lower() for token in tokens)
    return {
        "chars": len(text),
        "words": len(tokens),
        "unique_words": len(counts),
        "top_words": [word for word, _ in counts.most_common()],
    }


def route(state: TextStatsState) -> str:
    return "detailed" if state.get("words", 0) >= LONG_TEXT_WORDS else "brief"


def detailed(state: TextStatsState) -> TextStatsState:
    top_n = max(1, int(state.get("top_n", 3)))
    return {"top_words": list(state.get("top_words", []))[:top_n], "mode": "detailed"}


def brief(state: TextStatsState) -> TextStatsState:
    return {"top_words": list(state.get("top_words", []))[:1], "mode": "brief"}


builder = StateGraph(TextStatsState)
builder.add_node("analyze", analyze)
builder.add_node("detailed", detailed)
builder.add_node("brief", brief)
builder.add_edge(START, "analyze")
builder.add_conditional_edges("analyze", route, {"detailed": "detailed", "brief": "brief"})
builder.add_edge("detailed", END)
builder.add_edge("brief", END)

graph = builder.compile(name="text_stats_workflow")
