"""Conversation CRUD and cross-application isolation over HTTP."""

from aegra_api.models.auth import User


def _user(
    app_id: str = "agriculture_app",
    user_id: str = "10001",
    allowed_agents: list[str] | None = None,
) -> User:
    return User(
        identity=f"{app_id}:{user_id}",
        app_id=app_id,
        user_id=user_id,
        allowed_agents=["*"] if allowed_agents is None else allowed_agents,
        allowed_workflows=["*"],
    )


async def test_create_list_get_delete_conversation(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user())

    created = await asgi_client.post(
        "/api/v1/conversations",
        json={"agent_id": "echo_agent", "title": "First"},
    )
    assert created.status_code == 201
    conversation = created.json()
    assert conversation["thread_id"] == "thread-1"
    assert conversation["title"] == "First"
    assert fake_runtime.created_metadata[0]["agent_id"] == "echo_agent"

    listed = await asgi_client.get("/api/v1/conversations")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [conversation["id"]]

    fetched = await asgi_client.get(f"/api/v1/conversations/{conversation['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == conversation["id"]

    messages = await asgi_client.get(f"/api/v1/conversations/{conversation['id']}/messages")
    assert messages.status_code == 200
    assert messages.json()["items"] == []

    deleted = await asgi_client.delete(f"/api/v1/conversations/{conversation['id']}")
    assert deleted.status_code == 204
    assert fake_runtime.deleted_threads == ["thread-1"]

    gone = await asgi_client.get(f"/api/v1/conversations/{conversation['id']}")
    assert gone.status_code == 404


async def test_same_user_id_in_another_app_cannot_read_conversation(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user("agriculture_app"))
    created = await asgi_client.post(
        "/api/v1/conversations",
        json={"agent_id": "echo_agent"},
    )
    conversation_id = created.json()["id"]

    authenticated_as(_user("management_app"))
    fetched = await asgi_client.get(f"/api/v1/conversations/{conversation_id}")

    assert fetched.status_code == 404
    assert fetched.json()["error"]["code"] == "not_found"


async def test_agent_must_be_allowed(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user(allowed_agents=[]))

    response = await asgi_client.post(
        "/api/v1/conversations",
        json={"agent_id": "echo_agent"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_unknown_agent_is_rejected(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user())

    response = await asgi_client.post(
        "/api/v1/conversations",
        json={"agent_id": "does_not_exist"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "agent_not_found"


async def test_runtime_failure_on_create_returns_502(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user())
    fake_runtime.fail_create = True

    response = await asgi_client.post(
        "/api/v1/conversations",
        json={"agent_id": "echo_agent"},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "runtime_error"
