"""Persistence for the 31-day adaptive vocab coaching plan.

Owns the plans/days/events tables and the DB-first vocabulary reads (lexeme mix
by CEFR, over-band token detection, and rich DB dictionary entries) used by the
coaching service.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.domain.sql_models import (
    ReadingCoachNoteCache,
    VocabCoachingDay,
    VocabCoachingEvent,
    VocabCoachingPlan,
    VocabLexeme,
    VocabQuestion,
    VocabSense,
)

_USABLE_LEXEME_STATUS = ("enriched", "approved")
_USABLE_QUESTION_STATUS = ("validated", "approved", "generated")


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class VocabCoachingRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    # ------------------------------------------------------------------ plans
    async def get_active_plan(self, user_id: str) -> Optional[VocabCoachingPlan]:
        return (
            await self.s.execute(
                select(VocabCoachingPlan).where(
                    VocabCoachingPlan.user_id == _uuid(user_id),
                    VocabCoachingPlan.status == "active",
                )
            )
        ).scalar_one_or_none()

    async def archive_active_plans(self, user_id: str) -> int:
        plans = (
            (
                await self.s.execute(
                    select(VocabCoachingPlan).where(
                        VocabCoachingPlan.user_id == _uuid(user_id),
                        VocabCoachingPlan.status == "active",
                    )
                )
            )
            .scalars()
            .all()
        )
        for plan in plans:
            plan.status = "archived"
        await self.s.flush()
        return len(plans)

    async def create_plan(
        self,
        *,
        user_id: str,
        cefr_level: str,
        estimated_band: Optional[float],
        confidence: Optional[float],
        source: str,
        start_date: date,
        total_days: int,
        meta: Dict[str, Any],
    ) -> VocabCoachingPlan:
        plan = VocabCoachingPlan(
            user_id=_uuid(user_id),
            status="active",
            cefr_level=cefr_level,
            estimated_band=estimated_band,
            confidence=confidence,
            source=source,
            start_date=start_date,
            current_day=1,
            total_days=total_days,
            meta=meta or {},
        )
        self.s.add(plan)
        await self.s.flush()
        return plan

    # ------------------------------------------------------------------- days
    async def create_days(
        self, *, plan: VocabCoachingPlan, days: List[Dict[str, Any]]
    ) -> List[VocabCoachingDay]:
        rows: List[VocabCoachingDay] = []
        for day in days:
            row = VocabCoachingDay(
                plan_id=plan.id,
                user_id=plan.user_id,
                day_number=int(day["day_number"]),
                status=str(day.get("status") or "locked"),
                title=day.get("title"),
                focus_skill=day.get("focus_skill"),
                words=day.get("words") or [],
                reading=day.get("reading") or {},
                sessions=day.get("sessions") or {},
                analysis=day.get("analysis") or {},
                notes=day.get("notes") or [],
            )
            self.s.add(row)
            rows.append(row)
        await self.s.flush()
        return rows

    async def list_days(self, plan_id: str | uuid.UUID) -> List[VocabCoachingDay]:
        return list(
            (
                await self.s.execute(
                    select(VocabCoachingDay)
                    .where(VocabCoachingDay.plan_id == _uuid(plan_id))
                    .order_by(VocabCoachingDay.day_number)
                )
            )
            .scalars()
            .all()
        )

    async def get_day(
        self, *, plan_id: str | uuid.UUID, day_number: int
    ) -> Optional[VocabCoachingDay]:
        return (
            await self.s.execute(
                select(VocabCoachingDay).where(
                    VocabCoachingDay.plan_id == _uuid(plan_id),
                    VocabCoachingDay.day_number == day_number,
                )
            )
        ).scalar_one_or_none()

    # ----------------------------------------------------------------- events
    async def record_events(
        self,
        *,
        plan: VocabCoachingPlan,
        day: Optional[VocabCoachingDay],
        day_number: int,
        events: Sequence[Dict[str, Any]],
    ) -> int:
        count = 0
        for event in events:
            event_type = str(event.get("event_type") or "").strip()
            if not event_type:
                continue
            payload = dict(event.get("payload") or {})
            for key in (
                "event_id",
                "occurred_at",
                "paragraph_index",
                "visible_paragraph_indexes",
                "target",
                "context",
                "result",
            ):
                if event.get(key) is not None:
                    payload[key] = event.get(key)
            target = (
                event.get("target") if isinstance(event.get("target"), dict) else {}
            )
            result = (
                event.get("result") if isinstance(event.get("result"), dict) else {}
            )
            word = event.get("word") or target.get("word")
            phrase = event.get("phrase") or target.get("phrase") or target.get("text")
            sentence = event.get("sentence") or target.get("sentence")
            is_correct = event.get("is_correct")
            if is_correct is None and result.get("is_correct") is not None:
                is_correct = result.get("is_correct")
            self.s.add(
                VocabCoachingEvent(
                    plan_id=plan.id,
                    day_id=day.id if day else None,
                    user_id=plan.user_id,
                    day_number=day_number,
                    event_type=event_type[:32],
                    word=(str(word).strip()[:128] if word else None),
                    phrase=(str(phrase) if phrase else None),
                    sentence=(str(sentence) if sentence else None),
                    is_correct=is_correct,
                    payload=payload,
                )
            )
            count += 1
        await self.s.flush()
        return count

    async def list_events(
        self,
        *,
        plan_id: str | uuid.UUID,
        day_number: int,
        event_types: Optional[Sequence[str]] = None,
    ) -> List[VocabCoachingEvent]:
        stmt = select(VocabCoachingEvent).where(
            VocabCoachingEvent.plan_id == _uuid(plan_id),
            VocabCoachingEvent.day_number == day_number,
        )
        if event_types:
            stmt = stmt.where(VocabCoachingEvent.event_type.in_(tuple(event_types)))
        stmt = stmt.order_by(VocabCoachingEvent.created_at)
        return list((await self.s.execute(stmt)).scalars().all())

    # --------------------------------------------------------------- lexicon
    async def lexemes_by_level(
        self,
        *,
        cefr_levels: Sequence[str],
        limit: int,
        exclude_lemmas: Optional[Sequence[str]] = None,
        require_quiz: bool = True,
    ) -> List[Dict[str, Any]]:
        if not cefr_levels:
            return []
        stmt = select(VocabLexeme).where(
            func.upper(VocabLexeme.cefr_level).in_(
                tuple(level.upper() for level in cefr_levels)
            ),
            VocabLexeme.status.in_(_USABLE_LEXEME_STATUS),
        )
        if require_quiz:
            stmt = stmt.where(
                exists(
                    select(1).where(
                        VocabQuestion.lexeme_id == VocabLexeme.id,
                        VocabQuestion.status.in_(_USABLE_QUESTION_STATUS),
                    )
                )
            )
        stmt = stmt.order_by(
            VocabLexeme.frequency_rank.asc().nullslast(), VocabLexeme.lemma
        ).limit(max(limit * 8, 80))
        rows = list((await self.s.execute(stmt)).scalars().all())
        excluded = {str(item).lower() for item in (exclude_lemmas or [])}
        out: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for lexeme in rows:
            lemma = (lexeme.lemma or "").lower()
            if not lemma or lemma in excluded or lemma in seen:
                continue
            seen.add(lemma)
            out.append(await self._lexeme_brief(lexeme))
            if len(out) >= limit:
                break
        return out

    async def lexemes_for_lemmas(
        self,
        lemmas: Sequence[str],
        *,
        limit: int = 40,
        require_quiz: bool = True,
    ) -> List[Dict[str, Any]]:
        """Return quiz-ready lexeme briefs in the same rough order as input lemmas."""
        unique: List[str] = []
        seen: set[str] = set()
        for lemma in lemmas:
            clean = str(lemma or "").strip().lower()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            unique.append(clean)
            if len(unique) >= max(limit * 2, limit):
                break
        if not unique:
            return []

        stmt = select(VocabLexeme).where(
            VocabLexeme.status.in_(_USABLE_LEXEME_STATUS),
            (
                func.lower(VocabLexeme.lemma).in_(tuple(unique))
                | func.lower(VocabLexeme.display_word).in_(tuple(unique))
            ),
        )
        if require_quiz:
            stmt = stmt.where(
                exists(
                    select(1).where(
                        VocabQuestion.lexeme_id == VocabLexeme.id,
                        VocabQuestion.status.in_(_USABLE_QUESTION_STATUS),
                    )
                )
            )
        rows = list((await self.s.execute(stmt)).scalars().all())
        by_key: Dict[str, VocabLexeme] = {}
        for lexeme in rows:
            for key in (lexeme.lemma, lexeme.display_word):
                clean = str(key or "").strip().lower()
                if clean and clean not in by_key:
                    by_key[clean] = lexeme

        out: List[Dict[str, Any]] = []
        emitted: set[uuid.UUID] = set()
        for lemma in unique:
            lexeme = by_key.get(lemma)
            if lexeme is None or lexeme.id in emitted:
                continue
            emitted.add(lexeme.id)
            out.append(await self._lexeme_brief(lexeme))
            if len(out) >= limit:
                break
        return out

    async def _lexeme_brief(self, lexeme: VocabLexeme) -> Dict[str, Any]:
        sense = (
            await self.s.execute(
                select(VocabSense)
                .where(VocabSense.lexeme_id == lexeme.id)
                .order_by(VocabSense.sense_order)
                .limit(1)
            )
        ).scalar_one_or_none()
        return {
            "id": str(lexeme.id),
            "word": lexeme.display_word,
            "lemma": lexeme.lemma,
            "pos": lexeme.pos,
            "cefr": (lexeme.cefr_level or "").upper() or None,
            "ielts_band_min": (
                float(lexeme.ielts_band_min)
                if lexeme.ielts_band_min is not None
                else None
            ),
            "definition": sense.definition_en if sense else "",
            "vi_gloss": sense.vi_gloss if sense else None,
            "example": (sense.ielts_example if sense else None) or "",
            "phonetic": sense.phonetic if sense else None,
        }

    async def detect_levels_for_tokens(
        self, tokens: Sequence[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Map lowercase lemma -> {cefr, ielts_band_min} for known lexemes."""
        if not tokens:
            return {}
        unique = list({str(token).lower() for token in tokens if token})
        rows = list(
            (
                await self.s.execute(
                    select(VocabLexeme).where(
                        func.lower(VocabLexeme.lemma).in_(tuple(unique))
                    )
                )
            )
            .scalars()
            .all()
        )
        out: Dict[str, Dict[str, Any]] = {}
        for lexeme in rows:
            out[(lexeme.lemma or "").lower()] = {
                "cefr": (lexeme.cefr_level or "").upper() or None,
                "ielts_band_min": (
                    float(lexeme.ielts_band_min)
                    if lexeme.ielts_band_min is not None
                    else None
                ),
            }
        return out

    async def lookup_dictionary(self, lemma: str) -> Optional[Dict[str, Any]]:
        """DB-first rich dictionary entry. None if the word is not in our DB."""
        cleaned = (lemma or "").strip().lower()
        if not cleaned:
            return None
        lexemes = list(
            (
                await self.s.execute(
                    select(VocabLexeme)
                    .where(func.lower(VocabLexeme.lemma) == cleaned)
                    .order_by(VocabLexeme.frequency_rank.asc().nullslast())
                )
            )
            .scalars()
            .all()
        )
        if not lexemes:
            return None

        entries: List[Dict[str, Any]] = []
        vi_gloss: Optional[str] = None
        cefr: Optional[str] = None
        band: Optional[float] = None
        for lexeme in lexemes:
            senses = list(
                (
                    await self.s.execute(
                        select(VocabSense)
                        .where(VocabSense.lexeme_id == lexeme.id)
                        .order_by(VocabSense.sense_order)
                    )
                )
                .scalars()
                .all()
            )
            definitions: List[Dict[str, Any]] = []
            phonetic: Optional[str] = None
            audio: Optional[str] = None
            for sense in senses:
                phonetic = phonetic or sense.phonetic
                audio = audio or sense.audio_url
                vi_gloss = vi_gloss or sense.vi_gloss
                definitions.append(
                    {
                        "definition": sense.definition_en or "",
                        "example": sense.ielts_example or "",
                        "synonyms": list(sense.synonyms or [])[:6],
                    }
                )
            if not definitions:
                continue
            cefr = cefr or ((lexeme.cefr_level or "").upper() or None)
            if band is None and lexeme.ielts_band_min is not None:
                band = float(lexeme.ielts_band_min)
            entries.append(
                {
                    "word": lexeme.display_word,
                    "phonetic": phonetic,
                    "audio": audio,
                    "source": "primevocab-db",
                    "meanings": [
                        {
                            "part_of_speech": lexeme.pos or "",
                            "definitions": definitions,
                            "synonyms": [],
                            "antonyms": [],
                        }
                    ],
                }
            )
        if not entries:
            return None
        return {
            "word": cleaned,
            "entries": entries,
            "source": "db",
            "vi_gloss": vi_gloss,
            "cefr_level": cefr,
            "ielts_band": band,
            "cambridge_link": f"https://dictionary.cambridge.org/dictionary/english/{cleaned}",
            "dictionary_link": f"https://www.merriam-webster.com/dictionary/{cleaned}",
        }

    # -------------------------------------------------------- reading coach cache
    async def fetch_reading_coach_cache_and_hit(
        self, cache_key: str
    ) -> Optional[Dict[str, Any]]:
        """Return cached card JSON and increment hit_count atomically."""
        result = await self.s.execute(
            update(ReadingCoachNoteCache)
            .where(ReadingCoachNoteCache.cache_key == cache_key)
            .values(
                hit_count=ReadingCoachNoteCache.hit_count + 1,
                updated_at=datetime.now(timezone.utc),
            )
            .returning(ReadingCoachNoteCache.card_json)
        )
        row = result.first()
        if not row:
            return None
        card_json = row[0]
        return dict(card_json) if isinstance(card_json, dict) else None

    async def upsert_reading_coach_cache(
        self,
        *,
        cache_key: str,
        reading_id: str,
        selection_type: str,
        target_text: str,
        sentence_text: str,
        locale: str,
        user_level: str,
        prompt_version: str,
        model_name: str,
        card_json: Dict[str, Any],
    ) -> None:
        now = datetime.now(timezone.utc)
        values = {
            "cache_key": cache_key,
            "reading_id": reading_id,
            "selection_type": selection_type,
            "target_text": target_text,
            "sentence_text": sentence_text,
            "locale": locale,
            "user_level": user_level,
            "prompt_version": prompt_version,
            "model_name": model_name,
            "card_json": card_json,
            "hit_count": 0,
            "created_at": now,
            "updated_at": now,
        }
        stmt = pg_insert(ReadingCoachNoteCache).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[ReadingCoachNoteCache.cache_key],
            set_={
                "card_json": card_json,
                "model_name": model_name,
                "prompt_version": prompt_version,
                "updated_at": now,
            },
        )
        await self.s.execute(stmt)
        await self.s.flush()
