"""Workflow API: execution modes, run lifecycle, results and isolation."""

from aegra_api.models.auth import User

import app.services.workflows as workflow_service
from app.config import get_settings


def _user(
    *,
    allowed_workflows: list[str] | None = None,
    app_id: str = "agriculture_app",
    user_id: str = "10001",
) -> User:
    return User(
        identity=f"{app_id}:{user_id}",
        app_id=app_id,
        user_id=user_id,
        allowed_agents=["*"],
        allowed_workflows=["*"] if allowed_workflows is None else allowed_workflows,
    )


def _tuned_settings(**overrides):
    """A Settings copy with execution timings shrunk so tests stay fast."""
    return get_settings().model_copy(update=overrides)


async def _execute(client, workflow_id: str = "sum_workflow", **body):
    payload = {"input": {"a": 2, "b": 3}, "mode": "async", **body}
    return await client.post(f"/api/v1/workflows/{workflow_id}/execute", json=payload)


async def test_wildcard_lists_registered_workflows(seeded_apps, authenticated_as, asgi_client):
    authenticated_as(_user())

    response = await asgi_client.get("/api/v1/workflows")

    assert response.status_code == 200
    ids = [workflow["workflow_id"] for workflow in response.json()]
    assert ids == ["sum_workflow", "text_stats_workflow"]
    assert response.json()[0]["input_schema"]["required"] == ["a", "b"]


async def test_empty_allow_list_hides_every_workflow(seeded_apps, authenticated_as, asgi_client):
    authenticated_as(_user(allowed_workflows=[]))

    response = await asgi_client.get("/api/v1/workflows")

    assert response.status_code == 200
    assert response.json() == []


