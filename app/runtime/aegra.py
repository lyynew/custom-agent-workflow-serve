"""Aegra HTTP implementation of the runtime adapter."""

from collections.abc import Mapping
from typing import Any
from uuid import uuid5

import httpx
from aegra_api.constants import ASSISTANT_NAMESPACE_UUID

from app.runtime.adapter import RuntimeCredentials


class RuntimeCallError(RuntimeError):
    """The runtime rejected a call. Kept internal; API routes map it to a 5xx."""

    def __init__(self, status_code: int, operation: str) -> None:
        self.status_code = status_code
        self.operation = operation
        super().__init__(f"Runtime call failed: {operation} (HTTP {status_code})")


def assistant_id_for(graph_id: str) -> str:
    """Aegra seeds one deterministic default assistant per graph."""
    return str(uuid5(ASSISTANT_NAMESPACE_UUID, graph_id))


class AegraRuntimeAdapter:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def _client(self, timeout_seconds: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout_seconds or self._timeout_seconds,
            transport=self._transport,
        )

    async def create_thread(
        self,
        credentials: RuntimeCredentials,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        async with self._client() as client:
            response = await client.post(
                "/threads",
                json={"metadata": dict(metadata or {})},
                headers=credentials.headers(),
            )
        if response.status_code >= 400:
            raise RuntimeCallError(response.status_code, "create_thread")
        return str(response.json()["thread_id"])

    async def get_thread(
        self,
        credentials: RuntimeCredentials,
        thread_id: str,
    ) -> dict[str, Any] | None:
        async with self._client() as client:
            response = await client.get(f"/threads/{thread_id}", headers=credentials.headers())

        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise RuntimeCallError(response.status_code, "get_thread")
        return dict(response.json())

    async def delete_thread(
        self,
        credentials: RuntimeCredentials,
        thread_id: str,
    ) -> bool:
        async with self._client() as client:
            response = await client.delete(f"/threads/{thread_id}", headers=credentials.headers())

        if response.status_code == 404:
            return False
        if response.status_code >= 400:
            raise RuntimeCallError(response.status_code, "delete_thread")
        return True

    async def run_wait(
        self,
        credentials: RuntimeCredentials,
        *,
        graph_id: str,
        input: Mapping[str, Any],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        body = {"assistant_id": assistant_id_for(graph_id), "input": dict(input)}
        async with self._client(timeout_seconds) as client:
            response = await client.post("/runs/wait", json=body, headers=credentials.headers())

        if response.status_code >= 400:
            raise RuntimeCallError(response.status_code, "run_wait")
        return dict(response.json())
