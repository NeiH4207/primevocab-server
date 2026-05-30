"""Retry helpers for transient LLM API errors (rate limit, overload)."""

from __future__ import annotations

import asyncio
from typing import Any, Sequence

_RETRYABLE_STATUS = frozenset({429, 529, 503})
_RETRY_DELAYS_SEC = (0.8, 2.0, 5.0)
# Sonnet handles interactive vocab eval reliably when Haiku returns 529 overloaded.
_VOCAB_EVAL_FALLBACK_MODELS = (
    "claude-sonnet-4-20250514",
    "claude-haiku-4-5-20251001",
)


def vocab_eval_model_chain(primary: str | None) -> list[str]:
    """Deduplicated model list: primary first, then known fallbacks."""

    out: list[str] = []
    for model in (primary, *_VOCAB_EVAL_FALLBACK_MODELS):
        if model and model not in out:
            out.append(model)
    return out


async def anthropic_messages_with_retry(
    client: Any, *, model: str, **kwargs: Any
) -> Any:
    """Call ``client.messages.create`` with backoff on 429/529/503 for one model."""

    from anthropic import APIStatusError

    last: Exception | None = None
    for attempt, delay in enumerate(_RETRY_DELAYS_SEC):
        try:
            return await client.messages.create(model=model, **kwargs)
        except APIStatusError as exc:
            last = exc
            code = getattr(exc, "status_code", None)
            if code not in _RETRYABLE_STATUS or attempt >= len(_RETRY_DELAYS_SEC) - 1:
                raise
            await asyncio.sleep(delay)
    if last:
        raise last
    raise RuntimeError("anthropic_messages_with_retry failed without exception")


async def anthropic_messages_with_model_fallback(
    client: Any,
    *,
    models: Sequence[str],
    **kwargs: Any,
) -> tuple[Any, str]:
    """Try models in order; switch model immediately on 529/503 after per-model retries."""

    from anthropic import APIStatusError
    from loguru import logger

    chain = [m for m in models if m]
    if not chain:
        raise RuntimeError("No Anthropic models configured for vocab eval")

    last: Exception | None = None
    for index, model in enumerate(chain):
        try:
            resp = await anthropic_messages_with_retry(client, model=model, **kwargs)
            if index > 0:
                logger.info("Vocab AI eval succeeded with fallback model {}", model)
            return resp, model
        except APIStatusError as exc:
            last = exc
            code = getattr(exc, "status_code", None)
            has_next = index < len(chain) - 1
            if code in _RETRYABLE_STATUS and has_next:
                logger.warning(
                    "Anthropic model {} returned {} — trying {}",
                    model,
                    code,
                    chain[index + 1],
                )
                continue
            raise
    if last:
        raise last
    raise RuntimeError(
        "anthropic_messages_with_model_fallback failed without exception"
    )
