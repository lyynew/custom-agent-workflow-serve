from app.workflows.base import WorkflowDefinition
from app.workflows.implementations.sum_workflow import SumInput
from app.workflows.implementations.text_stats_workflow import TextStatsInput
from app.workflows.registry import workflow_registry

workflow_registry.register(
    WorkflowDefinition(
        workflow_id="sum_workflow",
        name="Sum Workflow",
        description="Adds two integers. Used as the minimal end-to-end example.",
        tags=("demo",),
        input_model=SumInput,
    )
)

workflow_registry.register(
    WorkflowDefinition(
        workflow_id="text_stats_workflow",
        name="Text Stats Workflow",
        description="Counts characters, words and keywords, with a branch for long text.",
        tags=("demo", "text"),
        input_model=TextStatsInput,
    )
)

__all__ = ["WorkflowDefinition", "workflow_registry"]
