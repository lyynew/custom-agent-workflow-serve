"""Business API errors with one response envelope."""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from app.auth.errors import AuthError
from app.repositories.errors import ResourceNotFound


class APIError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def _envelope(request: Request, code: str, message: str) -> dict[str, Any]:
    request_id = request.headers.get("x-request-id") or getattr(request.state, "request_id", None)
    return {"error": {"code": code, "message": message, "request_id": request_id}}


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(request, exc.code, exc.message),
    )


async def auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(request, exc.code, exc.message),
    )


async def resource_not_found_handler(request: Request, exc: ResourceNotFound) -> JSONResponse:
    # Deliberately does not distinguish "missing" from "not yours".
    return JSONResponse(
        status_code=404,
        content=_envelope(request, "not_found", f"{exc.resource} not found"),
    )
