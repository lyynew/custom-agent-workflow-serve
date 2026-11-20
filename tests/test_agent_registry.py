"""Registry behavior and config validation."""

import json

import pytest

from app.agents import agent_registry
from app.agents.base import AgentDefinition
from app.agents.registry import AgentNotFound, AgentRegistry


def test_echo_agent_is_registered():
    agent = agent_registry.get("echo_agent")
    assert agent.name == "Echo Agent"
    assert "demo" in agent.tags


def test_duplicate_agent_id_is_rejected():
    registry = AgentRegistry()
    registry.register(AgentDefinition(agent_id="a", name="A"))

    with pytest.raises(ValueError, match="Duplicate"):
        registry.register(AgentDefinition(agent_id="a", name="A again"))


def test_unknown_agent_raises():
    with pytest.raises(AgentNotFound):
        AgentRegistry().get("missing")


def test_validate_config_requires_matching_graph(tmp_path):
    registry = AgentRegistry()
    registry.register(AgentDefinition(agent_id="missing", name="Missing"))

    config = tmp_path / "aegra.json"
    config.write_text(json.dumps({"graphs": {}}), encoding="utf-8")

    with pytest.raises(RuntimeError, match="missing"):
        registry.validate_config(str(config))


def test_repository_config_contains_every_registered_agent():
    agent_registry.validate_config()
