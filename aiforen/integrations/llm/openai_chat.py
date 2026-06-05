"""OpenAI chat helpers — token-limit compatibility across model generations."""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional


def _token_limit_attempts(max_output_tokens: int) -> List[Dict[str, int]]:
    return [
        {"max_completion_tokens": max_output_tokens},
        {"max_tokens": max_output_tokens},
    ]


def _should_retry_token_param(exc: Exception) -> bool:
    err = str(exc).lower()
    return "unsupported parameter" in err and (
        "max_tokens" in err or "max_completion_tokens" in err
    )


async def openai_chat_completion_text(
    client: Any,
    *,
    model: str,
    messages: List[Dict[str, str]],
    max_output_tokens: int = 900,
    temperature: Optional[float] = 0.3,
    response_format: Optional[Dict[str, str]] = None,
) -> str:
    """Return assistant text; retries alternate token limit param names."""
    kwargs: Dict[str, Any] = {"model": model, "messages": messages}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if response_format:
        kwargs["response_format"] = response_format

    last_exc: Optional[Exception] = None
    for token_kwargs in _token_limit_attempts(max_output_tokens):
        try:
            resp = await client.chat.completions.create(**kwargs, **token_kwargs)
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if _should_retry_token_param(exc):
                continue
            raise

    if last_exc is not None:
        raise last_exc
    return ""


async def openai_chat_completion_stream(
    client: Any,
    *,
    model: str,
    messages: List[Dict[str, str]],
    max_output_tokens: int = 700,
    temperature: Optional[float] = 0.35,
) -> AsyncIterator[str]:
    """Stream assistant deltas; retries alternate token limit param names."""
    kwargs: Dict[str, Any] = {"model": model, "messages": messages, "stream": True}
    if temperature is not None:
        kwargs["temperature"] = temperature

    last_exc: Optional[Exception] = None
    for token_kwargs in _token_limit_attempts(max_output_tokens):
        try:
            stream = await client.chat.completions.create(**kwargs, **token_kwargs)
            async for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    yield delta
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if _should_retry_token_param(exc):
                continue
            raise

    if last_exc is not None:
        raise last_exc
