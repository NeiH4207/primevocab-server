"""Google Cloud Translation API v2 (batch-friendly)."""

from __future__ import annotations

import html
from functools import lru_cache
from typing import List

import httpx

from aiforen.core.config import get_settings

_TRANSLATE_URL = "https://translation.googleapis.com/language/translate/v2"
_BATCH_SIZE = 100


class GoogleTranslateClient:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    async def translate_batch(
        self,
        texts: List[str],
        *,
        target_language: str = "vi",
        source_language: str = "en",
    ) -> List[str]:
        if not texts:
            return []
        if not self.enabled:
            return list(texts)

        out: List[str] = []
        async with httpx.AsyncClient(timeout=60.0) as client:
            for i in range(0, len(texts), _BATCH_SIZE):
                chunk = texts[i : i + _BATCH_SIZE]
                resp = await client.post(
                    _TRANSLATE_URL,
                    params={"key": self._api_key},
                    json={
                        "q": chunk,
                        "target": target_language,
                        "source": source_language,
                        "format": "text",
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
                translations = payload.get("data", {}).get("translations") or []
                for item in translations:
                    raw = str(item.get("translatedText") or "")
                    out.append(html.unescape(raw))
        return out


@lru_cache
def get_translate_client() -> GoogleTranslateClient:
    settings = get_settings()
    return GoogleTranslateClient(settings.google_translate_api_key or "")
