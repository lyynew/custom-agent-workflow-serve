"""Business-facing routes mounted inside Aegra's FastAPI app.

`enable_custom_route_auth` puts `require_auth` in front of every route here.
The routes then call Aegra over loopback with the original Authorization and
X-App-Id headers, so there is exactly one authentication implementation.

Note: do NOT add `from __future__ import annotations` here. Aegra loads this
module dynamically, and Pydantic cannot resolve string annotations such as
``dict[str, Any]`` in that synthetic module namespace.
"""

import json
import os
from typing import Any
from uuid import uuid5

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from aegra_api.constants import ASSISTANT_NAMESPACE_UUID
from aegra_api.core.auth_deps import require_auth
from aegra_api.models.auth import User

SELF_URL = os.environ.get("SELF_URL", "http://127.0.0.1:2026")

app = FastAPI(title="AI Service Spike", version="0.0.1")


class ChatIn(BaseModel):
    message: str = Field(min_length=1)
    thread_id: str | None = None


class WorkflowIn(BaseModel):
    input: dict[str, Any]


def _forward(request: Request) -> dict[str, str]:
    allowed = {"authorization", "x-app-id"}
    return {key: value for key, value in request.headers.items() if key.lower() in allowed}


def _assistant_id(graph_id: str) -> str:
    """Aegra seeds a deterministic default assistant per graph."""
    return str(uuid5(ASSISTANT_NAMESPACE_UUID, graph_id))


def _ensure_allowed(allowed: list[str], target: str) -> None:
    # Confirmed semantics: ["*"] = all, [] = none.
    if "*" in allowed:
        return
    if target not in allowed:
        raise HTTPException(status_code=403, detail=f"Not allowed: {target}")


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _json_response(response: httpx.Response) -> JSONResponse:
    try:
        content = response.json()
    except ValueError:
        content = {"detail": response.text}
    return JSONResponse(status_code=response.status_code, content=content)


@app.get("/api/v1/whoami")
async def whoami(user: User = Depends(require_auth)) -> dict[str, Any]:
    return {
        "identity": user.identity,
        "app_id": getattr(user, "app_id", None),
        "user_id": getattr(user, "user_id", None),
        "allowed_agents": getattr(user, "allowed_agents", []),
        "allowed_workflows": getattr(user, "allowed_workflows", []),
    }


@app.get("/api/v1/threads")
async def list_threads(request: Request, _user: User = Depends(require_auth)) -> JSONResponse:
    async with httpx.AsyncClient(base_url=SELF_URL, timeout=30) as client:
        response = await client.post("/threads/search", json={"limit": 100}, headers=_forward(request))
    return _json_response(response)


@app.get("/api/v1/threads/{thread_id}")
async def get_thread(thread_id: str, request: Request, _user: User = Depends(require_auth)) -> JSONResponse:
    async with httpx.AsyncClient(base_url=SELF_URL, timeout=30) as client:
        response = await client.get(f"/threads/{thread_id}", headers=_forward(request))
    return _json_response(response)


@app.post("/api/v1/agents/{agent_id}/chat/stream")
async def chat_stream(
    agent_id: str,
    payload: ChatIn,
    request: Request,
    user: User = Depends(require_auth),
) -> StreamingResponse:
    _ensure_allowed(list(getattr(user, "allowed_agents", [])), agent_id)
    headers = {**_forward(request), "accept-encoding": "identity"}

    thread_id = payload.thread_id
    if not thread_id:
        async with httpx.AsyncClient(base_url=SELF_URL, timeout=60) as client:
            created = await client.post(
                "/threads",
                json={"metadata": {"graph_id": agent_id}},
                headers=headers,
            )
        if created.status_code >= 400:
            raise HTTPException(status_code=created.status_code, detail=created.text)
        thread_id = created.json()["thread_id"]

    body = {
        "assistant_id": _assistant_id(agent_id),
        "input": {"messages": [{"role": "user", "content": payload.message}]},
        "stream_mode": ["messages"],
    }

    async def events():
        meta = {"thread_id": thread_id, "identity": user.identity, "app_id": getattr(user, "app_id", None)}
        yield _sse("meta", meta)

        last_by_id: dict[str, str] = {}
        failed = False

        def adapt(event_name: str | None, payload: str) -> list[tuple[str, Any]]:
            """Translate Aegra's cumulative message snapshots into token deltas."""
            if event_name != "messages/partial":
                return []
            try:
                items = json.loads(payload)
            except json.JSONDecodeError:
                return []
            if not isinstance(items, list):
                return []
            out: list[tuple[str, Any]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, str):
                    continue
                message_id = str(item.get("id") or "default")
                previous = last_by_id.get(message_id, "")
                delta = content[len(previous):] if content.startswith(previous) else content
                last_by_id[message_id] = content
                if delta:
                    out.append(("token", {"delta": delta}))
            return out

        async with httpx.AsyncClient(base_url=SELF_URL, timeout=None) as client:
            async with client.stream(
                "POST",
                f"/threads/{thread_id}/runs/stream",
                json=body,
                headers=headers,
            ) as response:
                if response.status_code >= 400:
                    detail = (await response.aread()).decode("utf-8", "replace")
                    yield _sse("error", {"status": response.status_code, "detail": detail})
                    return

                event_name: str | None = None
                data_lines: list[str] = []

                async for line in response.aiter_lines():
                    if line == "":
                        if data_lines:
                            payload = "\n".join(data_lines)
                            if event_name in {"error", "run/error"}:
                                failed = True
                                yield _sse("error", {"detail": payload})
                            else:
                                for name, data in adapt(event_name, payload):
                                    yield _sse(name, data)
                        event_name, data_lines = None, []
                        continue
                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())

        if not failed:
            yield _sse("done", {"finish_reason": "stop"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/v1/workflows/{workflow_id}/execute")
async def execute_workflow(
    workflow_id: str,
    payload: WorkflowIn,
    request: Request,
    user: User = Depends(require_auth),
) -> JSONResponse:
    _ensure_allowed(list(getattr(user, "allowed_workflows", [])), workflow_id)
    body = {"assistant_id": _assistant_id(workflow_id), "input": payload.input}
    async with httpx.AsyncClient(base_url=SELF_URL, timeout=120) as client:
        response = await client.post("/runs/wait", json=body, headers=_forward(request))
    return _json_response(response)
