"""Agent / Workflow allow-list rules.

Confirmed semantics: ``["*"]`` allows everything, ``[]`` allows nothing.
"""

from collections.abc import Sequence

from app.auth.errors import AuthError


def is_allowed(allowed: Sequence[str], target: str) -> bool:
    return "*" in allowed or target in allowed


def ensure_allowed(allowed: Sequence[str], target: str) -> None:
    if not is_allowed(allowed, target):
        raise AuthError("forbidden", f"Not allowed: {target}", status_code=403)
