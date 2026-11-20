"""The `create_agent` variant: same model config, one function call."""

from app.agents.implementations.simple_agent import build
from app.repositories.model_configs import ModelConfigRepository
from app.services.models import model_resolver


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


async def test_build_returns_a_runnable_graph(seeded_apps, session):
    await _seed_demo_model(session)

    graph = await build()
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "你好"}]})

    assert result["messages"][-1].content == "model: 你好"
    assert graph.name == "simple_agent"


async def test_demo_provider_gets_no_tool_node(seeded_apps, session):
    """The `echo` model cannot bind tools, so the graph is a plain model call."""
    await _seed_demo_model(session)

    graph = await build()

    assert "tools" not in graph.get_graph().nodes


async def test_one_agent_can_use_a_different_model_config(seeded_apps, session, monkeypatch):
    """`MODEL_CONFIG_<AGENT_ID>` overrides the global default for one agent."""
    await _seed_demo_model(session)
    await ModelConfigRepository(session).upsert(
        {
            "name": "alt",
            "provider": "echo",
            "model_name": "echo-2",
            "temperature": 0.0,
            "timeout_seconds": 30,
            "extra": {"prefix": "alt"},
            "enabled": True,
        }
    )
    await session.commit()
    model_resolver.clear()

    monkeypatch.setenv("MODEL_CONFIG_SIMPLE_AGENT", "alt")
    graph = await build()
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "你好"}]})

    assert result["messages"][-1].content == "alt: 你好"
