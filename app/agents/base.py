"""Agent metadata registered by code.

The compiled graph itself is loaded by Aegra from `aegra.json`; the registry
owns the business-facing identity, permissions and listing metadata.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentDefinition:
    agent_id: str
    name: str
    description: str = ""
    tags: tuple[str, ...] = ()
