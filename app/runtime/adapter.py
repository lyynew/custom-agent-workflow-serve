"""Runtime boundary.

Business services depend on this protocol, never on Aegra types. Swapping the
runtime (for example back to a self-built LangGraph service) means implementing
this interface, not rewriting the business layer.
"""

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class RuntimeCredentials:
    """Per-request credentials forwarded to the runtime.

    The token is carried only across the runtime boundary; it is never stored,
    logged, or attached to a domain object.
    """

    app_id: str
    authorization: str

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": self.authorization,
            "X-App-Id": self.app_id,
        }


@dataclass(frozen=True)
class RunStarted:
    run_id: str


@dataclass(frozen=True)
class TokenDelta:
    text: str


@dataclass(frozen=True)
class ToolCallEvent:
    call_id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ToolResultEvent:
    call_id: str | None
    name: str
    content: str


@dataclass(frozen=True)
class RunFinished:
    status: str = "success"


@dataclass(frozen=True)
class RunFailed:
    message: str
    code: str = "runtime_error"


RuntimeEvent = RunStarted | TokenDelta | ToolCallEvent | ToolResultEvent | RunFinished | RunFailed


@dataclass(frozen=True)
class RunWaitResult:
    state: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None


class RuntimeAdapter(Protocol):
    async def create_thread(
        self,
        credentials: RuntimeCredentials,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> str: ...

    async def get_thread(
        self,
        credentials: RuntimeCredentials,
        thread_id: str,
    ) -> dict[str, Any] | None: ...

    async def delete_thread(
        self,
        credentials: RuntimeCredentials,
        thread_id: str,
    ) -> bool: ...

    async def run_wait(
        self,
        credentials: RuntimeCredentials,
        *,
        graph_id: str,
        input: Mapping[str, Any],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]: ...

    async def run_wait_on_thread(
        self,
        credentials: RuntimeCredentials,
        *,
        thread_id: str,
        graph_id: str,
        input: Mapping[str, Any],
        timeout_seconds: float | None = None,
    ) -> RunWaitResult: ...

    def stream_thread_run(
        self,
        credentials: RuntimeCredentials,
        *,
        thread_id: str,
        graph_id: str,
        input: Mapping[str, Any],
    ) -> AsyncIterator[RuntimeEvent]: ...
