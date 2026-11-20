"""Code-first agent registry.

Registration happens at import time; `validate_config()` confirms every
registered agent has a matching graph in `aegra.json`, so a typo fails at
startup instead of at the first chat request.
"""

import json
from pathlib import Path

from app.agents.base import AgentDefinition


class AgentNotFound(Exception):
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        super().__init__(f"Agent not found: {agent_id}")


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, AgentDefinition] = {}

    def register(self, agent: AgentDefinition) -> None:
        if not agent.agent_id or not agent.agent_id.strip():
            raise ValueError("agent_id must not be empty")
        if agent.agent_id in self._agents:
            raise ValueError(f"Duplicate agent_id: {agent.agent_id}")
        self._agents[agent.agent_id] = agent

    def get(self, agent_id: str) -> AgentDefinition:
        try:
            return self._agents[agent_id]
        except KeyError as exc:
            raise AgentNotFound(agent_id) from exc

    def list(self) -> tuple[AgentDefinition, ...]:
        return tuple(self._agents[key] for key in sorted(self._agents))

    def validate_config(self, config_path: str = "aegra.json") -> None:
        path = Path(config_path)
        if not path.is_file():
            raise RuntimeError(f"Agent config not found: {config_path}")

        config = json.loads(path.read_text(encoding="utf-8"))
        graphs = config.get("graphs")
        if not isinstance(graphs, dict):
            raise RuntimeError("aegra.json must contain a 'graphs' object")

        missing = [agent.agent_id for agent in self._agents.values() if agent.agent_id not in graphs]
        if missing:
            raise RuntimeError(
                "Agents registered without a graph in aegra.json: " + ", ".join(sorted(missing))
            )


agent_registry = AgentRegistry()
