"""Model configuration: resolution, providers and the model-backed agent."""

import pytest
from langchain_core.messages import HumanMessage

from app.repositories.model_configs import ModelConfigRepository
from app.services.models import ModelConfigError, model_resolver


@pytest.fixture(autouse=True)
def clear_model_cache():
    model_resolver.clear()
    yield
    model_resolver.clear()


async def _seed_model(session, **overrides) -> None:
    values = {
        "name": "demo",
        "provider": "echo",
        "model_name": "echo-1",
        "temperature": 0.0,
        "timeout_seconds": 30,
        "extra": {"prefix": "model"},
        "enabled": True,
    }
    values.update(overrides)
    await ModelConfigRepository(session).upsert(values)
    await session.commit()


async def test_echo_provider_answers_deterministically(seeded_apps, session):
    await _seed_model(session)

    model = await model_resolver.resolve("demo")
    reply = await model.ainvoke([HumanMessage(content="hello")])

    assert reply.content == "model: hello"


async def test_prefix_comes_from_the_configuration_row(seeded_apps, session):
    await _seed_model(session, name="branded", extra={"prefix": "agri"})

    model = await model_resolver.resolve("branded")
    reply = await model.ainvoke([HumanMessage(content="hi")])

    assert reply.content == "agri: hi"


async def test_missing_configuration_is_an_error(seeded_apps):
    with pytest.raises(ModelConfigError, match="not registered"):
        await model_resolver.resolve("does_not_exist")


async def test_disabled_configuration_is_an_error(seeded_apps, session):
    await _seed_model(session, name="off", enabled=False)

    with pytest.raises(ModelConfigError, match="disabled"):
        await model_resolver.resolve("off")


async def test_unsupported_provider_is_an_error(seeded_apps, session):
    await _seed_model(session, name="weird", provider="carrier-pigeon")

    with pytest.raises(ModelConfigError, match="unsupported provider"):
        await model_resolver.resolve("weird")


async def test_openai_provider_requires_a_resolvable_key(seeded_apps, session, monkeypatch):
    monkeypatch.delenv("MISSING_MODEL_KEY", raising=False)
    await _seed_model(
        session,
        name="real",
        provider="openai",
        model_name="gpt-4o-mini",
        api_key_encrypted="env:MISSING_MODEL_KEY",
    )

    with pytest.raises(ModelConfigError, match="unresolvable key reference"):
        await model_resolver.resolve("real")


async def test_openai_provider_builds_a_client(seeded_apps, session, monkeypatch):
    monkeypatch.setenv("TEST_MODEL_KEY", "sk-test-not-a-real-key")
    await _seed_model(
        session,
        name="real",
        provider="openai",
        base_url="https://example.test/v1",
        model_name="gpt-4o-mini",
        api_key_encrypted="env:TEST_MODEL_KEY",
        max_tokens=256,
    )

    resolved = await model_resolver.resolve_full("real")

    assert resolved.provider == "openai"
    # Construction only: no request is made, so this stays offline.
    assert resolved.model.model_name == "gpt-4o-mini"
    assert resolved.model.openai_api_base == "https://example.test/v1"


async def test_resolver_caches_until_cleared(seeded_apps, session):
    await _seed_model(session)

    first = await model_resolver.resolve_full("demo")

    # A configuration change is invisible until the cache is cleared, which is
    # the documented behaviour of the short TTL.
    await ModelConfigRepository(session).upsert(
        {"name": "demo", "model_name": "echo-2", "extra": {"prefix": "fresh"}}
    )
    await session.commit()

    assert await model_resolver.resolve_full("demo") is first

    model_resolver.clear()
    refreshed = await model_resolver.resolve_full("demo")
    assert refreshed is not first
    assert (await refreshed.model.ainvoke([HumanMessage(content="x")])).content == "fresh: x"


async def test_llm_agent_uses_the_configured_model(seeded_apps, session):
    await _seed_model(session)

    from app.agents.implementations.llm_agent import graph

    result = await graph.ainvoke({"messages": [{"role": "user", "content": "你好"}]})

    assert result["messages"][-1].content == "model: 你好"


async def test_llm_agent_surfaces_configuration_errors(seeded_apps):
    from app.agents.implementations.llm_agent import graph

    # Nothing seeded: the agent must fail loudly rather than silently degrade.
    with pytest.raises(ModelConfigError):
        await graph.ainvoke({"messages": [{"role": "user", "content": "hi"}]})
