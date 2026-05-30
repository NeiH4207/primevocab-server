"""Postgres vocabulary lexicon repository + legacy Mongo DTO adapter."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aiforen.domain.sql_models import (
    VocabCollocation,
    VocabLegacyWordMap,
    VocabLexeme,
    VocabPack,
    VocabPackItem,
    VocabQuestion,
    VocabReviewQueue,
    VocabSense,
    VocabWordForm,
)
from aiforen.domain.vocab_pack_tracks import track_id_for_pack

LEXEME_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def lexeme_id_for(lemma: str, pos: str) -> uuid.UUID:
    """Stable lexeme id from lemma + pos (survives re-seeds)."""

    key = f"aiforen.vocab:{lemma.strip().lower()}:{pos.strip().lower()}"
    return uuid.uuid5(LEXEME_NAMESPACE, key)


def _band_difficulty_label(band: float) -> str:
    if band <= 5.0:
        return "beginner"
    if band >= 7.5:
        return "advanced"
    return "intermediate"


class VocabLexiconRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def has_content(self) -> bool:
        row = await self.s.execute(select(func.count()).select_from(VocabLexeme))
        return int(row.scalar_one()) > 0

    async def has_filled_packs(self, *, min_pack_items: int = 100) -> bool:
        """True when vocab packs look populated (post fill_packs / import)."""
        row = await self.s.execute(select(func.count()).select_from(VocabPackItem))
        return int(row.scalar_one() or 0) >= min_pack_items

    async def get_lexeme(self, lexeme_id: uuid.UUID) -> Optional[VocabLexeme]:
        stmt = (
            select(VocabLexeme)
            .where(VocabLexeme.id == lexeme_id)
            .options(
                selectinload(VocabLexeme.senses),
                selectinload(VocabLexeme.collocations),
                selectinload(VocabLexeme.questions),
            )
        )
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def get_lexeme_by_lemma_pos(
        self, lemma: str, pos: str
    ) -> Optional[VocabLexeme]:
        lid = lexeme_id_for(lemma, pos)
        return await self.get_lexeme(lid)

    async def resolve_word_id(self, word_id: str) -> Optional[uuid.UUID]:
        """Resolve API word_id: UUID string, legacy mongo id, or lemma lookup."""
        return (await self.resolve_word_ids([word_id])).get(word_id)

    async def resolve_word_ids(self, word_ids: List[str]) -> Dict[str, uuid.UUID]:
        """Batch-resolve API word_ids to lexeme UUIDs."""
        if not word_ids:
            return {}
        out: Dict[str, uuid.UUID] = {}
        legacy_ids: List[str] = []
        for wid in word_ids:
            if not wid:
                continue
            try:
                out[wid] = uuid.UUID(wid)
            except ValueError:
                legacy_ids.append(wid)
        if legacy_ids:
            stmt = select(
                VocabLegacyWordMap.legacy_word_id,
                VocabLegacyWordMap.lexeme_id,
            ).where(VocabLegacyWordMap.legacy_word_id.in_(legacy_ids))
            for legacy_id, lexeme_id in (await self.s.execute(stmt)).all():
                out[legacy_id] = lexeme_id
        return out

    async def get_word_dtos_batch(
        self,
        word_ids: List[str],
        *,
        pack_id: Optional[str] = None,
        mastery_steps: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """Batch-hydrate word DTOs (one lexeme query + selectinload)."""
        if not word_ids:
            return {}
        id_map = await self.resolve_word_ids(word_ids)
        lexeme_ids = list(dict.fromkeys(id_map.values()))
        if not lexeme_ids:
            return {wid: None for wid in word_ids}

        stmt = (
            select(VocabLexeme)
            .where(VocabLexeme.id.in_(lexeme_ids))
            .options(
                selectinload(VocabLexeme.senses),
                selectinload(VocabLexeme.collocations),
                selectinload(VocabLexeme.questions),
            )
        )
        lexemes = (await self.s.execute(stmt)).scalars().all()
        by_lex = {lx.id: lx for lx in lexemes}

        out: Dict[str, Optional[Dict[str, Any]]] = {}
        for wid in word_ids:
            lex_id = id_map.get(wid)
            if not lex_id or lex_id not in by_lex:
                out[wid] = None
                continue
            step = int((mastery_steps or {}).get(wid, 0) or 0)
            out[wid] = self.lexeme_to_word_dto(
                by_lex[lex_id], pack_id=pack_id, mastery_step=step
            )
        return out

    async def list_packs(
        self,
        *,
        current_band: Optional[float] = None,
        target_band: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(VocabPack)
            .where(VocabPack.is_active.is_(True))
            .order_by(VocabPack.sort_order)
        )
        if current_band is not None:
            stmt = stmt.where(
                VocabPack.source_band_min <= current_band,
                VocabPack.source_band_max >= current_band,
            )
        if target_band is not None:
            stmt = stmt.where(
                VocabPack.target_band_min <= target_band,
                VocabPack.target_band_max >= target_band,
            )
        packs = (await self.s.execute(stmt)).scalars().all()
        return [self._pack_to_dict(p) for p in packs]

    async def count_words_by_packs(self, pack_ids: List[str]) -> Dict[str, int]:
        if not pack_ids:
            return {}
        stmt = (
            select(VocabPackItem.pack_id, func.count())
            .where(VocabPackItem.pack_id.in_(pack_ids))
            .group_by(VocabPackItem.pack_id)
        )
        rows = (await self.s.execute(stmt)).all()
        return {str(pack_id): int(count) for pack_id, count in rows}

    async def pack_ids_for_word_ids(self, word_ids: List[str]) -> Dict[str, List[str]]:
        """Map API word_id → pack_ids that contain the lexeme."""
        if not word_ids:
            return {}
        id_map = await self.resolve_word_ids(word_ids)
        if not id_map:
            return {}
        lexeme_ids = list(dict.fromkeys(id_map.values()))
        rows = (
            await self.s.execute(
                select(VocabPackItem.lexeme_id, VocabPackItem.pack_id).where(
                    VocabPackItem.lexeme_id.in_(lexeme_ids)
                )
            )
        ).all()
        packs_by_lex: Dict[uuid.UUID, List[str]] = defaultdict(list)
        for lexeme_id, pack_id in rows:
            packs_by_lex[lexeme_id].append(str(pack_id))
        out: Dict[str, List[str]] = {}
        for wid in word_ids:
            lex_id = id_map.get(wid)
            if lex_id:
                out[wid] = list(dict.fromkeys(packs_by_lex.get(lex_id, [])))
        return out

    async def get_pack(self, pack_id: str) -> Optional[Dict[str, Any]]:
        pack = (
            await self.s.execute(select(VocabPack).where(VocabPack.pack_id == pack_id))
        ).scalar_one_or_none()
        if not pack:
            return None
        return self._pack_to_dict(pack)

    async def list_pack_lexeme_ids(self, pack_id: str) -> List[Tuple[uuid.UUID, int]]:
        stmt = (
            select(VocabPackItem.lexeme_id, VocabPackItem.order_index)
            .where(VocabPackItem.pack_id == pack_id)
            .order_by(VocabPackItem.order_index)
        )
        rows = (await self.s.execute(stmt)).all()
        return [(r[0], r[1]) for r in rows]

    async def list_words_for_pack(
        self,
        pack_id: str,
        *,
        limit: int = 5000,
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(VocabPackItem)
            .where(VocabPackItem.pack_id == pack_id)
            .order_by(VocabPackItem.order_index)
            .limit(limit)
            .options(
                selectinload(VocabPackItem.lexeme).selectinload(VocabLexeme.senses),
                selectinload(VocabPackItem.lexeme).selectinload(
                    VocabLexeme.collocations
                ),
                selectinload(VocabPackItem.lexeme).selectinload(VocabLexeme.questions),
            )
        )
        items = (await self.s.execute(stmt)).scalars().all()
        out: List[Dict[str, Any]] = []
        for item in items:
            dto = self.lexeme_to_word_dto(
                item.lexeme, pack_id=pack_id, sense_id=item.sense_id
            )
            if dto:
                out.append(dto)
        return out

    async def get_word_dto(
        self,
        word_id: str,
        *,
        pack_id: Optional[str] = None,
        mastery_step: int = 0,
    ) -> Optional[Dict[str, Any]]:
        lexeme_id = await self.resolve_word_id(word_id)
        if not lexeme_id:
            return None
        lexeme = await self.get_lexeme(lexeme_id)
        if not lexeme:
            return None
        if not pack_id:
            stmt = (
                select(VocabPackItem.pack_id)
                .where(VocabPackItem.lexeme_id == lexeme_id)
                .limit(1)
            )
            pack_id = (await self.s.execute(stmt)).scalar_one_or_none()
        return self.lexeme_to_word_dto(
            lexeme, pack_id=pack_id, mastery_step=mastery_step
        )

    async def lookup_labels_for_word_ids(
        self, word_ids: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Map API word_id → lemma + stat_labels (topic analytics)."""
        if not word_ids:
            return {}
        # Batch-resolve in a single query instead of one round-trip per word_id.
        wid_to_lex: Dict[str, uuid.UUID] = await self.resolve_word_ids(word_ids)
        lexeme_ids: List[uuid.UUID] = list(dict.fromkeys(wid_to_lex.values()))
        if not lexeme_ids:
            return {}

        stmt = (
            select(
                VocabLexeme.id,
                VocabLexeme.lemma,
                VocabPackItem.stat_labels,
            )
            .join(VocabPackItem, VocabPackItem.lexeme_id == VocabLexeme.id)
            .where(VocabLexeme.id.in_(lexeme_ids))
        )
        rows = (await self.s.execute(stmt)).all()
        by_lex: Dict[uuid.UUID, Dict[str, Any]] = {}
        for lex_id, lemma, labels in rows:
            existing = by_lex.get(lex_id)
            merged = list(labels or [])
            if existing:
                for lb in merged:
                    if lb not in existing["stat_labels"]:
                        existing["stat_labels"].append(lb)
            else:
                by_lex[lex_id] = {"lemma": lemma, "stat_labels": merged}

        missing = [lid for lid in lexeme_ids if lid not in by_lex]
        if missing:
            lemma_rows = (
                await self.s.execute(
                    select(VocabLexeme.id, VocabLexeme.lemma).where(
                        VocabLexeme.id.in_(missing)
                    )
                )
            ).all()
            for lex_id, lemma in lemma_rows:
                by_lex[lex_id] = {"lemma": lemma, "stat_labels": []}

        out: Dict[str, Dict[str, Any]] = {}
        for wid, lex_id in wid_to_lex.items():
            if lex_id in by_lex:
                out[wid] = by_lex[lex_id]
        return out

    async def list_lexemes(
        self,
        *,
        limit: int = 50,
        skip: int = 0,
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(VocabLexeme)
            .where(VocabLexeme.status != "deprecated")
            .order_by(VocabLexeme.lemma)
            .offset(skip)
            .limit(limit)
            .options(
                selectinload(VocabLexeme.senses),
                selectinload(VocabLexeme.collocations),
                selectinload(VocabLexeme.questions),
            )
        )
        lexemes = (await self.s.execute(stmt)).scalars().all()
        return [
            dto for lx in lexemes if (dto := self.lexeme_to_word_dto(lx)) is not None
        ]

    async def categories(self) -> List[str]:
        stmt = (
            select(VocabPack.category).where(VocabPack.is_active.is_(True)).distinct()
        )
        return sorted({r[0] for r in (await self.s.execute(stmt)).all() if r[0]})

    def _pack_to_dict(self, pack: VocabPack) -> Dict[str, Any]:
        pack_id = str(pack.pack_id or "")
        pack_family = getattr(pack, "pack_family", "band") or "band"
        if pack_id.startswith("pack_oxford_"):
            pack_family = "cefr"
        return {
            "pack_id": pack.pack_id,
            "title": pack.title,
            "description": pack.description or "",
            "category": pack.category,
            "task_type": pack.task_type,
            "exam_type": pack.exam_type,
            "skill_focus": pack.skill_focus,
            "topic": pack.topic,
            "source_band_min": float(pack.source_band_min),
            "source_band_max": float(pack.source_band_max),
            "target_band_min": float(pack.target_band_min),
            "target_band_max": float(pack.target_band_max),
            "sort_order": pack.sort_order,
            "is_active": pack.is_active,
            "is_premium": pack.is_premium,
            "pack_family": pack_family,
            "cefr_level": getattr(pack, "cefr_level", None)
                or (pack_id.replace("pack_oxford_", "").upper() if pack_id.startswith("pack_oxford_") else None),
            "content_status": getattr(pack, "content_status", "draft"),
            "target_word_count": getattr(pack, "target_word_count", 12),
            "completed_word_count": getattr(pack, "completed_word_count", 0),
            "created_at": pack.created_at,
            "updated_at": pack.updated_at,
        }

    def _primary_sense(
        self, lexeme: VocabLexeme, sense_id: Optional[uuid.UUID] = None
    ) -> Optional[VocabSense]:
        senses = sorted(lexeme.senses or [], key=lambda s: s.sense_order)
        if not senses:
            return None
        if sense_id:
            for s in senses:
                if s.id == sense_id:
                    return s
        return senses[0]

    def _pick_question(
        self,
        lexeme: VocabLexeme,
        *,
        mastery_step: int = 0,
        sense_id: Optional[uuid.UUID] = None,
    ) -> Optional[VocabQuestion]:
        approved = [
            q for q in (lexeme.questions or []) if q.status in ("validated", "approved")
        ]
        if not approved:
            # LLM/batch pipeline writes "generated" until human review; still show in study flow.
            approved = [q for q in (lexeme.questions or []) if q.status == "generated"]
        if not approved:
            return None
        if mastery_step <= 2:
            preferred = ("meaning_mcq", "cloze")
        elif mastery_step <= 4:
            preferred = ("collocation", "usage_fix", "meaning_mcq")
        else:
            preferred = ("meaning_mcq",)
        for qtype in preferred:
            for q in approved:
                if q.type == qtype:
                    return q
        return approved[0]

    def _active_questions(self, lexeme: VocabLexeme) -> List[VocabQuestion]:
        rows = [
            q
            for q in (lexeme.questions or [])
            if q.status in ("validated", "approved", "generated")
        ]
        return sorted(
            rows,
            key=lambda q: (
                int(q.mastery_slot or 99),
                str(q.type or ""),
            ),
        )

    def _questions_for_track(
        self,
        lexeme: VocabLexeme,
        track_id: Optional[str],
        *,
        sense_id: Optional[uuid.UUID] = None,
    ) -> List[VocabQuestion]:
        rows = self._active_questions(lexeme)
        if track_id:
            rows = [q for q in rows if (q.track_id or "") == track_id]
        if sense_id is not None:
            rows = [q for q in rows if q.sense_id is None or q.sense_id == sense_id]
        return rows

    def _question_to_quiz_step(self, question: VocabQuestion) -> Dict[str, Any]:
        payload = question.payload if isinstance(question.payload, dict) else {}
        interaction = (question.interaction_kind or "mcq").strip().lower()
        slot = int(question.mastery_slot or question.difficulty or 1)
        step: Dict[str, Any] = {
            "question_id": str(question.id),
            "mastery_slot": max(1, min(5, slot)),
            "track_id": question.track_id,
            "task_type": question.type,
            "skill": question.skill,
            "level_code": question.level_code,
            "interaction_kind": interaction,
            "prompt": question.prompt,
            "explanation": question.explanation,
            "payload": payload,
        }
        context = payload.get("context")
        if isinstance(context, str) and context.strip():
            step["context"] = context.strip()
        if interaction == "mcq" and (question.options or []):
            step["mcq"] = {
                "question": question.prompt,
                "options": question.options or [],
                "correct_option_id": question.correct_option_id,
                "explanation": question.explanation,
            }
        return step

    async def get_question(self, question_id: uuid.UUID) -> Optional[VocabQuestion]:
        return (
            await self.s.execute(
                select(VocabQuestion).where(VocabQuestion.id == question_id)
            )
        ).scalar_one_or_none()

    def lexeme_to_word_dto(
        self,
        lexeme: VocabLexeme,
        *,
        pack_id: Optional[str] = None,
        sense_id: Optional[uuid.UUID] = None,
        mastery_step: int = 0,
    ) -> Optional[Dict[str, Any]]:
        sense = self._primary_sense(lexeme, sense_id)
        band = float(lexeme.ielts_band_min or lexeme.ielts_band_max or 6.0)
        word_id = str(lexeme.id)
        category = pack_id or (lexeme.exam_types[0] if lexeme.exam_types else "General")
        if pack_id and "_" in pack_id:
            category = pack_id.replace("pack_", "").replace("_", " ").title()

        if not sense:
            return {
                "word_id": word_id,
                "pack_id": pack_id,
                "word": lexeme.display_word,
                "definition": lexeme.display_word,
                "vi_gloss": None,
                "pronunciation": "",
                "audio_url": None,
                "part_of_speech": lexeme.pos,
                "category": category,
                "task_type": "Both",
                "band_score": band,
                "difficulty_level": _band_difficulty_label(band),
                "examples": [],
                "synonyms": [],
                "collocations": [],
                "usage": None,
                "tips": [],
                "mcq": None,
                "vi_prompt": None,
                "vi_translate_prompt": None,
                "topic_prompt": None,
                "example_good_sentence": None,
                "tags": [lexeme.lemma],
                "total_attempts": 0,
                "success_rate": 0.0,
                "created_at": (
                    lexeme.created_at.isoformat()
                    if lexeme.created_at
                    else datetime.utcnow().isoformat()
                ),
                "updated_at": (
                    lexeme.updated_at.isoformat()
                    if lexeme.updated_at
                    else datetime.utcnow().isoformat()
                ),
                "is_active": lexeme.status != "deprecated",
                "lexeme_id": word_id,
                "question_id": None,
                "question_type": None,
            }

        collocations = [c.phrase for c in (lexeme.collocations or [])[:8]]
        track = track_id_for_pack(pack_id)
        track_questions = self._questions_for_track(
            lexeme, track, sense_id=sense.id if sense else None
        )
        quiz_steps = [self._question_to_quiz_step(q) for q in track_questions]
        question = self._pick_question(
            lexeme, mastery_step=mastery_step, sense_id=sense_id
        )
        if track_questions:
            slot_target = max(1, min(5, mastery_step + 1)) if mastery_step else 1
            question = next(
                (q for q in track_questions if int(q.mastery_slot or 0) == slot_target),
                track_questions[0],
            )
        mcq = None
        if question and (question.interaction_kind or "mcq") == "mcq":
            mcq = {
                "question": question.prompt,
                "options": question.options or [],
                "correct_option_id": question.correct_option_id,
                "explanation": question.explanation,
            }
        elif quiz_steps:
            first_mcq = next((s for s in quiz_steps if s.get("mcq")), None)
            if first_mcq:
                mcq = first_mcq["mcq"]
                if question is None:
                    question = next(
                        (
                            q
                            for q in track_questions
                            if str(q.id) == first_mcq["question_id"]
                        ),
                        track_questions[0] if track_questions else None,
                    )
        example = sense.ielts_example or sense.gre_example or ""

        return {
            "word_id": word_id,
            "pack_id": pack_id,
            "word": lexeme.display_word,
            "definition": sense.definition_en,
            "vi_gloss": sense.vi_gloss,
            "pronunciation": sense.phonetic or "",
            "audio_url": sense.audio_url,
            "part_of_speech": lexeme.pos,
            "category": category,
            "task_type": "Both",
            "band_score": band,
            "difficulty_level": _band_difficulty_label(band),
            "examples": (
                [
                    {
                        "correct": example,
                        "context": "IELTS writing",
                        "explanation": sense.usage_note or "",
                    }
                ]
                if example
                else []
            ),
            "synonyms": (
                list(sense.synonyms) if isinstance(sense.synonyms, list) else []
            ),
            "collocations": collocations,
            "usage": sense.usage_note,
            "tips": sense.tips if isinstance(sense.tips, list) else [],
            "mcq": mcq,
            "quiz_track_id": track,
            "quiz_steps": quiz_steps,
            "vi_prompt": sense.vi_translate_prompt,
            "vi_translate_prompt": sense.vi_translate_prompt,
            "topic_prompt": sense.topic_prompt,
            "example_good_sentence": example,
            "tags": list(sense.topic_tags or []),
            "total_attempts": 0,
            "success_rate": 0.0,
            "created_at": (
                lexeme.created_at.isoformat()
                if lexeme.created_at
                else datetime.utcnow().isoformat()
            ),
            "updated_at": (
                lexeme.updated_at.isoformat()
                if lexeme.updated_at
                else datetime.utcnow().isoformat()
            ),
            "is_active": lexeme.status != "deprecated",
            "lexeme_id": word_id,
            "question_id": str(question.id) if question else None,
            "question_type": question.type if question else None,
        }

    # ---------- upsert helpers for pipeline / seed ----------

    async def upsert_lexeme(
        self,
        *,
        lemma: str,
        pos: str,
        display_word: Optional[str] = None,
        cefr_level: Optional[str] = None,
        ielts_band_min: Optional[float] = None,
        ielts_band_max: Optional[float] = None,
        gre_tier: Optional[str] = None,
        frequency_rank: Optional[int] = None,
        difficulty_score: Optional[float] = None,
        is_academic: bool = False,
        exam_types: Optional[List[str]] = None,
        sources: Optional[List[Dict[str, Any]]] = None,
        status: str = "draft",
    ) -> VocabLexeme:
        lid = lexeme_id_for(lemma, pos)
        existing = await self.get_lexeme(lid)
        if existing:
            existing.display_word = display_word or existing.display_word
            existing.cefr_level = cefr_level or existing.cefr_level
            existing.ielts_band_min = (
                ielts_band_min
                if ielts_band_min is not None
                else existing.ielts_band_min
            )
            existing.ielts_band_max = (
                ielts_band_max
                if ielts_band_max is not None
                else existing.ielts_band_max
            )
            existing.gre_tier = gre_tier or existing.gre_tier
            existing.frequency_rank = (
                frequency_rank
                if frequency_rank is not None
                else existing.frequency_rank
            )
            existing.difficulty_score = (
                difficulty_score
                if difficulty_score is not None
                else existing.difficulty_score
            )
            existing.is_academic = is_academic or existing.is_academic
            if exam_types:
                existing.exam_types = exam_types
            if sources:
                existing.sources = sources
            existing.status = status
            await self.s.flush()
            return existing

        lexeme = VocabLexeme(
            id=lid,
            lemma=lemma.strip().lower(),
            display_word=display_word or lemma,
            pos=pos.strip().lower(),
            cefr_level=cefr_level,
            ielts_band_min=ielts_band_min,
            ielts_band_max=ielts_band_max,
            gre_tier=gre_tier,
            frequency_rank=frequency_rank,
            difficulty_score=difficulty_score,
            is_academic=is_academic,
            exam_types=exam_types or ["ielts"],
            sources=sources or [],
            status=status,
        )
        self.s.add(lexeme)
        await self.s.flush()
        return lexeme

    async def upsert_primary_sense(
        self,
        lexeme_id: uuid.UUID,
        *,
        definition_en: str,
        vi_gloss: Optional[str] = None,
        vi_translate_prompt: Optional[str] = None,
        topic_prompt: Optional[str] = None,
        usage_note: Optional[str] = None,
        ielts_example: Optional[str] = None,
        gre_example: Optional[str] = None,
        phonetic: Optional[str] = None,
        audio_url: Optional[str] = None,
        topic_tags: Optional[List[str]] = None,
        tips: Optional[List[str]] = None,
        synonyms: Optional[List[str]] = None,
    ) -> VocabSense:
        stmt = select(VocabSense).where(
            VocabSense.lexeme_id == lexeme_id, VocabSense.sense_order == 1
        )
        sense = (await self.s.execute(stmt)).scalar_one_or_none()
        if sense:
            sense.definition_en = definition_en
            if vi_gloss is not None:
                sense.vi_gloss = vi_gloss
            if vi_translate_prompt is not None:
                sense.vi_translate_prompt = vi_translate_prompt
            if topic_prompt is not None:
                sense.topic_prompt = topic_prompt
            if usage_note is not None:
                sense.usage_note = usage_note
            if ielts_example is not None:
                sense.ielts_example = ielts_example
            if gre_example is not None:
                sense.gre_example = gre_example
            if phonetic is not None:
                sense.phonetic = phonetic
            if audio_url is not None:
                sense.audio_url = audio_url
            if topic_tags:
                sense.topic_tags = topic_tags
            if tips:
                sense.tips = tips
            if synonyms is not None:
                sense.synonyms = synonyms
            await self.s.flush()
            return sense
        sense = VocabSense(
            lexeme_id=lexeme_id,
            sense_order=1,
            definition_en=definition_en,
            vi_gloss=vi_gloss,
            vi_translate_prompt=vi_translate_prompt,
            topic_prompt=topic_prompt,
            usage_note=usage_note,
            ielts_example=ielts_example,
            gre_example=gre_example,
            phonetic=phonetic,
            audio_url=audio_url,
            topic_tags=topic_tags or [],
            tips=tips or [],
            synonyms=synonyms or [],
        )
        self.s.add(sense)
        await self.s.flush()
        return sense

    async def patch_primary_sense_gloss(
        self,
        lexeme_id: uuid.UUID,
        *,
        vi_gloss: str,
        vi_translate_prompt: Optional[str] = None,
        topic_prompt: Optional[str] = None,
        synonyms: Optional[List[str]] = None,
    ) -> Optional[VocabSense]:
        """Update VI fields only; never changes definition_en or examples."""
        stmt = select(VocabSense).where(
            VocabSense.lexeme_id == lexeme_id,
            VocabSense.sense_order == 1,
        )
        sense = (await self.s.execute(stmt)).scalar_one_or_none()
        if not sense:
            return None
        sense.vi_gloss = vi_gloss
        if vi_translate_prompt is not None:
            sense.vi_translate_prompt = vi_translate_prompt
        if topic_prompt is not None:
            sense.topic_prompt = topic_prompt
        if synonyms is not None:
            sense.synonyms = synonyms
        await self.s.flush()
        return sense

    async def upsert_question(
        self,
        lexeme_id: uuid.UUID,
        *,
        qtype: str,
        prompt: str,
        options: List[Dict[str, Any]],
        correct_option_id: str,
        explanation: Optional[str] = None,
        difficulty: int = 3,
        status: str = "validated",
        sense_id: Optional[uuid.UUID] = None,
        generator_meta: Optional[Dict[str, Any]] = None,
    ) -> VocabQuestion:
        """One row per (lexeme_id, type); updates canonical row and drops duplicates."""
        meta = generator_meta or {}
        preferred_sources = (
            "llm_mcq_openai",
            "llm_mcq_batch",
            "enrich_pack",
            "bootstrap",
        )
        rows = (
            (
                await self.s.execute(
                    select(VocabQuestion).where(
                        VocabQuestion.lexeme_id == lexeme_id,
                        VocabQuestion.type == qtype,
                    )
                )
            )
            .scalars()
            .all()
        )

        def _rank(q: VocabQuestion) -> int:
            src = (q.generator_meta or {}).get("source") or ""
            try:
                return preferred_sources.index(src)
            except ValueError:
                return len(preferred_sources)

        if rows:
            rows_sorted = sorted(rows, key=_rank)
            keep = rows_sorted[0]
            for dup in rows_sorted[1:]:
                await self.s.delete(dup)  # type: ignore[attr-defined]
            keep.prompt = prompt
            keep.options = options
            keep.correct_option_id = correct_option_id
            keep.explanation = explanation
            keep.status = status
            keep.difficulty = difficulty
            if sense_id is not None:
                keep.sense_id = sense_id
            keep.generator_meta = {**(keep.generator_meta or {}), **meta}
            await self.s.flush()
            return keep

        q = VocabQuestion(
            lexeme_id=lexeme_id,
            sense_id=sense_id,
            type=qtype,
            prompt=prompt,
            options=options,
            correct_option_id=correct_option_id,
            explanation=explanation,
            difficulty=difficulty,
            status=status,
            generator_meta=meta,
        )
        self.s.add(q)
        await self.s.flush()
        return q

    async def upsert_pack(self, pack: Dict[str, Any]) -> None:
        existing = (
            await self.s.execute(
                select(VocabPack).where(VocabPack.pack_id == pack["pack_id"])
            )
        ).scalar_one_or_none()
        if existing:
            for key in (
                "title",
                "description",
                "category",
                "task_type",
                "exam_type",
                "pack_family",
                "skill_focus",
                "topic",
                "source_band_min",
                "source_band_max",
                "target_band_min",
                "target_band_max",
                "sort_order",
                "is_active",
                "is_premium",
                "content_status",
                "target_word_count",
                "completed_word_count",
            ):
                if key in pack:
                    setattr(existing, key, pack[key])
        else:
            self.s.add(
                VocabPack(
                    pack_id=pack["pack_id"],
                    title=pack["title"],
                    description=pack.get("description", ""),
                    category=pack.get("category", "General"),
                    task_type=pack.get("task_type", "Both"),
                    exam_type=pack.get("exam_type", "ielts"),
                    pack_family=pack.get("pack_family", "band"),
                    skill_focus=pack.get("skill_focus"),
                    topic=pack.get("topic"),
                    source_band_min=pack.get("source_band_min", 0),
                    source_band_max=pack.get("source_band_max", 9),
                    target_band_min=pack.get("target_band_min", 0),
                    target_band_max=pack.get("target_band_max", 9),
                    sort_order=pack.get("sort_order", 0),
                    is_active=pack.get("is_active", True),
                    is_premium=pack.get("is_premium", False),
                    content_status=pack.get("content_status", "draft"),
                    target_word_count=pack.get("target_word_count", 12),
                    completed_word_count=pack.get("completed_word_count", 0),
                )
            )
        await self.s.flush()

    async def set_pack_items(
        self,
        pack_id: str,
        lexeme_ids: List[uuid.UUID],
        *,
        sense_ids: Optional[List[Optional[uuid.UUID]]] = None,
        stat_labels: Optional[List[List[str]]] = None,
        is_core_flags: Optional[List[bool]] = None,
    ) -> None:
        await self.s.execute(
            delete(VocabPackItem).where(VocabPackItem.pack_id == pack_id)
        )
        for idx, lid in enumerate(lexeme_ids):
            sid = sense_ids[idx] if sense_ids and idx < len(sense_ids) else None
            labels = stat_labels[idx] if stat_labels and idx < len(stat_labels) else []
            is_core = (
                is_core_flags[idx]
                if is_core_flags and idx < len(is_core_flags)
                else True
            )
            self.s.add(
                VocabPackItem(
                    pack_id=pack_id,
                    lexeme_id=lid,
                    sense_id=sid,
                    order_index=idx,
                    is_core=is_core,
                    stat_labels=labels or [],
                )
            )
        await self.s.flush()

    async def upsert_legacy_map(
        self, legacy_word_id: str, lexeme_id: uuid.UUID, pack_id: Optional[str] = None
    ) -> None:
        stmt = select(VocabLegacyWordMap).where(
            VocabLegacyWordMap.legacy_word_id == legacy_word_id
        )
        row = (await self.s.execute(stmt)).scalar_one_or_none()
        if row:
            row.lexeme_id = lexeme_id
            row.pack_id = pack_id
        else:
            self.s.add(
                VocabLegacyWordMap(
                    legacy_word_id=legacy_word_id,
                    lexeme_id=lexeme_id,
                    pack_id=pack_id,
                )
            )
        await self.s.flush()

    async def clear_all_vocab_content(self) -> None:
        """Wipe lexicon tables (dev seed reset)."""

        for model in (
            VocabReviewQueue,
            VocabQuestion,
            VocabPackItem,
            VocabLegacyWordMap,
            VocabCollocation,
            VocabWordForm,
            VocabSense,
            VocabPack,
            VocabLexeme,
        ):
            await self.s.execute(delete(model))
