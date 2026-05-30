"""Fill unified band packs from NGSL/NAWL pool; stat_labels for analytics only."""

from __future__ import annotations

from typing import List, Optional, Set, Tuple
from uuid import UUID

from loguru import logger
from sqlalchemy import or_, select

from aiforen.domain.sql_models import VocabLexeme
from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo, lexeme_id_for
from aiforen.scripts.vocab.pack_specs import get_pack_spec, infer_stat_labels

XLSX_SOURCE = "Vocabulary.xlsx"
_SEASON_SORT = {
    "Season 1": 0,
    "Season 2": 1,
    "Season 3": 2,
    "Season 4": 3,
    "Season 5": 4,
    "Season 6": 5,
    "Season 7": 6,
    "New words": 7,
}


def _from_xlsx_source(lx: VocabLexeme) -> bool:
    for src in lx.sources or []:
        if src.get("name") == XLSX_SOURCE:
            return True
    return False


def _xlsx_sort_key(lx: VocabLexeme) -> tuple:
    for src in lx.sources or []:
        if src.get("name") == XLSX_SOURCE:
            season = str(src.get("season", ""))
            order = int(src.get("order", 9999))
            return (_SEASON_SORT.get(season, 99), order, lx.lemma)
    return (99, 9999, lx.lemma)


async def select_words_for_pack(
    repo: VocabLexiconRepo,
    pack_id: str,
    *,
    exclude_lexeme_ids: Optional[Set[UUID]] = None,
) -> List[Tuple[str, str]]:
    spec = get_pack_spec(pack_id)
    if not spec:
        raise ValueError(f"Unknown pack_id: {pack_id}")

    target = int(spec.get("target_count", 20))
    core: List[Tuple[str, str]] = list(spec.get("core_words") or [])
    core_only = bool(spec.get("core_only"))
    rank_min = int(spec.get("rank_min", 1))
    rank_max = int(spec.get("rank_max", 2809))
    prefer_academic = bool(spec.get("prefer_academic"))
    pool_mode = str(spec.get("pool_mode", "rank"))

    chosen: List[Tuple[str, str]] = []
    seen = set()
    core_set = set()

    for lemma, pos in core:
        key = (lemma.lower(), pos.lower())
        if key not in seen:
            chosen.append(key)
            seen.add(key)
            core_set.add(key)

    exclude = exclude_lexeme_ids or set()

    if not core_only:
        if pool_mode == "xlsx":
            stmt = select(VocabLexeme).where(VocabLexeme.status != "deprecated")
            pool = [
                lx
                for lx in (await repo.s.execute(stmt)).scalars().all()
                if _from_xlsx_source(lx) and lx.id not in exclude
            ]
            target = min(target, len(pool))
            pool = sorted(pool, key=_xlsx_sort_key)
            passes = [pool]
        elif pool_mode == "gre":
            stmt = select(VocabLexeme).where(
                VocabLexeme.status != "deprecated",
                or_(
                    VocabLexeme.is_academic.is_(True),
                    VocabLexeme.gre_tier.isnot(None),
                    VocabLexeme.exam_types.contains(["gre"]),
                ),
            )
            pool = list((await repo.s.execute(stmt)).scalars().all())
            target = min(target, len(pool))

            def _gre_sort_key(lx: VocabLexeme) -> tuple:
                in_gre = "gre" in (lx.exam_types or [])
                rank = lx.frequency_rank or 0
                tier = {"hard": 0, "medium": 1, "easy": 2}.get(lx.gre_tier or "", 3)
                return (0 if in_gre else 1, tier, -rank)

            pool = sorted(pool, key=_gre_sort_key)
            passes = [pool]
        else:
            stmt = select(VocabLexeme).where(
                VocabLexeme.status != "deprecated",
                VocabLexeme.frequency_rank.isnot(None),
                VocabLexeme.frequency_rank >= rank_min,
                VocabLexeme.frequency_rank <= rank_max,
            )
            pool = sorted(
                (await repo.s.execute(stmt)).scalars().all(),
                key=lambda x: x.frequency_rank or 9999,
            )

            def _is_academic_pref(lx: VocabLexeme) -> bool:
                return bool(lx.is_academic) or "gre" in (lx.exam_types or [])

            if prefer_academic:
                academic = [lx for lx in pool if _is_academic_pref(lx)]
                general = [lx for lx in pool if not _is_academic_pref(lx)]
                passes = [academic, general]
            else:
                passes = [pool]

        for batch in passes:
            for lx in batch:
                if len(chosen) >= target:
                    break
                if lx.id in exclude:
                    continue
                key = (lx.lemma, lx.pos)
                if key not in seen:
                    chosen.append(key)
                    seen.add(key)
            if len(chosen) >= target:
                break

        if len(chosen) < target and prefer_academic:
            stmt = select(VocabLexeme).where(
                VocabLexeme.status != "deprecated",
                VocabLexeme.is_academic.is_(True),
                VocabLexeme.frequency_rank.is_(None),
            )
            extra = sorted(
                (await repo.s.execute(stmt)).scalars().all(),
                key=lambda x: (x.difficulty_score or 0, x.lemma),
                reverse=True,
            )
            for lx in extra:
                if len(chosen) >= target:
                    break
                if lx.id in exclude:
                    continue
                key = (lx.lemma, lx.pos)
                if key not in seen:
                    chosen.append(key)
                    seen.add(key)

    lexeme_ids = [lexeme_id_for(lemma, pos) for lemma, pos in chosen]
    labels_list = [infer_stat_labels(lemma) for lemma, _ in chosen]
    core_flags = [(lemma, pos) in core_set for lemma, pos in chosen]

    await repo.set_pack_items(
        pack_id,
        lexeme_ids,
        stat_labels=labels_list,
        is_core_flags=core_flags,
    )
    logger.info(
        "Pack {} → {} words (core={}, from_pool={})",
        pack_id,
        len(chosen),
        len(core_set),
        len(chosen) - len(core_set),
    )
    return chosen