async def test_async_execution_returns_202_and_a_pending_run(
    seeded_apps, authenticated_as, asgi_client, fake_runtime, workflow_launcher
):
    authenticated_as(_user())

    response = await _execute(asgi_client)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    assert body["workflow_id"] == "sum_workflow"
    assert body["output"] is None

    # The runtime received the validated payload, and the thread carries the
    # workflow id so the runtime side stays traceable.
    assert fake_runtime.started_runs[0]["input"] == {"a": 2, "b": 3}
    assert fake_runtime.created_metadata[-1]["workflow_id"] == "sum_workflow"

    await workflow_launcher.drain()

    detail = await asgi_client.get(f"/api/v1/workflows/runs/{body['run_id']}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "succeeded"
    assert detail.json()["output"] == {"total": 5}
    assert detail.json()["finished_at"] is not None


async def test_sync_execution_waits_and_returns_the_result(
    seeded_apps, authenticated_as, asgi_client, fake_runtime, workflow_launcher
):
    authenticated_as(_user())

    response = await _execute(asgi_client, mode="sync")

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["output"] == {"total": 5}
    assert workflow_launcher.pending == []


async def test_invalid_input_is_rejected_before_the_runtime_is_called(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user())

    response = await asgi_client.post(
        "/api/v1/workflows/sum_workflow/execute",
        json={"input": {"a": 1, "c": 3}, "mode": "async"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"
    assert fake_runtime.started_runs == []


async def test_unknown_workflow_returns_404(seeded_apps, authenticated_as, asgi_client, fake_runtime):
    authenticated_as(_user())

    response = await _execute(asgi_client, workflow_id="missing_workflow")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "workflow_not_found"


async def test_workflow_outside_the_allow_list_returns_403(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user(allowed_workflows=["text_stats_workflow"]))

    response = await _execute(asgi_client)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_run_is_invisible_to_another_application(
    seeded_apps, authenticated_as, asgi_client, fake_runtime, workflow_launcher
):
    authenticated_as(_user())
    created = (await _execute(asgi_client)).json()
    await workflow_launcher.drain()

    # Same user_id, different app_id.
    authenticated_as(_user(app_id="management_app"))

    detail = await asgi_client.get(f"/api/v1/workflows/runs/{created['run_id']}")
    result = await asgi_client.get(f"/api/v1/workflows/runs/{created['run_id']}/result")
    listing = await asgi_client.get("/api/v1/workflows/runs")

    assert detail.status_code == 404
    assert result.status_code == 404
    assert listing.json()["items"] == []


async def test_result_of_unfinished_run_conflicts(
    seeded_apps, authenticated_as, asgi_client, fake_runtime, workflow_launcher
):
    authenticated_as(_user())
    created = (await _execute(asgi_client)).json()

    response = await asgi_client.get(f"/api/v1/workflows/runs/{created['run_id']}/result")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "run_not_finished"


async def test_failed_run_reports_the_runtime_error(
    seeded_apps, authenticated_as, asgi_client, fake_runtime, workflow_launcher, monkeypatch
):
    fake_runtime.workflow_statuses = ["failed"]
    fake_runtime.workflow_error = "graph exploded"
    authenticated_as(_user())

    created = (await _execute(asgi_client)).json()
    await workflow_launcher.drain()

    detail = await asgi_client.get(f"/api/v1/workflows/runs/{created['run_id']}")
    result = await asgi_client.get(f"/api/v1/workflows/runs/{created['run_id']}/result")

    assert detail.json()["status"] == "failed"
    assert detail.json()["error"] == {"code": "workflow_failed", "message": "graph exploded"}
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "workflow_failed"


async def test_cancel_marks_the_run_and_is_idempotent(
    seeded_apps, authenticated_as, asgi_client, fake_runtime, workflow_launcher
):
    authenticated_as(_user())
    created = (await _execute(asgi_client)).json()

    first = await asgi_client.post(f"/api/v1/workflows/runs/{created['run_id']}/cancel")
    second = await asgi_client.post(f"/api/v1/workflows/runs/{created['run_id']}/cancel")

    assert first.status_code == 200
    assert first.json()["status"] == "cancelled"
    assert second.status_code == 200
    assert second.json()["status"] == "cancelled"
    assert fake_runtime.cancelled_runs == ["wf-run-1"]

    # A cancelled run must not be overwritten by the waiting poller.
    await workflow_launcher.drain()
    detail = await asgi_client.get(f"/api/v1/workflows/runs/{created['run_id']}")
    assert detail.json()["status"] == "cancelled"


async def test_cancel_after_completion_conflicts(
    seeded_apps, authenticated_as, asgi_client, fake_runtime, workflow_launcher
):
    authenticated_as(_user())
    created = (await _execute(asgi_client)).json()
    await workflow_launcher.drain()

    response = await asgi_client.post(f"/api/v1/workflows/runs/{created['run_id']}/cancel")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "run_not_cancellable"


async def test_sync_timeout_cancels_the_runtime_run(
    seeded_apps, authenticated_as, asgi_client, fake_runtime, monkeypatch
):
    monkeypatch.setattr(
        workflow_service,
        "get_settings",
        lambda: _tuned_settings(
            workflow_run_timeout_seconds=0.05,
            workflow_poll_interval_seconds=0.01,
        ),
    )
    fake_runtime.workflow_statuses = ["running"]
    authenticated_as(_user())

    response = await _execute(asgi_client, mode="sync")

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "workflow_timeout"
    assert fake_runtime.cancelled_runs == ["wf-run-1"]


async def test_async_timeout_is_recorded_on_the_run(
    seeded_apps, authenticated_as, asgi_client, fake_runtime, workflow_launcher, monkeypatch
):
    monkeypatch.setattr(
        workflow_service,
        "get_settings",
        lambda: _tuned_settings(
            workflow_run_timeout_seconds=0.05,
            workflow_poll_interval_seconds=0.01,
        ),
    )
    fake_runtime.workflow_statuses = ["running"]
    authenticated_as(_user())

    created = (await _execute(asgi_client)).json()
    await workflow_launcher.drain()

    detail = await asgi_client.get(f"/api/v1/workflows/runs/{created['run_id']}")
    assert detail.json()["status"] == "timed_out"
    assert detail.json()["error"]["code"] == "workflow_timeout"


async def test_concurrency_limit_returns_429(
    seeded_apps, authenticated_as, asgi_client, fake_runtime, workflow_launcher, monkeypatch
):
    monkeypatch.setattr(
        workflow_service,
        "get_settings",
        lambda: _tuned_settings(workflow_max_concurrent_runs=1),
    )
    authenticated_as(_user())

    first = await _execute(asgi_client)
    second = await _execute(asgi_client)

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "too_many_runs"

    await workflow_launcher.drain()


async def test_runtime_failure_returns_502(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    fake_runtime.fail_start_run = True
    authenticated_as(_user())

    response = await _execute(asgi_client)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "runtime_error"
    # The thread created for the failed run must not be left behind.
    assert fake_runtime.deleted_threads == ["thread-1"]


async def test_startup_reconciliation_interrupts_orphan_runs(seeded_apps, agri_context, session):
    """A run in flight when the process died must not stay `running` forever."""
    from app.main import _mark_interrupted_runs
    from app.repositories.workflow_runs import WorkflowRunRepository

    repository = WorkflowRunRepository(session, agri_context)
    run = await repository.create(
        workflow_id="sum_workflow",
        runtime_run_id="wf-run-orphan",
        thread_id="thread-orphan",
        input={"a": 1, "b": 2},
    )
    await session.commit()

    await _mark_interrupted_runs()
    await session.refresh(run)

    assert run.status == "interrupted"
    assert run.finished_at is not None
