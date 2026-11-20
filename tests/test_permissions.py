"""Allow-list semantics: ["*"] is everything, [] is nothing."""

import pytest

from app.auth.errors import AuthError
from app.auth.permissions import ensure_allowed, is_allowed


def test_wildcard_allows_everything():
    assert is_allowed(["*"], "echo_agent") is True


def test_explicit_membership():
    assert is_allowed(["echo_agent", "report_agent"], "echo_agent") is True
    assert is_allowed(["report_agent"], "echo_agent") is False


def test_empty_list_denies_everything():
    assert is_allowed([], "echo_agent") is False

    with pytest.raises(AuthError) as exc:
        ensure_allowed([], "echo_agent")

    assert exc.value.status_code == 403
    assert exc.value.code == "forbidden"
