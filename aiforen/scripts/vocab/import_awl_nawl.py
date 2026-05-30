"""Import AWL + NAWL sample lists; flag academic lexemes."""

from __future__ import annotations

import csv
from pathlib import Path

from loguru import logger

from aiforen.scripts.vocab._common import pg_session, run_async

AWL = Path(__file__).parent / "data" / "awl_sample.tsv"
NAWL = Path(__file__).parent / "data" / "nawl_sample.tsv"


async def _import_file(
    repo,
    path: Path,
    *,
    source_name: str,
    band: float,
) -> int:
    count = 0
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            lemma = (row.get("lemma") or "").strip().lower()
            pos = (row.get("pos") or "noun").strip().lower()
            if not lemma:
                continue
            sublist = row.get("sublist") or row.get("rank")
            await repo.upsert_lexeme(
                lemma=lemma,
                pos=pos,
                is_academic=True,
                ielts_band_min=band,
                ielts_band_max=band + 1.0,
                exam_types=["ielts", "gre"],
                sources=[
                    {
                        "name": source_name,
                        "sublist": sublist,
                        "license": "CC-BY-SA-4.0",
                    }
                ],
                status="draft",
            )
            count += 1
    return count


async def main() -> None:
    async for repo in pg_session():
        awl_n = await _import_file(repo, AWL, source_name="AWL", band=6.5)
        nawl_n = await _import_file(repo, NAWL, source_name="NAWL", band=7.5)
        logger.info("AWL: {}, NAWL: {}", awl_n, nawl_n)


if __name__ == "__main__":
    run_async(main())
