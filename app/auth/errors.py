"""Authentication domain errors.

These are translated to HTTP responses at the Aegra boundary, and to API errors
inside business routes.
"""


class AuthError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 401) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class SecretResolutionError(RuntimeError):
    """A `secret_ref` cannot be resolved. This is a configuration error, not a client error."""
