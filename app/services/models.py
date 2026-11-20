"""Resolve `biz.model_configs` rows into LangChain chat models.

The graph never decides which model it talks to: it asks for a configuration by
name. Swapping provider, base URL or temperature is a row update, not a code
change.

API keys are never stored. `api_key_encrypted` holds an `env:VARIABLE` reference,
resolved at call time, exactly like `applications.jwt_config.secret_ref`.
"""

import logging
from dataclasses import dataclass
from time import monotonic
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import select

from app.auth.errors import SecretResolutionError
from app.auth.secrets import resolve_secret
from app.config import get_settings
from app.database.session import get_session_factory
from app.models.model_config import ModelConfig

logger = logging.getLogger(__name__)

SUPPORTED_PROVIDERS = ("openai", "echo")


class ModelConfigError(RuntimeError):
    """The requested model configuration is missing, disabled or unsupported."""


class EchoChatModel(BaseChatModel):
    """Deterministic stand-in for an external provider.

    Backs the `echo` provider so the whole pipeline (config row -> resolution ->
    graph call) runs and is testable without network access. It is also the
    smoke-test path in `docker-compose`.
    """

    prefix: str = "model"

    @property
    def _llm_type(self) -> str:
        return "echo"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        prompt = ""
        for message in reversed(messages):
            if getattr(message, "type", "") == "human":
                prompt = message.content if isinstance(message.content, str) else str(message.content)
                break

        text = f"{self.prefix}: {prompt}" if prompt else f"{self.prefix}: (no input)"
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])


@dataclass(frozen=True)
class ResolvedModel:
    name: str
    provider: str
    model: BaseChatModel


class ModelResolver:
    """Caches built models per configuration name.

    Building a client is cheap, but the database read on every LLM call is not,
    so the resolved object is cached with a short TTL. `clear()` forces a reload
    after a configuration change.
    """

    def __init__(self, ttl_seconds: int | None = None) -> None:
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, ResolvedModel]] = {}

    @property
    def ttl(self) -> int:
        if self._ttl is not None:
            return self._ttl
        return get_settings().model_config_cache_ttl_seconds

    async def resolve(self, name: str) -> BaseChatModel:
        return (await self.resolve_full(name)).model

    async def resolve_full(self, name: str) -> ResolvedModel:
        now = monotonic()
        cached = self._cache.get(name)
        if cached is not None and cached[0] > now:
            return cached[1]

        async with get_session_factory()() as session:
            row = await session.scalar(select(ModelConfig).where(ModelConfig.name == name))

        if row is None:
            raise ModelConfigError(f"Model configuration '{name}' is not registered")
        if not row.enabled:
            raise ModelConfigError(f"Model configuration '{name}' is disabled")

        resolved = ResolvedModel(name=row.name, provider=row.provider, model=self._build(row))
        self._cache[name] = (now + self.ttl, resolved)
        return resolved

    def _build(self, row: ModelConfig) -> BaseChatModel:
        if row.provider == "echo":
            prefix = str((row.extra or {}).get("prefix") or "model")
            return EchoChatModel(prefix=prefix)

        if row.provider == "openai":
            return self._build_openai(row)

        raise ModelConfigError(
            f"Model configuration '{row.name}' uses unsupported provider '{row.provider}'. "
            f"Supported: {', '.join(SUPPORTED_PROVIDERS)}"
        )

    def _build_openai(self, row: ModelConfig) -> BaseChatModel:
        # Imported lazily so the service starts without the provider SDK installed.
        from langchain_openai import ChatOpenAI

        api_key: str | None = None
        if row.api_key_encrypted:
            try:
                api_key = resolve_secret(row.api_key_encrypted)
            except SecretResolutionError as exc:
                raise ModelConfigError(
                    f"Model configuration '{row.name}' has an unresolvable key reference: {exc}"
                ) from exc

        if not api_key:
            raise ModelConfigError(f"Model configuration '{row.name}' has no API key reference")

        kwargs: dict[str, Any] = {
            "model": row.model_name,
            "temperature": row.temperature,
            "timeout": row.timeout_seconds,
        }
        if row.base_url:
            kwargs["base_url"] = row.base_url
        if row.max_tokens:
            kwargs["max_tokens"] = row.max_tokens

        return ChatOpenAI(api_key=api_key, **kwargs)

    def clear(self) -> None:
        self._cache.clear()


model_resolver = ModelResolver()
