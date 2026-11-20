"""Code-first workflow registry.

Same contract as the agent registry: register at import time, then
`validate_config()` fails startup if a registered workflow has no matching
graph in `aegra.json`.
"""

import json
from pathlib import Path

from app.workflows.base import WorkflowDefinition


class WorkflowNotFound(Exception):
    def __init__(self, workflow_id: str) -> None:
        self.workflow_id = workflow_id
        super().__init__(f"Workflow not found: {workflow_id}")


class WorkflowRegistry:
    def __init__(self) -> None:
        self._workflows: dict[str, WorkflowDefinition] = {}

    def register(self, workflow: WorkflowDefinition) -> None:
        if not workflow.workflow_id or not workflow.workflow_id.strip():
            raise ValueError("workflow_id must not be empty")
        if workflow.workflow_id in self._workflows:
            raise ValueError(f"Duplicate workflow_id: {workflow.workflow_id}")
        self._workflows[workflow.workflow_id] = workflow

    def get(self, workflow_id: str) -> WorkflowDefinition:
        try:
            return self._workflows[workflow_id]
        except KeyError as exc:
            raise WorkflowNotFound(workflow_id) from exc

    def list(self) -> tuple[WorkflowDefinition, ...]:
        return tuple(self._workflows[key] for key in sorted(self._workflows))

    def validate_config(self, config_path: str = "aegra.json") -> None:
        path = Path(config_path)
        if not path.is_file():
            raise RuntimeError(f"Workflow config not found: {config_path}")

        config = json.loads(path.read_text(encoding="utf-8"))
        graphs = config.get("graphs")
        if not isinstance(graphs, dict):
            raise RuntimeError("aegra.json must contain a 'graphs' object")

        missing = [item.workflow_id for item in self._workflows.values() if item.workflow_id not in graphs]
        if missing:
            raise RuntimeError(
                "Workflows registered without a graph in aegra.json: " + ", ".join(sorted(missing))
            )


workflow_registry = WorkflowRegistry()
