"""Pick an LLM provider based on settings."""

from __future__ import annotations

from aiforen.core.config import get_settings

from .base import LLMProvider
from .mock import MockLLMProvider

_settings = get_settings()


def get_llm_provider() -> LLMProvider:
    if _settings.llm_provider == "anthropic":
        from .anthropic_provider import AnthropicLLMProvider

        return AnthropicLLMProvider()
    if _settings.llm_provider == "openai":
        from .openai_provider import OpenAILLMProvider

        return OpenAILLMProvider()
    if _settings.llm_provider == "mock":
        return MockLLMProvider()
    return MockLLMProvider()
