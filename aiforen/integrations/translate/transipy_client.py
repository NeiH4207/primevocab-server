"""Batch translation via transipy (Google Translate + parallel workers).

Uses transipy.trans_helper.translate — see https://github.com/NeiH4207/transipy
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from typing import Dict, List

from loguru import logger
from transipy.trans_helper import translate

from aiforen.core.config import get_settings


class TransipyClient:
    """Parallel EN→VI gloss using transipy's translate() per unique string."""

    def __init__(self, *, max_workers: int = 16) -> None:
        self._max_workers = max(1, min(max_workers, 32))

    @property
    def enabled(self) -> bool:
        return True

    async def translate_batch(
        self,
        texts: List[str],
        *,
        target_language: str = "vi",
        source_language: str = "en",
    ) -> List[str]:
        if not texts:
            return []
        return await asyncio.to_thread(
            self._translate_sync,
            texts,
            source_language,
            target_language,
        )

    def _translate_sync(self, texts: List[str], src: str, dest: str) -> List[str]:
        ordered_unique: List[str] = []
        seen: set[str] = set()
        for t in texts:
            key = str(t)
            if key not in seen:
                seen.add(key)
                ordered_unique.append(key)

        mapping: Dict[str, str] = {}
        total = len(ordered_unique)
        logger.info(
            "transipy translating {} unique strings (workers={})",
            total,
            self._max_workers,
        )

        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {
                pool.submit(translate, text, src, dest): text for text in ordered_unique
            }
            done = 0
            for fut in as_completed(futures):
                text = futures[fut]
                try:
                    mapping[text] = str(fut.result())
                except Exception as exc:
                    logger.warning("transipy miss {}: {}", text, exc)
                    mapping[text] = text
                done += 1
                if done % 100 == 0 or done == total:
                    logger.info("transipy progress {}/{}", done, total)

        return [mapping.get(str(t), str(t)) for t in texts]


@lru_cache
def get_transipy_client() -> TransipyClient:
    settings = get_settings()
    return TransipyClient(max_workers=settings.transipy_chunk_size)


def get_translate_client() -> TransipyClient:
    return get_transipy_client()
