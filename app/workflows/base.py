"""Workflow metadata registered by code.

The compiled graph is loaded by Aegra from `aegra.json`; this definition owns
the business-facing identity, the input contract and the listing metadata.
Keeping the input model here means a malformed request is rejected by the API
before a runtime thread or run record is created.
"""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_id: str
    name: str
    description: str = ""
    tags: tuple[str, ...] = ()
    input_model: type[BaseModel] | None = None

    def validate_input(self, payload: Any) -> dict[str, Any]:
        """Return a normalised payload, or raise ValidationError."""
        if self.input_model is None:
            if payload is None:
                return {}
            if not isinstance(payload, dict):
                raise TypeError("Workflow input must be a JSON object")
            return dict(payload)

        validated = self.input_model.model_validate(payload if payload is not None else {})
        return validated.model_dump(exclude_none=True)

    def input_schema(self) -> dict[str, Any]:
        if self.input_model is None:
            return {"type": "object"}
        return self.input_model.model_json_schema()
