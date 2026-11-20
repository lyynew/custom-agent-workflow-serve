"""The only identity object business code should receive."""

from pydantic import BaseModel, ConfigDict, Field


class AuthContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    app_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    identity_provider: str = "jwt"

    @property
    def identity(self) -> str:
        """Namespaced identity used by the runtime and thread metadata."""
        return f"{self.app_id}:{self.user_id}"
