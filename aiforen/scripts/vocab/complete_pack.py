"""Complete one vocab pack end-to-end: select → enrich → mark complete."""

from __future__ import annotations

import argparse
import asyncio

from loguru import logger
from sqlalchemy import select

from aiforen.core import db as core_db
from aiforen.domain.sql_models import VocabPack
from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo
from aiforen.scripts.vocab.build_packs import BAND_PACKS, build_band_packs
from aiforen.scripts.vocab.enrich_pack import enrich_pack
from aiforen.scripts.vocab.pack_specs import get_pack_spec
from aiforen.scripts.vocab.select_pack_words import select_words_for_pack


async def complete_one_pack(
    repo: VocabLexiconRepo,
    pack_id: str,
    *,
    skip_select: bool = False,
    select_only: bool = False,
    fast: bool = False,
    gloss_only: bool = False,
) -> None:
    spec = get_pack_spec(pack_id)
    if not spec:
        raise ValueError(f"Unknown pack_id: {pack_id}")

    pack_row = (
        await repo.s.execute(select(VocabPack).where(VocabPack.pack_id == pack_id))
    ).scalar_one_or_none()
    if not pack_row:
        await repo.upsert_pack(
            {
                "pack_id": spec["pack_id"],
                "title": spec["title"],
                "description": spec.get("description", ""),
                "category": spec.get("category", "General"),
                "task_type": "Both",
                "exam_type": spec.get("exam_type", "ielts"),
                "pack_family": spec.get("pack_family", "band"),
                "skill_focus": spec.get("skill_focus"),
                "topic": spec.get("topic"),
                "source_band_min": 0.0,
                "source_band_max": 9.0,
                "target_band_min": spec.get("target_band_min", 0.0),
                "target_band_max": spec.get("target_band_max", 9.0),
                "sort_order": spec.get("sort_order", 0),
                "is_active": True,
                "is_premium": spec.get("is_premium", False),
                "target_word_count": spec.get("target_count", 12),
                "content_status": "selecting",
            }
        )
        pack_row = (
            await repo.s.execute(select(VocabPack).where(VocabPack.pack_id == pack_id))
        ).scalar_one()

    if not gloss_only:
        pack_row.content_status = "selecting"
        await repo.s.flush()

    if not skip_select and not gloss_only:
        words = await select_words_for_pack(repo, pack_id)
        pack_row.target_word_count = len(words)
    elif gloss_only or skip_select:
        from sqlalchemy import func

        from aiforen.domain.sql_models import VocabPackItem

        cnt = await repo.s.scalar(
            select(func.count())
            .select_from(VocabPackItem)
            .where(VocabPackItem.pack_id == pack_id)
        )
        pack_row.target_word_count = int(cnt or pack_row.target_word_count)

    if select_only:
        pack_row.completed_word_count = 0
        pack_row.content_status = "draft"
        await repo.s.flush()
        logger.info(
            "Pack {} selected {} words (enrich pending)",
            pack_id,
            pack_row.target_word_count,
        )
        return

    if not gloss_only:
        pack_row.content_status = "enriching"
        await repo.s.flush()

    n = await enrich_pack(repo, pack_id, fast=fast, gloss_only=gloss_only)
    pack_row.completed_word_count = n
    if gloss_only:
        await repo.s.flush()
        logger.info("Pack {} gloss-only done ({} vi_gloss updated)", pack_id, n)
        return

    pack_row.content_status = "complete"
    await repo.s.flush()
    logger.info("Pack {} complete ({} words enriched)", pack_id, n)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Complete one vocab pack")
    parser.add_argument("pack_id", nargs="?", help="e.g. pack_band6_task1_trends")
    parser.add_argument("--all", action="store_true", help="Complete all 15 packs")
    parser.add_argument(
        "--init-packs", action="store_true", help="Upsert pack metadata first"
    )
    parser.add_argument(
        "--skip-select", action="store_true", help="Keep current pack items"
    )
    parser.add_argument(
        "--select-only",
        action="store_true",
        help="Fill pack items only; skip dictionary enrich (fast bulk fill)",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Fast enrich: transipy VI gloss + template prompts/MCQ (no dictionary/LLM)",
    )
    parser.add_argument(
        "--gloss-only",
        action="store_true",
        help="Transipy vi_gloss + prompts only; keep existing definition_en and examples",
    )
    args = parser.parse_args()

    if args.gloss_only and args.fast:
        parser.error(
            "Use --gloss-only alone (not with --fast); --fast overwrites definitions."
        )
    if args.gloss_only and args.select_only:
        parser.error("--gloss-only cannot be used with --select-only")
    if args.gloss_only and not args.skip_select:
        parser.error("--gloss-only requires --skip-select (keeps current pack items)")

    core_db.init_pg()
    sm = core_db.pg_sessionmaker()

    async with sm() as session:
        repo = VocabLexiconRepo(session)
        if args.init_packs:
            await build_band_packs(repo)
        if args.all:
            for spec in BAND_PACKS:
                await complete_one_pack(
                    repo,
                    spec["pack_id"],
                    skip_select=args.skip_select,
                    select_only=args.select_only,
                    fast=args.fast,
                    gloss_only=args.gloss_only,
                )
        elif args.pack_id:
            await complete_one_pack(
                repo,
                args.pack_id,
                skip_select=args.skip_select,
                select_only=args.select_only,
                fast=args.fast,
                gloss_only=args.gloss_only,
            )
        else:
            parser.print_help()
            return
        await session.commit()

    logger.info("Done")


if __name__ == "__main__":
    asyncio.run(main())
