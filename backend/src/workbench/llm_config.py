"""Explicit provider configuration. Local mode never falls back to cloud credentials."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    pass


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise ConfigurationError(f"{name} 必須是整數。") from None
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} 必須介於 {minimum}–{maximum}。")
    return value


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    timeout_seconds: int = 20
    max_tokens: int = 4096

    @property
    def available(self) -> bool:
        return bool(self.model and (self.provider == "lmstudio" or self.api_key))

    @classmethod
    def from_env(cls) -> LLMConfig:
        provider = os.getenv("WORKBENCH_LLM_PROVIDER", "openai").strip().lower()
        if provider not in {"openai", "lmstudio"}:
            raise ConfigurationError("WORKBENCH_LLM_PROVIDER 必須是 openai 或 lmstudio。")
        if provider == "lmstudio":
            base_url = os.getenv("WORKBENCH_LLM_BASE_URL", "http://127.0.0.1:1234/v1").strip().rstrip("/")
            try:
                url = urlsplit(base_url)
                port = url.port
                if (url.scheme not in {"http", "https"} or not url.hostname or
                        url.username is not None or url.password is not None or url.query or url.fragment or
                        url.path != "/v1" or any(c.isspace() for c in base_url) or port == 0):
                    raise ValueError()
            except ValueError:
                raise ConfigurationError("WORKBENCH_LLM_BASE_URL 需為 http(s)://主機[:port]/v1，不能包含帳密或查詢參數。") from None
            key = os.getenv("WORKBENCH_LLM_API_KEY", "").strip()
        else:
            # OpenAI's key is never sent to a configurable local endpoint.
            base_url = "https://api.openai.com/v1"
            key = os.getenv("OPENAI_API_KEY", "").strip()
        return cls(provider=provider, model=os.getenv("WORKBENCH_LLM_MODEL", "").strip(),
                   base_url=base_url, api_key=key,
                   timeout_seconds=_integer("WORKBENCH_LLM_TIMEOUT_SECONDS", 90 if provider == "lmstudio" else 20, 5, 300),
                   max_tokens=_integer("WORKBENCH_LLM_MAX_TOKENS", 4096, 256, 16384))


def configured_llm() -> LLMConfig | None:
    """Invalid optional settings disable enrichment, not the local parser."""
    try:
        return LLMConfig.from_env()
    except ConfigurationError:
        return None
