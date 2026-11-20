"""Registry invariants and the input contract of each workflow."""

import pytest
from pydantic import ValidationError

from app.workflows import workflow_registry
from app.workflows.base import WorkflowDefinition
from app.workflows.registry import WorkflowNotFound, WorkflowRegistry


def test_registry_lists_workflows_in_stable_order():
    ids = [workflow.workflow_id for workflow in workflow_registry.list()]

    assert ids == sorted(ids)
    assert {"sum_workflow", "text_stats_workflow"} <= set(ids)


def test_unknown_workflow_raises():
    with pytest.raises(WorkflowNotFound):
        workflow_registry.get("not_a_workflow")


def test_duplicate_registration_is_rejected():
    registry = WorkflowRegistry()
    registry.register(WorkflowDefinition(workflow_id="duplicate", name="Duplicate"))

    with pytest.raises(ValueError):
        registry.register(WorkflowDefinition(workflow_id="duplicate", name="Duplicate again"))


def test_validate_config_checks_aegra_json():
    workflow_registry.validate_config()


def test_sum_input_requires_both_operands():
    definition = workflow_registry.get("sum_workflow")

    with pytest.raises(ValidationError) as excinfo:
        definition.validate_input({"a": 1})

    assert "b" in str(excinfo.value)


def test_sum_input_rejects_unknown_fields():
    definition = workflow_registry.get("sum_workflow")

    with pytest.raises(ValidationError):
        definition.validate_input({"a": 1, "b": 2, "c": 3})


def test_text_stats_applies_defaults():
    definition = workflow_registry.get("text_stats_workflow")

    assert definition.validate_input({"text": "hello"}) == {"text": "hello", "top_n": 3}


def test_text_stats_rejects_empty_text():
    definition = workflow_registry.get("text_stats_workflow")

    with pytest.raises(ValidationError):
        definition.validate_input({"text": ""})


def test_input_schema_is_exported_for_clients():
    schema = workflow_registry.get("sum_workflow").input_schema()

    assert schema["required"] == ["a", "b"]
    assert set(schema["properties"]) == {"a", "b"}
