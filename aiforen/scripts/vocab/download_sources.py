"""Download NGSL / NAWL word lists from official sources (CC BY-SA)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from loguru import logger

from aiforen.scripts.vocab.sources import RAW_DIR, SOURCES


async def download_one(client: httpx.AsyncClient, url: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 100:
        logger.info("Skip (exists): {}", dest.name)
        return True
    try:
        resp = await client.get(url, follow_redirects=True, timeout=60.0)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        logger.info("Downloaded {} ({} bytes)", dest.name, len(resp.content))
        return True
    except Exception as exc:
        logger.error("Failed {}: {}", url, exc)
        return False


async def download_all(*, force: bool = False) -> int:
    if force:
        for sf in SOURCES:
            p = sf.local_path
            if p.exists():
                p.unlink()

    ok = 0
    async with httpx.AsyncClient(
        headers={"User-Agent": "Aiforen-Vocab-Crawler/1.0 (educational; CC-BY-SA)"}
    ) as client:
        for sf in SOURCES:
            if await download_one(client, sf.url, sf.local_path):
                ok += 1
    logger.info("Downloaded {}/{} files to {}", ok, len(SOURCES), RAW_DIR)
    return ok


def main() -> None:
    import sys

    force = "--force" in sys.argv
    asyncio.run(download_all(force=force))


if __name__ == "__main__":
    main()
