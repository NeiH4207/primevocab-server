"""Adaptive 30-day vocab coaching orchestration.

Quick check runs once (persisted CEFR estimate). After that the learner gets the
three daily sessions (memory recall, reading challenge, coaching notes). The
deterministic engine picks the word mix and segments the Cambridge reading; the
LLM only writes coaching copy, explains phrases, and generates questions from the
learner's captured actions.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

from loguru import logger

from aiforen.core.config import get_settings
from aiforen.domain.coaching_content import (
    grade_reading_answer,
    passage_tokens_from_paragraphs,
    placeholder_reading,
    should_refresh_reading_snapshot,
    unit_to_reading_payload,
)
from aiforen.domain.coaching_reading_titles import static_reading_titles
from aiforen.domain.reading_coach_cache import (
    READING_COACH_PROMPT_VERSION,
    cache_key_from_selection,
    is_cacheable_reading_coach_card,
)
from aiforen.domain.sql_models import VocabCoachingDay, VocabCoachingPlan
from aiforen.domain.vocab_coaching_reading import (
    CURATED_DIFFICULT_WORDS,
    find_sentence,
    normalize_token,
)
from aiforen.integrations.llm import get_llm_provider
from aiforen.integrations.llm.json_utils import (
    align_reading_coach_card_to_selection,
    build_reading_helper_note_messages,
    build_reading_helper_prompt,
    extract_json,
    mock_reading_helper_note,
    mock_reading_helper_text,
    normalize_coaching_notes_payload,
    normalize_reading_explain_payload,
    normalize_reading_helper_note_payload,
    normalize_reading_questions_payload,
)
from aiforen.integrations.llm.openai_chat import (
    openai_chat_completion_stream,
    openai_chat_completion_text,
)
from aiforen.integrations.translate import get_translate_client
from aiforen.repositories.pg.coaching_content import CoachingContentRepo
from aiforen.repositories.pg.user_stats import VN_TZ, UserStatsRepo
from aiforen.repositories.pg.vocab_coaching import VocabCoachingRepo
from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo

CEFR_LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]
TOTAL_DAYS = 30
COACHING_C2_RELEASE_DAYS = 30

_IELTS_BAND = {
    "A1": 3.5,
    "A2": 4.5,
    "B1": 5.5,
    "B2": 6.5,
    "C1": 7.5,
    "C2": 8.5,
}
_IELTS_RANGE = {
    "A1": "IELTS vocabulary ~3.0–4.0",
    "A2": "IELTS vocabulary ~4.0–4.5",
    "B1": "IELTS vocabulary ~5.0–5.5",
    "B2": "IELTS vocabulary ~6.0–6.5",
    "C1": "IELTS vocabulary ~7.0–7.5",
    "C2": "IELTS vocabulary ~8.0+",
}

# Oxford pack ids align quiz matrix track_id (cefr:*) with vocab learning.
_CEFR_PACK_ID: Dict[str, str] = {
    "A1": "pack_oxford_a1",
    "A2": "pack_oxford_a2",
    "B1": "pack_oxford_b1",
    "B2": "pack_oxford_b2",
    "C1": "pack_oxford_c1",
    "C2": "pack_oxford_c1",
}
_COACHING_PACK_FALLBACK = (
    "pack_band_6",
    "pack_band_7",
    "pack_band_8",
    "pack_gre",
)

FOCUS_PLAN_MIN_WORDS = 12
FOCUS_PLAN_MAX_WORDS = 20
FOCUS_PLAN_SEED_LIMIT = 30
FOCUS_SOURCE_FORGOTTEN = "Forgotten"
FOCUS_SOURCE_INTERACTION = "Clicked"
FOCUS_SOURCE_READING = "Reading keyword"
FOCUS_SOURCE_FALLBACK = "Fallback"
FOCUS_SOURCE_ORDER = (
    FOCUS_SOURCE_FORGOTTEN,
    FOCUS_SOURCE_INTERACTION,
    FOCUS_SOURCE_READING,
    FOCUS_SOURCE_FALLBACK,
)
FOCUS_SIGNAL_SCORES = {
    "lookup": 55,
    "word_lookup": 55,
    "explain": 45,
    "explain_request": 45,
    "explain_result": 45,
    "translate": 38,
    "highlight": 32,
    "highlight_add": 32,
    "word_click": 24,
    "reading_wrong": 42,
    "reading_seed": 12,
    "forgotten": 100,
}


def _cefr_index(level: Optional[str]) -> int:
    try:
        return CEFR_LEVELS.index((level or "B1").upper())
    except ValueError:
        return CEFR_LEVELS.index("B1")


def _cefr_offset(level: str, delta: int) -> str:
    idx = max(0, min(len(CEFR_LEVELS) - 1, _cefr_index(level) + delta))
    return CEFR_LEVELS[idx]


def _ielts_band(level: str) -> float:
    return _IELTS_BAND.get((level or "B1").upper(), 5.5)


def _ielts_range(level: str) -> str:
    return _IELTS_RANGE.get((level or "B1").upper(), "IELTS vocabulary ~5.0–5.5")


_LEGACY_GENERIC_DAY_TITLE_MARKERS = (
    "Establish your vocabulary rhythm",
    "Adaptive focus",
)


def _is_legacy_generic_day_title(title: Optional[str]) -> bool:
    text = (title or "").strip()
    if not text:
        return True
    return any(marker in text for marker in _LEGACY_GENERIC_DAY_TITLE_MARKERS)


def _merged_catalog_titles(
    cefr_level: str,
    db_titles: Optional[Dict[int, str]] = None,
) -> Dict[int, str]:
    """DB catalog wins; static embedded titles fill gaps (prod has no vocab_storage mount)."""
    merged = dict(static_reading_titles(cefr_level))
    if db_titles:
        merged.update({int(k): str(v) for k, v in db_titles.items() if v})
    return merged


def _resolve_day_reading_title(
    cefr_level: str,
    day_number: int,
    *,
    catalog_titles: Dict[int, str],
    reading: Optional[Dict[str, Any]] = None,
) -> str:
    reading_title = str((reading or {}).get("title") or "").strip()
    if (
        reading_title
        and reading_title != "Reading content coming soon"
        and not _is_legacy_generic_day_title(reading_title)
    ):
        return reading_title
    effective_catalog = _merged_catalog_titles(cefr_level, catalog_titles)
    catalog_title = effective_catalog.get(day_number)
    if catalog_title:
        return catalog_title.strip()
    return (
        placeholder_reading(cefr_level, day_number).get("title") or f"Day {day_number}"
    )


def _coaching_word_key(item: Dict[str, Any]) -> str:
    return normalize_token(str(item.get("lemma") or item.get("word") or ""))


def _text_candidate_tokens(value: Any) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for raw in str(value or "").replace("’", "'").split():
        token = normalize_token(raw)
        if len(token) < 3 or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _coaching_word_has_quiz(item: Dict[str, Any]) -> bool:
    return bool(item.get("quiz_steps"))


def _difficulty_rank(item: Dict[str, Any]) -> float:
    cefr_rank = _cefr_index(str(item.get("cefr") or "B1")) * 10
    try:
        band = float(item.get("ielts_band_min") or 0)
    except (TypeError, ValueError):
        band = 0.0
    return round(cefr_rank + band, 2)


def _role_for_level(word_cefr: Optional[str], plan_cefr: str) -> str:
    delta = _cefr_index(word_cefr) - _cefr_index(plan_cefr)
    if delta < 0:
        return "lower"
    if delta > 0:
        return "stretch"
    return "current"


def _coaching_vocab_min_index(plan_cefr: str) -> int:
    """Focus/reading pool may include one CEFR band below the learner plan."""
    return max(0, _cefr_index(plan_cefr) - 1)


def _coaching_word_meets_plan_level(word_cefr: Optional[str], plan_cefr: str) -> bool:
    if not word_cefr:
        return True
    try:
        return _cefr_index(word_cefr) >= _coaching_vocab_min_index(plan_cefr)
    except ValueError:
        return True


def _reading_vocab_candidates_stale(candidates: Sequence[Any], plan_cefr: str) -> bool:
    min_idx = _coaching_vocab_min_index(plan_cefr)
    for item in candidates:
        if not isinstance(item, dict):
            continue
        cefr = item.get("cefr")
        if cefr and _cefr_index(str(cefr)) < min_idx:
            return True
    return False


def _confidence_pct(value: Any) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 70.0
    if num <= 1:
        num *= 100
    return round(max(0.0, min(100.0, num)), 1)


class VocabCoachingService:
    def __init__(self, session):
        self.s = session
        self.repo = VocabCoachingRepo(session)
        self.content_repo = CoachingContentRepo(session)
        self.stats = UserStatsRepo(session)
        self.lexicon = VocabLexiconRepo(session)

    # ============================================================ plan / view
    async def get_plan(self, *, user_id: str, locale: str = "en") -> Dict[str, Any]:
        plan = await self.repo.get_active_plan(user_id)
        if plan is None:
            stats = await self.stats.get_or_default(user_id)
            profile = stats.get("vocab_profile") or {}
            if not profile.get("calibration_completed"):
                return {"needs_quick_check": True, "plan": None, "timeline": []}
            plan = await self._create_plan_from_calibration(user_id, profile)
        return await self._plan_view(plan)

    async def create_plan(self, *, user_id: str, locale: str = "en") -> Dict[str, Any]:
        plan = await self.repo.get_active_plan(user_id)
        if plan is not None:
            return await self._plan_view(plan)
        stats = await self.stats.get_or_default(user_id)
        profile = stats.get("vocab_profile") or {}
        if not profile.get("calibration_completed"):
            return {"needs_quick_check": True, "plan": None, "timeline": []}
        plan = await self._create_plan_from_calibration(user_id, profile)
        return await self._plan_view(plan)

    async def _create_plan_from_calibration(
        self, user_id: str, profile: Dict[str, Any]
    ) -> VocabCoachingPlan:
        cefr = str(profile.get("calibration_cefr_level") or "B1").upper()
        if cefr not in CEFR_LEVELS:
            cefr = "B1"
        if cefr == "C2":
            published = await self.content_repo.count_published_units("C2")
            if published < COACHING_C2_RELEASE_DAYS:
                cefr = "C1"
        insight = profile.get("calibration_insight") or {}
        confidence = _confidence_pct(insight.get("confidence"))
        band = profile.get("current_band")
        try:
            band = float(band) if band is not None else _ielts_band(cefr)
        except (TypeError, ValueError):
            band = _ielts_band(cefr)

        await self.repo.archive_active_plans(user_id)
        plan = await self.repo.create_plan(
            user_id=user_id,
            cefr_level=cefr,
            estimated_band=band,
            confidence=confidence,
            source=str(profile.get("level_source") or "calibration"),
            start_date=datetime.now(VN_TZ).date(),
            total_days=TOTAL_DAYS,
            meta={
                "ielts_range": _ielts_range(cefr),
                "mix": {"current": 10, "lower": 2, "stretch": 3},
            },
        )
        catalog_titles = _merged_catalog_titles(
            cefr, await self.content_repo.list_published_unit_titles(cefr)
        )
        day_rows = []
        for number in range(1, TOTAL_DAYS + 1):
            day_rows.append(
                {
                    "day_number": number,
                    "status": "ready" if number == 1 else "locked",
                    "title": _resolve_day_reading_title(
                        cefr,
                        number,
                        catalog_titles=catalog_titles,
                    ),
                    "focus_skill": "foundation" if number == 1 else "adaptive",
                    "analysis": (
                        {}
                        if number == 1
                        else {"preview": self._locked_preview(number, cefr)}
                    ),
                }
            )
        await self.repo.create_days(plan=plan, days=day_rows)
        day_one = await self.repo.get_day(plan_id=plan.id, day_number=1)
        if day_one is not None:
            await self._ensure_day_content(plan, day_one)
        for number in range(2, TOTAL_DAYS + 1):
            day = await self.repo.get_day(plan_id=plan.id, day_number=number)
            if day is not None:
                await self._sync_day_title(plan, day, catalog_titles=catalog_titles)
        return plan

    async def _sync_day_title(
        self,
        plan: VocabCoachingPlan,
        day: VocabCoachingDay,
        *,
        catalog_titles: Optional[Dict[int, str]] = None,
    ) -> bool:
        titles = catalog_titles
        if titles is None:
            titles = _merged_catalog_titles(
                plan.cefr_level,
                await self.content_repo.list_published_unit_titles(plan.cefr_level),
            )
        else:
            titles = _merged_catalog_titles(plan.cefr_level, titles)
        resolved = _resolve_day_reading_title(
            plan.cefr_level,
            day.day_number,
            catalog_titles=titles,
            reading=day.reading if isinstance(day.reading, dict) else None,
        )
        reading = day.reading if isinstance(day.reading, dict) else {}
        stored_reading_title = str(reading.get("title") or "").strip()
        stale_reading = not stored_reading_title or _is_legacy_generic_day_title(
            stored_reading_title
        )
        stale_day = _is_legacy_generic_day_title(day.title)
        if day.title == resolved and not stale_reading and not stale_day:
            return False
        if stale_day or stale_reading or day.title != resolved:
            day.title = resolved
            if stored_reading_title != resolved:
                reading = dict(reading)
                reading["title"] = resolved
                day.reading = reading
            await self.s.flush()
            return True
        return False

    async def _sync_plan_day_titles(
        self, plan: VocabCoachingPlan, days: Sequence[VocabCoachingDay]
    ) -> None:
        catalog_titles = _merged_catalog_titles(
            plan.cefr_level,
            await self.content_repo.list_published_unit_titles(plan.cefr_level),
        )
        for day in days:
            await self._sync_day_title(plan, day, catalog_titles=catalog_titles)

    def _locked_preview(self, number: int, cefr: str) -> str:
        return (
            f"Day {number} unlocks after Day {number - 1}. The coach will analyze your "
            f"Day {number - 1} results — the words you looked up, your reading score and "
            f"weak spots — and design Day {number}'s {cefr} word mix and reading focus "
            "around them."
        )

    async def _plan_view(self, plan: VocabCoachingPlan) -> Dict[str, Any]:
        days = await self.repo.list_days(plan.id)
        await self._sync_plan_day_titles(plan, days)
        catalog_titles = _merged_catalog_titles(
            plan.cefr_level,
            await self.content_repo.list_published_unit_titles(plan.cefr_level),
        )
        timeline = []
        for day in days:
            unlocked = day.day_number <= plan.current_day
            display_title = _resolve_day_reading_title(
                plan.cefr_level,
                day.day_number,
                catalog_titles=catalog_titles,
                reading=day.reading if isinstance(day.reading, dict) else None,
            )
            timeline.append(
                {
                    "day_number": day.day_number,
                    "status": day.status,
                    "title": display_title,
                    "reading_title": display_title,
                    "focus_skill": day.focus_skill,
                    "unlocked": unlocked,
                    "is_today": day.day_number == plan.current_day,
                    "completed": day.status == "completed",
                    "preview": (day.analysis or {}).get("preview"),
                    "word_count": len(day.words or []),
                }
            )
        return {
            "needs_quick_check": False,
            "plan": {
                "id": str(plan.id),
                "status": plan.status,
                "cefr_level": plan.cefr_level,
                "estimated_band": (
                    float(plan.estimated_band)
                    if plan.estimated_band is not None
                    else None
                ),
                "confidence": (
                    float(plan.confidence) if plan.confidence is not None else None
                ),
                "source": plan.source,
                "start_date": plan.start_date.isoformat(),
                "current_day": plan.current_day,
                "total_days": plan.total_days,
                "ielts_range": (plan.meta or {}).get("ielts_range")
                or _ielts_range(plan.cefr_level),
                "mix": (plan.meta or {}).get("mix")
                or {"current": 10, "lower": 2, "stretch": 3},
            },
            "timeline": timeline,
        }

    # ============================================================ day content
    async def _ensure_day_content(
        self, plan: VocabCoachingPlan, day: VocabCoachingDay
    ) -> None:
        mutated = False
        if not day.words:
            words = await self._build_word_mix(plan.cefr_level)
            day.words = await self._enrich_coaching_words(
                words, plan_cefr=plan.cefr_level
            )
            mutated = True

        reading = dict(day.reading or {})
        sessions = dict(day.sessions or {})
        workspace = (
            dict(sessions.get("workspace") or {})
            if isinstance(sessions.get("workspace"), dict)
            else {}
        )
        reading_answers = (
            workspace.get("reading_answers")
            if isinstance(workspace.get("reading_answers"), dict)
            else {}
        )
        reading_status = (sessions.get("reading") or {}).get("status")

        unit = await self.content_repo.get_published_unit(
            plan.cefr_level, day.day_number
        )
        if unit is not None:
            paragraphs = list(unit.paragraphs or [])
            difficult = await self._detect_difficult_words(
                plan.cefr_level, paragraphs=paragraphs
            )
            if not reading or should_refresh_reading_snapshot(
                reading,
                unit,
                reading_answers=reading_answers,
                reading_status=reading_status,
            ):
                reading = unit_to_reading_payload(unit, difficult)
                mutated = True
        elif not reading or (
            not reading.get("placeholder")
            and not reading_answers
            and reading_status != "completed"
        ):
            reading = placeholder_reading(plan.cefr_level, day.day_number)
            mutated = True

        unit_seeds = list(unit.vocab_keywords or []) if unit is not None else []
        stored_seeds = reading.get("vocab_keyword_seeds") or []
        if unit is not None and stored_seeds != unit_seeds:
            reading["vocab_keyword_seeds"] = unit_seeds
            mutated = True

        candidates = reading.get("vocab_candidates") or []
        if (
            not candidates
            or _reading_vocab_candidates_stale(candidates, plan.cefr_level)
            or (unit is not None and stored_seeds != unit_seeds)
        ):
            reading["vocab_candidates"] = await self._build_reading_vocab_candidates(
                plan.cefr_level,
                keyword_seeds=reading.get("vocab_keyword_seeds") or [],
            )
            mutated = True

        if mutated:
            day.reading = reading

        reading_title = str((reading or {}).get("title") or "").strip()
        if (
            reading_title
            and not _is_legacy_generic_day_title(reading_title)
            and reading_title != "Reading content coming soon"
            and day.title != reading_title
        ):
            day.title = reading_title
            mutated = True

        if not day.sessions:
            day.sessions = {
                "recall": {"status": "pending"},
                "reading": {"status": "pending"},
                "focus": {"status": "pending"},
                "notes": {"status": "pending"},
            }
            mutated = True

        if mutated:
            await self.s.flush()

    async def _build_word_mix(self, cefr: str) -> List[Dict[str, Any]]:
        current_level = cefr.upper()
        lower_level = _cefr_offset(current_level, -1)
        stretch_level = _cefr_offset(current_level, 1)

        current = await self._lexemes_for_level(current_level, 10, [])
        used = [w["word"].lower() for w in current]
        lower = await self._lexemes_for_level(lower_level, 2, used)
        used += [w["word"].lower() for w in lower]
        stretch = await self._lexemes_for_level(stretch_level, 3, used)

        out: List[Dict[str, Any]] = []
        for role, group in (
            ("current", current),
            ("lower", lower),
            ("stretch", stretch),
        ):
            for item in group:
                out.append({**item, "role": role})
        return out

    async def _build_reading_vocab_candidates(
        self,
        cefr: str,
        *,
        keyword_seeds: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Resolve curator-picked keywords from DB; skip lemmas that are missing or not quiz-ready."""
        if not keyword_seeds:
            return []

        out: List[Dict[str, Any]] = []
        seen: set[str] = set()

        for seed in keyword_seeds:
            if not isinstance(seed, dict):
                continue
            lemma = normalize_token(str(seed.get("lemma") or seed.get("word") or ""))
            if not lemma or lemma in seen:
                continue
            seen.add(lemma)

            briefs = await self.repo.lexemes_for_lemmas(
                [lemma], limit=1, require_quiz=True
            )
            if not briefs:
                logger.debug("coaching vocab seed skipped (not in DB): {}", lemma)
                continue

            brief = dict(briefs[0])
            if seed.get("vi_gloss"):
                brief["vi_gloss"] = seed["vi_gloss"]
            if seed.get("pos"):
                brief["pos"] = seed["pos"]

            enriched = await self._enrich_coaching_word(
                {
                    **brief,
                    "role": _role_for_level(brief.get("cefr"), cefr),
                }
            )
            key = _coaching_word_key(enriched)
            if not key or not _coaching_word_has_quiz(enriched):
                logger.debug("coaching vocab seed skipped (no quiz): {}", lemma)
                continue
            out.append(enriched)

        return out[:FOCUS_PLAN_SEED_LIMIT]

    async def _lexemes_for_level(
        self,
        level: str,
        count: int,
        exclude: Sequence[str],
        *,
        strict: bool = True,
    ) -> List[Dict[str, Any]]:
        level = (level or "B1").upper()
        rows = await self.repo.lexemes_by_level(
            cefr_levels=[level], limit=count, exclude_lemmas=exclude, require_quiz=True
        )
        if len(rows) >= count:
            return rows[:count]
        widen_levels = [level]
        idx = _cefr_index(level)
        if idx > 0:
            widen_levels.append(CEFR_LEVELS[idx - 1])
        if idx < len(CEFR_LEVELS) - 1:
            widen_levels.append(CEFR_LEVELS[idx + 1])
        rows = await self.repo.lexemes_by_level(
            cefr_levels=widen_levels,
            limit=count,
            exclude_lemmas=exclude,
            require_quiz=True,
        )
        if len(rows) < count and strict:
            logger.error(
                "coaching vocab pool short for {}: need {}, found {}",
                level,
                count,
                len(rows),
            )
            raise ValueError("coaching_vocab_pool_unavailable")
        return rows[:count]

    def _coaching_pack_candidates(self, level: str) -> List[str]:
        level = (level or "B1").upper()
        packs: List[str] = []
        primary = _CEFR_PACK_ID.get(level)
        if primary:
            packs.append(primary)
        for pack in _COACHING_PACK_FALLBACK:
            if pack not in packs:
                packs.append(pack)
        return packs

    @staticmethod
    def _usable_quiz_steps(steps: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for step in steps:
            kind = str(step.get("interaction_kind") or "mcq").lower()
            if step.get("mcq"):
                out.append(dict(step))
            elif kind in ("rewrite", "free_text", "reorder", "cloze"):
                out.append(dict(step))
        return out

    async def _quiz_steps_for_lexeme(
        self, lexeme: Any, level: str
    ) -> tuple[List[Dict[str, Any]], Optional[str], Optional[Dict[str, Any]]]:
        for pack_id in self._coaching_pack_candidates(level):
            dto = self.lexicon.lexeme_to_word_dto(
                lexeme, pack_id=pack_id, mastery_step=0
            )
            if not dto:
                continue
            steps = self._usable_quiz_steps(dto.get("quiz_steps") or [])
            if steps:
                return steps, dto.get("quiz_track_id"), dto
        questions = self.lexicon._active_questions(lexeme)
        steps = self._usable_quiz_steps(
            [self.lexicon._question_to_quiz_step(q) for q in questions]
        )
        track_id = questions[0].track_id if questions else None
        dto = self.lexicon.lexeme_to_word_dto(
            lexeme, pack_id=_CEFR_PACK_ID.get(level, "pack_oxford_b1"), mastery_step=0
        )
        return steps, track_id, dto

    async def _enrich_coaching_words(
        self,
        words: Sequence[Dict[str, Any]],
        *,
        plan_cefr: str = "B1",
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        exclude: set[str] = set()
        target = max(len(words), 1)

        for item in words:
            enriched = await self._enrich_coaching_word(item)
            if enriched.get("quiz_steps"):
                out.append(enriched)
                exclude.add(str(enriched.get("word") or "").lower())
            else:
                logger.warning(
                    "coaching word has no quiz tasks, replacing: {}",
                    item.get("word"),
                )
                role = str(item.get("role") or "current")
                level = str(item.get("cefr") or plan_cefr).upper()
                replacement = await self._pick_replacement_word(level, role, exclude)
                if replacement:
                    out.append(replacement)
                    exclude.add(str(replacement.get("word") or "").lower())

        while len(out) < target:
            replacement = await self._pick_replacement_word(
                plan_cefr.upper(), "current", exclude
            )
            if not replacement:
                break
            out.append(replacement)
            exclude.add(str(replacement.get("word") or "").lower())

        return out

    async def _pick_replacement_word(
        self, level: str, role: str, exclude: set[str]
    ) -> Optional[Dict[str, Any]]:
        candidates = await self._lexemes_for_level(
            level, 12, list(exclude), strict=False
        )
        for brief in candidates:
            enriched = await self._enrich_coaching_word({**brief, "role": role})
            if enriched.get("quiz_steps"):
                return enriched
        return None

    async def _enrich_coaching_word(self, item: Dict[str, Any]) -> Dict[str, Any]:
        if item.get("quiz_steps"):
            steps = self._usable_quiz_steps(item.get("quiz_steps") or [])
            if steps:
                merged = dict(item)
                merged["quiz_steps"] = steps
                return merged
        lexeme_id = str(item.get("id") or "").strip()
        try:
            lid = uuid.UUID(lexeme_id)
        except ValueError:
            logger.warning("coaching word missing lexeme id: {}", item.get("word"))
            return {**item, "quiz_steps": []}
        lexeme = await self.lexicon.get_lexeme(lid)
        if lexeme is None:
            logger.warning("coaching lexeme not found: {}", item.get("word"))
            return {**item, "quiz_steps": []}
        level = str(item.get("cefr") or lexeme.cefr_level or "B1").upper()
        quiz_steps, track_id, dto = await self._quiz_steps_for_lexeme(lexeme, level)
        if not quiz_steps:
            return {**item, "quiz_steps": []}
        return {
            **item,
            "definition": (dto or {}).get("definition") or item.get("definition") or "",
            "vi_gloss": (
                (dto or {}).get("vi_gloss")
                if (dto or {}).get("vi_gloss") is not None
                else item.get("vi_gloss")
            ),
            "example": (dto or {}).get("example_good_sentence")
            or item.get("example")
            or "",
            "phonetic": (dto or {}).get("pronunciation") or item.get("phonetic"),
            "pos": (dto or {}).get("part_of_speech") or item.get("pos"),
            "quiz_steps": quiz_steps,
            "quiz_track_id": track_id,
        }

    async def _detect_difficult_words(
        self,
        cefr: str,
        *,
        paragraphs: Optional[Sequence[str]] = None,
        day_number: int = 1,
    ) -> List[Dict[str, Any]]:
        tokens = passage_tokens_from_paragraphs(paragraphs) if paragraphs else []
        db_levels = await self.repo.detect_levels_for_tokens(tokens)
        stretch_idx = _cefr_index(cefr) + 1
        out: List[Dict[str, Any]] = []
        for token in tokens:
            info = db_levels.get(token)
            curated = CURATED_DIFFICULT_WORDS.get(token)
            cefr_tok = (info or {}).get("cefr") or (curated or {}).get("cefr")
            band_tok = (info or {}).get("ielts_band_min") or (curated or {}).get("band")
            flagged = False
            if cefr_tok and _cefr_index(cefr_tok) >= stretch_idx:
                flagged = True
            elif curated is not None:
                flagged = True
            if flagged:
                out.append(
                    {
                        "word": token,
                        "cefr": cefr_tok,
                        "band": float(band_tok) if band_tok is not None else None,
                        "in_db": info is not None,
                    }
                )
            if len(out) >= 30:
                break
        return out

    def _recall_prompts(self, words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        prompts = []
        for item in words:
            word = item.get("word")
            if not word:
                continue
            prompts.append(
                {
                    "word": word,
                    "prompt": f'Recall: what does "{word}" mean, and use it in a short sentence.',
                    "definition": item.get("definition") or "",
                    "role": item.get("role"),
                    "lexeme_id": item.get("id"),
                    "quiz_steps": item.get("quiz_steps") or [],
                    "quiz_track_id": item.get("quiz_track_id"),
                    "vi_gloss": item.get("vi_gloss"),
                    "example": item.get("example") or "",
                    "phonetic": item.get("phonetic"),
                    "pos": item.get("pos"),
                    "cefr": item.get("cefr"),
                    "ielts_band_min": item.get("ielts_band_min"),
                }
            )
        return prompts

    async def get_day(
        self, *, user_id: str, day_number: int, locale: str = "en"
    ) -> Dict[str, Any]:
        plan = await self._require_plan(user_id)
        if day_number < 1 or day_number > plan.total_days:
            raise ValueError("Invalid day number")
        day = await self.repo.get_day(plan_id=plan.id, day_number=day_number)
        if day is None:
            raise ValueError("Day not found")

        if day_number > plan.current_day:
            catalog_titles = _merged_catalog_titles(
                plan.cefr_level,
                await self.content_repo.list_published_unit_titles(plan.cefr_level),
            )
            display_title = _resolve_day_reading_title(
                plan.cefr_level,
                day_number,
                catalog_titles=catalog_titles,
                reading=day.reading if isinstance(day.reading, dict) else None,
            )
            await self._sync_day_title(plan, day, catalog_titles=catalog_titles)
            return {
                "locked": True,
                "day_number": day_number,
                "status": "locked",
                "title": display_title,
                "preview": (day.analysis or {}).get("preview")
                or self._locked_preview(day_number, plan.cefr_level),
            }

        if day.status == "locked":
            day.status = "ready"
        await self._ensure_day_content(plan, day)
        catalog_titles = _merged_catalog_titles(
            plan.cefr_level,
            await self.content_repo.list_published_unit_titles(plan.cefr_level),
        )
        await self._sync_day_title(plan, day, catalog_titles=catalog_titles)
        display_title = _resolve_day_reading_title(
            plan.cefr_level,
            day_number,
            catalog_titles=catalog_titles,
            reading=day.reading if isinstance(day.reading, dict) else None,
        )

        recall: Dict[str, Any]
        if day_number == 1:
            recall = {
                "kind": "onboarding",
                "prompts": [],
                "message": "Day 1 builds your first memory pool. Tomorrow's recall will draw from today's words.",
            }
        else:
            prev = await self.repo.get_day(plan_id=plan.id, day_number=day_number - 1)
            recall = {
                "kind": "recall",
                "prompts": self._recall_prompts((prev.words if prev else []) or []),
                "message": "Rate each word from yesterday: Remember or Forgot. Forgotten words will be mixed into the post-reading vocab focus.",
            }

        words = await self._enrich_coaching_words(
            day.words or [], plan_cefr=plan.cefr_level
        )
        if words != (day.words or []):
            day.words = words
            await self.s.flush()

        return {
            "locked": False,
            "day_number": day.day_number,
            "status": day.status,
            "title": display_title,
            "reading_title": display_title,
            "focus_skill": day.focus_skill,
            "cefr_level": plan.cefr_level,
            "words": words,
            "reading": day.reading or {},
            "recall": recall,
            "sessions": day.sessions or {},
            "notes": day.notes or [],
            "analysis": day.analysis or {},
            "started_at": day.started_at.isoformat() if day.started_at else None,
            "completed_at": day.completed_at.isoformat() if day.completed_at else None,
        }

    async def start_day(self, *, user_id: str, day_number: int) -> Dict[str, Any]:
        plan = await self._require_plan(user_id)
        day = await self.repo.get_day(plan_id=plan.id, day_number=day_number)
        if day is None or day_number > plan.current_day:
            raise ValueError("Day is not available")
        await self._ensure_day_content(plan, day)
        if day.started_at is None:
            day.started_at = datetime.now(timezone.utc)
        if day.status in ("locked", "ready"):
            day.status = "in_progress"
        sessions = dict(day.sessions or {})
        reading_state = dict(sessions.get("reading") or {})
        reading_state.setdefault("started_at", datetime.now(timezone.utc).isoformat())
        reading_state["status"] = "in_progress"
        sessions["reading"] = reading_state
        day.sessions = sessions
        await self.s.flush()
        return {"status": day.status, "started_at": day.started_at.isoformat()}

    async def save_day_progress(
        self,
        *,
        user_id: str,
        day_number: int,
        workspace: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Persist in-progress UI state so learners can resume all three sessions."""
        plan = await self._require_plan(user_id)
        day = await self.repo.get_day(plan_id=plan.id, day_number=day_number)
        if day is None or day_number > plan.current_day:
            raise ValueError("Day is not available")
        if day.status == "completed":
            return {"saved": False, "reason": "day_completed"}

        await self._ensure_day_content(plan, day)
        if day.started_at is None:
            day.started_at = datetime.now(timezone.utc)
        if day.status in ("locked", "ready"):
            day.status = "in_progress"

        sessions = dict(day.sessions or {})
        sessions["workspace"] = workspace
        recall_answers = (
            workspace.get("recall_answers")
            if isinstance(workspace.get("recall_answers"), dict)
            else {}
        )
        focus_answers = (
            workspace.get("focus_answers")
            if isinstance(workspace.get("focus_answers"), dict)
            else {}
        )
        reading_answers = (
            workspace.get("reading_answers")
            if isinstance(workspace.get("reading_answers"), dict)
            else {}
        )

        recall_state = dict(sessions.get("recall") or {})
        if recall_answers:
            recall_state["status"] = "in_progress"
        sessions["recall"] = recall_state

        focus_state = dict(sessions.get("focus") or {})
        if focus_answers:
            focus_state["status"] = "in_progress"
        sessions["focus"] = focus_state

        reading_state = dict(sessions.get("reading") or {})
        if reading_answers or workspace.get("highlights") or workspace.get("bolds"):
            reading_state.setdefault(
                "started_at", datetime.now(timezone.utc).isoformat()
            )
            reading_state["status"] = "in_progress"
        sessions["reading"] = reading_state

        day.sessions = sessions
        await self.s.flush()
        return {"saved": True, "workspace": workspace}

    async def build_focus_plan(
        self,
        *,
        user_id: str,
        day_number: int,
        recall_answers: Dict[str, Any],
        reading_answers: Dict[str, Any],
        reading_vocab_signals: Sequence[Dict[str, Any]],
        min_words: int = FOCUS_PLAN_MIN_WORDS,
        max_words: int = FOCUS_PLAN_MAX_WORDS,
    ) -> Dict[str, Any]:
        plan = await self._require_plan(user_id)
        day = await self.repo.get_day(plan_id=plan.id, day_number=day_number)
        if day is None or day_number > plan.current_day:
            raise ValueError("Day is not available")
        await self._ensure_day_content(plan, day)

        sessions = dict(day.sessions or {})
        workspace = (
            dict(sessions.get("workspace") or {})
            if isinstance(sessions.get("workspace"), dict)
            else {}
        )
        existing = workspace.get("focus_plan")
        if isinstance(existing, dict) and existing.get("words"):
            return existing

        persisted_events = await self.repo.list_events(
            plan_id=plan.id,
            day_number=day_number,
            event_types=[
                "lookup",
                "word_lookup",
                "word_click",
                "text_select",
                "translate",
                "highlight",
                "highlight_add",
                "explain",
                "explain_request",
                "explain_result",
                "reading_answer",
            ],
        )
        persisted_actions = [
            self._event_to_helper_action(event) for event in persisted_events
        ]
        combined_actions = self._dedupe_focus_actions(
            [*persisted_actions, *list(reading_vocab_signals or [])]
        )
        focus_plan = await self._compose_focus_plan(
            plan=plan,
            day=day,
            day_number=day_number,
            recall_answers=recall_answers,
            reading_answers=reading_answers,
            actions=combined_actions,
            min_words=min_words,
            max_words=max_words,
        )

        workspace["focus_plan"] = focus_plan
        workspace["updated_at"] = datetime.now(timezone.utc).isoformat()
        sessions["workspace"] = workspace
        focus_state = dict(sessions.get("focus") or {})
        focus_state["status"] = "ready"
        focus_state.setdefault("started_at", datetime.now(timezone.utc).isoformat())
        sessions["focus"] = focus_state
        day.sessions = sessions
        await self.s.flush()
        return focus_plan

    def _dedupe_focus_actions(
        self, actions: Sequence[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        seen: set[str] = set()
        out: List[Dict[str, Any]] = []
        for action in actions:
            if not isinstance(action, dict):
                continue
            target = (
                action.get("target") if isinstance(action.get("target"), dict) else {}
            )
            payload = (
                action.get("payload") if isinstance(action.get("payload"), dict) else {}
            )
            key = str(action.get("event_id") or "").strip()
            if not key:
                key = "|".join(
                    str(action.get(name) or target.get(name) or payload.get(name) or "")
                    for name in (
                        "event_type",
                        "word",
                        "phrase",
                        "paragraph_index",
                    )
                )
            if key in seen:
                continue
            seen.add(key)
            out.append(action)
        return out

    async def _forgotten_recall_words(
        self,
        *,
        plan: VocabCoachingPlan,
        day_number: int,
        recall_answers: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        if day_number <= 1:
            return []
        prev = await self.repo.get_day(plan_id=plan.id, day_number=day_number - 1)
        if prev is None:
            return []
        words = await self._enrich_coaching_words(
            prev.words or [], plan_cefr=plan.cefr_level
        )
        forgotten: List[Dict[str, Any]] = []
        for word in words:
            if recall_answers.get(str(word.get("word") or "")) is False:
                forgotten.append(word)
        return forgotten

    async def _compose_focus_plan(
        self,
        *,
        plan: VocabCoachingPlan,
        day: VocabCoachingDay,
        day_number: int,
        recall_answers: Dict[str, Any],
        reading_answers: Dict[str, Any],
        actions: Sequence[Dict[str, Any]],
        min_words: int,
        max_words: int,
    ) -> Dict[str, Any]:
        reading = dict(day.reading or {})
        reading_candidates = [
            item
            for item in (reading.get("vocab_candidates") or [])
            if isinstance(item, dict)
        ]
        fallback_words = await self._enrich_coaching_words(
            day.words or [], plan_cefr=plan.cefr_level
        )
        forgotten_words = await self._forgotten_recall_words(
            plan=plan, day_number=day_number, recall_answers=recall_answers
        )

        entries: Dict[str, Dict[str, Any]] = {}
        omitted: Dict[str, Dict[str, Any]] = {}

        def add_omitted(text: Any, reason: str, source: str) -> None:
            clean = str(text or "").strip()
            token = normalize_token(clean)
            key = token or clean.lower()
            if not key or key in omitted:
                return
            omitted[key] = {
                "text": clean[:80] or key,
                "reason": reason,
                "source": source,
            }

        def add_word(item: Dict[str, Any], source: str, score: int) -> None:
            key = _coaching_word_key(item)
            if not key:
                return
            if not _coaching_word_has_quiz(item):
                add_omitted(item.get("word") or key, "no_quiz_steps", source)
                return
            existing = entries.get(key)
            if existing is None:
                entries[key] = {
                    "word": dict(item),
                    "sources": {source},
                    "score": score,
                    "difficulty": _difficulty_rank(item),
                }
                return
            existing["sources"].add(source)
            existing["score"] += score
            if len(item.get("quiz_steps") or []) > len(
                existing["word"].get("quiz_steps") or []
            ):
                existing["word"] = dict(item)

        for item in forgotten_words:
            add_word(item, FOCUS_SOURCE_FORGOTTEN, FOCUS_SIGNAL_SCORES["forgotten"])
        for item in reading_candidates:
            if not _coaching_word_meets_plan_level(item.get("cefr"), plan.cefr_level):
                continue
            add_word(item, FOCUS_SOURCE_READING, FOCUS_SIGNAL_SCORES["reading_seed"])
        for item in fallback_words:
            add_word(item, FOCUS_SOURCE_FALLBACK, 0)

        interaction_keys = self._apply_interaction_scores(entries, omitted, actions)
        wrong_keys = self._apply_wrong_reading_scores(
            entries,
            omitted,
            reading.get("questions") or [],
            reading_answers,
        )
        friction_count = len(interaction_keys | wrong_keys)
        target = self._adaptive_focus_size(
            friction_count=friction_count, min_words=min_words, max_words=max_words
        )

        def bucket(entry: Dict[str, Any]) -> int:
            sources = entry["sources"]
            for index, source in enumerate(FOCUS_SOURCE_ORDER):
                if source in sources:
                    return index
            return len(FOCUS_SOURCE_ORDER)

        sorted_entries = sorted(
            entries.values(),
            key=lambda entry: (
                bucket(entry),
                float(entry["difficulty"]),
                -int(entry["score"]),
                str(entry["word"].get("word") or "").lower(),
            ),
        )
        selected = sorted_entries[:target]

        words: List[Dict[str, Any]] = []
        for entry in selected:
            sources = [
                source for source in FOCUS_SOURCE_ORDER if source in entry["sources"]
            ]
            word = dict(entry["word"])
            word["focusSources"] = sources
            word["interactionScore"] = int(entry["score"])
            word["difficultyRank"] = float(entry["difficulty"])
            word["fromYesterdayForgot"] = FOCUS_SOURCE_FORGOTTEN in sources
            word["fromReading"] = FOCUS_SOURCE_READING in sources
            word["fromUserInteraction"] = FOCUS_SOURCE_INTERACTION in sources
            words.append(word)

        summary = {
            "forgotten": sum(1 for word in words if word.get("fromYesterdayForgot")),
            "interaction": sum(1 for word in words if word.get("fromUserInteraction")),
            "reading": sum(1 for word in words if word.get("fromReading")),
            "fallback": sum(
                1
                for word in words
                if FOCUS_SOURCE_FALLBACK in (word.get("focusSources") or [])
            ),
            "total": len(words),
            "target": target,
            "friction_count": friction_count,
            "candidate_count": len(entries),
            "omitted": len(omitted),
        }
        return {
            "words": words,
            "summary": summary,
            "omitted_candidates": list(omitted.values())[:12],
            "finalized_at": datetime.now(timezone.utc).isoformat(),
        }

    def _apply_interaction_scores(
        self,
        entries: Dict[str, Dict[str, Any]],
        omitted: Dict[str, Dict[str, Any]],
        actions: Sequence[Dict[str, Any]],
    ) -> set[str]:
        interaction_keys: set[str] = set()
        word_click_counts: Dict[str, int] = {}

        def score_text(text: Any, score: int, source_event: str) -> None:
            for token in _text_candidate_tokens(text):
                entry = entries.get(token)
                if entry is None:
                    omitted.setdefault(
                        token,
                        {
                            "text": token,
                            "reason": "no_quiz_steps",
                            "source": source_event,
                        },
                    )
                    continue
                entry["sources"].add(FOCUS_SOURCE_INTERACTION)
                entry["score"] += score
                interaction_keys.add(token)

        for action in actions:
            if not isinstance(action, dict):
                continue
            event_type = str(action.get("event_type") or action.get("type") or "")
            target = (
                action.get("target") if isinstance(action.get("target"), dict) else {}
            )
            word = action.get("word") or target.get("word")
            phrase = action.get("phrase") or target.get("phrase") or target.get("text")
            if event_type in ("lookup", "word_lookup"):
                score_text(word or phrase, FOCUS_SIGNAL_SCORES["lookup"], event_type)
            elif event_type in ("translate",):
                score_text(phrase or word, FOCUS_SIGNAL_SCORES["translate"], event_type)
            elif event_type in ("explain", "explain_request", "explain_result"):
                score_text(phrase or word, FOCUS_SIGNAL_SCORES["explain"], event_type)
            elif event_type in ("highlight", "highlight_add"):
                score_text(phrase or word, FOCUS_SIGNAL_SCORES["highlight"], event_type)
            elif event_type in ("word_click", "text_select"):
                tokens = _text_candidate_tokens(word or phrase)
                if len(tokens) <= 2:
                    for token in tokens:
                        word_click_counts[token] = word_click_counts.get(token, 0) + 1

        for token, count in word_click_counts.items():
            if count < 2:
                continue
            entry = entries.get(token)
            if entry is None:
                omitted.setdefault(
                    token,
                    {"text": token, "reason": "no_quiz_steps", "source": "word_click"},
                )
                continue
            entry["sources"].add(FOCUS_SOURCE_INTERACTION)
            entry["score"] += FOCUS_SIGNAL_SCORES["word_click"]
            interaction_keys.add(token)
        return interaction_keys

    def _apply_wrong_reading_scores(
        self,
        entries: Dict[str, Dict[str, Any]],
        omitted: Dict[str, Dict[str, Any]],
        questions: Sequence[Dict[str, Any]],
        reading_answers: Dict[str, Any],
    ) -> set[str]:
        wrong_keys: set[str] = set()
        by_id = {str(q.get("id") or ""): q for q in questions if isinstance(q, dict)}
        for question_id, selected in reading_answers.items():
            question = by_id.get(str(question_id))
            if not question or grade_reading_answer(question, str(selected)):
                continue
            source_word = question.get("source_word")
            for token in _text_candidate_tokens(source_word):
                entry = entries.get(token)
                if entry is None:
                    omitted.setdefault(
                        token,
                        {
                            "text": token,
                            "reason": "no_quiz_steps",
                            "source": "reading_answer",
                        },
                    )
                    continue
                entry["sources"].add(FOCUS_SOURCE_INTERACTION)
                entry["score"] += FOCUS_SIGNAL_SCORES["reading_wrong"]
                wrong_keys.add(token)
        return wrong_keys

    def _adaptive_focus_size(
        self, *, friction_count: int, min_words: int, max_words: int
    ) -> int:
        lower = max(
            1, min(FOCUS_PLAN_MIN_WORDS, int(min_words or FOCUS_PLAN_MIN_WORDS))
        )
        upper = max(lower, min(30, int(max_words or FOCUS_PLAN_MAX_WORDS)))
        if friction_count <= 2:
            target = 12
        elif friction_count <= 5:
            target = 15
        elif friction_count <= 8:
            target = 18
        else:
            target = 20
        return max(lower, min(upper, target))

    # ================================================================= events
    async def record_events(
        self, *, user_id: str, day_number: int, events: Sequence[Dict[str, Any]]
    ) -> Dict[str, Any]:
        plan = await self._require_plan(user_id)
        day = await self.repo.get_day(plan_id=plan.id, day_number=day_number)
        count = await self.repo.record_events(
            plan=plan, day=day, day_number=day_number, events=events
        )
        return {"recorded": count}

    # ============================================================== dictionary
    async def lookup_dictionary(self, *, word: str) -> Optional[Dict[str, Any]]:
        return await self.repo.lookup_dictionary(word)

    # ===================================================== AI Helper (streaming)
    async def stream_helper_recommendations(
        self,
        *,
        user_id: str,
        day_number: int,
        locale: str = "en",
        paragraph_index: int = 0,
        recent_actions: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> AsyncIterator[str]:
        """Stream markdown coaching tips for the AI Helper sidebar (token chunks)."""
        plan = await self._require_plan(user_id)
        day = await self.repo.get_day(plan_id=plan.id, day_number=day_number)
        if day is None or day_number > plan.current_day:
            raise ValueError("Day is not available")
        await self._ensure_day_content(plan, day)
        reading = day.reading or {}
        paragraphs = reading.get("paragraphs") or []
        idx = max(0, min(paragraph_index, max(0, len(paragraphs) - 1)))
        paragraph = str(paragraphs[idx] if paragraphs else "")
        context = {
            "locale": locale,
            "level": plan.cefr_level,
            "title": reading.get("title"),
            "paragraph": paragraph,
            "recent_actions": self._recent_actions_for_helper(recent_actions or []),
        }
        prompt = build_reading_helper_prompt(context=context)
        settings = get_settings()
        api_key = settings.openai_api_key or ""
        model = settings.openai_vocab_eval_model or settings.openai_model

        if api_key:
            try:
                from openai import AsyncOpenAI

                client = AsyncOpenAI(api_key=api_key)
                async for delta in openai_chat_completion_stream(
                    client,
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_output_tokens=700,
                    temperature=0.35,
                ):
                    yield delta
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("coaching helper OpenAI stream failed: {}", exc)
        elif settings.llm_provider == "openai":
            logger.warning(
                "coaching helper OpenAI stream falling back to mock: OPENAI_API_KEY missing model={}",
                model,
            )

        text = mock_reading_helper_text(context=context)
        for piece in text.split(" "):
            yield piece + " "
            await asyncio.sleep(0.02)

    async def generate_helper_note(
        self,
        *,
        user_id: str,
        day_number: int,
        locale: str = "en",
        paragraph_index: int = 0,
        visible_paragraph_indexes: Optional[Sequence[int]] = None,
        reading_selection: Optional[Dict[str, Any]] = None,
        reading_state: Optional[Dict[str, Any]] = None,
        recent_actions: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Structured live reading note (JSON) for AI Helper cards."""
        plan = await self._require_plan(user_id)
        day = await self.repo.get_day(plan_id=plan.id, day_number=day_number)
        if day is None or day_number > plan.current_day:
            raise ValueError("Day is not available")
        await self._ensure_day_content(plan, day)
        reading = day.reading or {}
        paragraphs = reading.get("paragraphs") or []
        raw_visible = list(visible_paragraph_indexes or [paragraph_index])
        visible = sorted(
            {max(0, min(int(i), max(0, len(paragraphs) - 1))) for i in raw_visible}
        ) or [max(0, min(paragraph_index, max(0, len(paragraphs) - 1)))]
        para_payload = [
            {"index": i, "text": str(paragraphs[i])[:1600]}
            for i in visible
            if 0 <= i < len(paragraphs)
        ]
        recent = self._recent_actions_for_helper(recent_actions or [])
        persisted = await self.repo.list_events(plan_id=plan.id, day_number=day_number)
        action_context = self._reading_action_context(
            events=persisted,
            recent_actions=recent,
        )
        context = {
            "locale": locale,
            "level": (reading_selection or {}).get("user_level") or plan.cefr_level,
            "title": (reading_selection or {}).get("passage_title")
            or reading.get("title"),
            "paragraphs": para_payload,
            "visible_paragraph_indexes": visible,
            "reading_selection": reading_selection or {},
            "reading_state": reading_state or {},
            "recent_actions": recent,
            **action_context,
        }
        messages = build_reading_helper_note_messages(context=context)
        settings = get_settings()
        api_key = settings.openai_api_key or ""
        model = settings.openai_vocab_eval_model or settings.openai_model

        cache_bundle = cache_key_from_selection(
            reading=reading,
            reading_selection=reading_selection,
            locale=locale,
            user_level=str(
                (reading_selection or {}).get("user_level") or plan.cefr_level
            ),
            model_name=model,
        )
        if cache_bundle:
            cache_key, cache_parts = cache_bundle
            cached_card = await self.repo.fetch_reading_coach_cache_and_hit(cache_key)
            if cached_card:
                logger.info(
                    "reading_coach_cache hit cache_key={} reading_id={} selection_type={}",
                    cache_key,
                    cache_parts.get("reading_id"),
                    cache_parts.get("selection_type"),
                )
                cached_card = align_reading_coach_card_to_selection(
                    cached_card,
                    reading_selection=reading_selection,
                    locale=locale,
                    context=context,
                )
                return self._reading_coach_card_response(cached_card)
            logger.info(
                "reading_coach_cache miss cache_key={} reading_id={}",
                cache_key,
                cache_parts.get("reading_id"),
            )

        if api_key:
            try:
                from openai import AsyncOpenAI

                client = AsyncOpenAI(api_key=api_key)
                raw = await openai_chat_completion_text(
                    client,
                    model=model,
                    messages=messages,
                    max_output_tokens=900,
                    temperature=0.3,
                    response_format={"type": "json_object"},
                )
                if raw:
                    card = normalize_reading_helper_note_payload(extract_json(raw))
                    card = align_reading_coach_card_to_selection(
                        card,
                        reading_selection=reading_selection,
                        locale=locale,
                        context=context,
                    )
                    if cache_bundle and is_cacheable_reading_coach_card(card):
                        cache_key, cache_parts = cache_bundle
                        sel = reading_selection or {}
                        await self.repo.upsert_reading_coach_cache(
                            cache_key=cache_key,
                            reading_id=str(cache_parts.get("reading_id") or ""),
                            selection_type=str(cache_parts.get("selection_type") or ""),
                            target_text=str(sel.get("selected_text") or "")[:1200],
                            sentence_text=str(
                                sel.get("sentence_text")
                                or sel.get("selected_text")
                                or ""
                            )[:1200],
                            locale=str(cache_parts.get("locale") or "en"),
                            user_level=str(cache_parts.get("user_level") or "B1"),
                            prompt_version=READING_COACH_PROMPT_VERSION,
                            model_name=model,
                            card_json=card,
                        )
                        logger.info(
                            "reading_coach_cache stored cache_key={} reading_id={}",
                            cache_key,
                            cache_parts.get("reading_id"),
                        )
                    return self._reading_coach_card_response(card)
                logger.warning(
                    "coaching helper note OpenAI returned empty content model={}",
                    model,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("coaching helper note OpenAI failed: {}", exc)
        elif settings.llm_provider == "openai":
            logger.warning(
                "coaching helper note falling back to mock: OPENAI_API_KEY missing model={}",
                model,
            )

        logger.warning(
            "coaching helper note using mock fallback model={} has_api_key={}",
            model,
            bool(api_key),
        )
        card = mock_reading_helper_note(context=context)
        card = align_reading_coach_card_to_selection(
            card,
            reading_selection=reading_selection,
            locale=locale,
            context=context,
        )
        return self._reading_coach_card_response(card)

    # =============================================================== translate
    async def translate_text(
        self, *, user_id: str, day_number: int, text: str, target: str = "vi"
    ) -> Dict[str, Any]:
        plan = await self._require_plan(user_id)
        day = await self.repo.get_day(plan_id=plan.id, day_number=day_number)
        clean = (text or "").strip()[:1200]
        if not clean:
            raise ValueError("text is required")
        target = ((target or "vi").strip().lower() or "vi")[:8]
        translation = clean
        try:
            out = await get_translate_client().translate_batch(
                [clean], target_language=target, source_language="en"
            )
            if out:
                translation = out[0]
        except Exception as exc:  # noqa: BLE001 — graceful fallback to source
            logger.warning("coaching translate failed: {}", exc)
        await self.repo.record_events(
            plan=plan,
            day=day,
            day_number=day_number,
            events=[
                {
                    "event_type": "translate",
                    "phrase": clean,
                    "payload": {"target": target, "translation": translation},
                }
            ],
        )
        return {"source": clean, "translation": translation, "target": target}

    # ============================================================= AI features
    async def explain_phrase(
        self, *, user_id: str, day_number: int, phrase: str, sentence: str
    ) -> Dict[str, Any]:
        plan = await self._require_plan(user_id)
        day = await self.repo.get_day(plan_id=plan.id, day_number=day_number)
        clean_phrase = (phrase or "").strip()
        if not clean_phrase:
            raise ValueError("phrase is required")
        clean_sentence = (sentence or "").strip() or find_sentence(clean_phrase)
        reading = day.reading or {}
        paragraphs = [str(p) for p in (reading.get("paragraphs") or [])]
        paragraph = next(
            (
                p
                for p in paragraphs
                if clean_phrase.lower() in p.lower()
                or clean_sentence[:80].lower() in p.lower()
            ),
            "",
        )
        signals = await self._action_signals(plan.id, day_number)
        context = {
            "level": plan.cefr_level,
            "phrase": clean_phrase,
            "sentence": clean_sentence,
            "paragraph": paragraph[:1800],
            "title": reading.get("title") if day else "Reading",
            **signals,
        }
        try:
            result = await get_llm_provider().explain_reading_phrase(context=context)
        except Exception as exc:  # noqa: BLE001 — graceful fallback
            logger.warning("coaching explain_phrase LLM failed: {}", exc)
            result = normalize_reading_explain_payload(
                {}, phrase=clean_phrase, sentence=clean_sentence, level=plan.cefr_level
            )
        await self.repo.record_events(
            plan=plan,
            day=day,
            day_number=day_number,
            events=[
                {
                    "event_type": "explain",
                    "phrase": clean_phrase,
                    "sentence": clean_sentence,
                    "payload": {"paraphrase": result.get("paraphrase")},
                }
            ],
        )
        return result

    async def generate_questions(
        self, *, user_id: str, day_number: int, count: int = 4, locale: str = "en"
    ) -> Dict[str, Any]:
        plan = await self._require_plan(user_id)
        day = await self.repo.get_day(plan_id=plan.id, day_number=day_number)
        if day is None:
            raise ValueError("Day not found")
        await self._ensure_day_content(plan, day)
        reading = day.reading or {}
        question_limit = int(reading.get("question_limit") or 4)
        seeded = reading.get("questions") or []
        if len(seeded) >= question_limit:
            return {"questions": reading.get("ai_questions") or []}

        signals = await self._action_signals(plan.id, day_number)
        difficult = reading.get("difficult_words") or []
        fallback = seeded
        passage = "\n\n".join(str(p) for p in (reading.get("paragraphs") or []))
        context = {
            "level": plan.cefr_level,
            "title": reading.get("title"),
            "passage": passage,
            "count": count,
            "difficult_words": difficult[:12],
            "fallback_questions": fallback,
            **signals,
        }
        try:
            payload = await get_llm_provider().generate_reading_questions(
                context=context
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("coaching generate_questions LLM failed: {}", exc)
            payload = normalize_reading_questions_payload(
                {}, count=count, fallback_questions=fallback
            )
        reading = dict(day.reading or {})
        reading["ai_questions"] = payload.get("questions") or []
        day.reading = reading
        await self.s.flush()
        return {"questions": reading["ai_questions"]}

    async def complete_day(
        self, *, user_id: str, day_number: int, locale: str = "en"
    ) -> Dict[str, Any]:
        plan = await self._require_plan(user_id)
        day = await self.repo.get_day(plan_id=plan.id, day_number=day_number)
        if day is None or day_number > plan.current_day:
            raise ValueError("Day is not available")
        await self._ensure_day_content(plan, day)

        signals = await self._action_signals(plan.id, day_number)
        reading_correct, reading_total = await self._answer_score(
            plan.id, day_number, "reading_answer"
        )
        recall_correct, recall_total = await self._answer_score(
            plan.id, day_number, "recall_answer"
        )
        context = {
            "level": plan.cefr_level,
            "day_number": day_number,
            "locale": locale,
            "reading_correct": reading_correct,
            "reading_total": reading_total,
            "recall_correct": recall_correct,
            "recall_total": recall_total,
            **signals,
        }
        try:
            notes_payload = await get_llm_provider().generate_coaching_notes(
                context=context
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("coaching notes LLM failed: {}", exc)
            notes_payload = normalize_coaching_notes_payload({}, context=context)

        day.notes = notes_payload.get("notes") or []
        day.analysis = {
            **(day.analysis or {}),
            "completed": {
                "headline": notes_payload.get("headline"),
                "next_focus": notes_payload.get("next_focus"),
                "recommended_words": notes_payload.get("recommended_words") or [],
                "reading_score": f"{reading_correct}/{reading_total}",
                "recall_score": f"{recall_correct}/{recall_total}",
            },
        }
        sessions = dict(day.sessions or {})
        sessions["notes"] = {"status": "completed"}
        day.sessions = sessions
        day.status = "completed"
        day.completed_at = day.completed_at or datetime.now(timezone.utc)

        advanced = False
        next_preview = None
        if plan.current_day == day_number and day_number < plan.total_days:
            plan.current_day = day_number + 1
            advanced = True
            nxt = await self.repo.get_day(plan_id=plan.id, day_number=day_number + 1)
            if nxt is not None:
                personalized = self._next_day_preview(
                    day_number + 1,
                    plan.cefr_level,
                    notes_payload.get("recommended_words") or [],
                    notes_payload.get("next_focus") or "",
                )
                nxt.status = "ready"
                nxt.analysis = {**(nxt.analysis or {}), "preview": personalized}
                await self._ensure_day_content(plan, nxt)
                next_preview = personalized
        await self.s.flush()

        return {
            "notes": notes_payload,
            "advanced": advanced,
            "current_day": plan.current_day,
            "next_day_preview": next_preview,
        }

    def _next_day_preview(
        self, number: int, cefr: str, recommended: List[str], next_focus: str
    ) -> str:
        words = ", ".join(recommended[:5]) if recommended else ""
        base = (
            f"Day {number} is now unlocked. It was designed from your previous results"
        )
        if words:
            base += f", prioritising the words you struggled with: {words}"
        if next_focus:
            base += f". Focus: {next_focus}"
        else:
            base += f". It keeps your {cefr} mix and adapts the reading questions."
        return base

    def _coach_card_id(self, card: Dict[str, Any]) -> str:
        seed = "|".join(
            [
                str(card.get("card_type") or ""),
                ",".join(str(x) for x in (card.get("trigger_event_ids") or [])),
                str((card.get("target") or {}).get("text") or ""),
                str(card.get("diagnosis") or ""),
            ]
        )
        digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
        return f"rcc-{digest}"

    def _reading_coach_card_response(self, card: Dict[str, Any]) -> Dict[str, Any]:
        if not card.get("should_show"):
            return {"card": None, "feed": []}
        card = dict(card)
        card["id"] = card.get("id") or self._coach_card_id(card)
        card.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        return {"card": card, "feed": [card]}

    def _event_to_helper_action(self, event) -> Dict[str, Any]:
        payload = event.payload or {}
        target = (
            payload.get("target") if isinstance(payload.get("target"), dict) else {}
        )
        result = (
            payload.get("result") if isinstance(payload.get("result"), dict) else {}
        )
        context = (
            payload.get("context") if isinstance(payload.get("context"), dict) else {}
        )
        action: Dict[str, Any] = {
            "event_id": str(payload.get("event_id") or event.id),
            "event_type": event.event_type,
            "type": event.event_type,
            "occurred_at": str(
                payload.get("occurred_at") or event.created_at.isoformat()
            ),
        }
        paragraph_index = payload.get("paragraph_index")
        if paragraph_index is not None:
            action["paragraph_index"] = paragraph_index
        if event.word:
            action["word"] = event.word
        if event.phrase:
            action["phrase"] = str(event.phrase)[:240]
        if event.sentence:
            action["sentence"] = str(event.sentence)[:500]
        if event.is_correct is not None:
            action["is_correct"] = bool(event.is_correct)
        if target:
            action["target"] = target
        if context:
            action["context"] = context
        if result:
            action["result"] = result
        if payload.get("selection_intent"):
            action["selection_intent"] = str(payload.get("selection_intent"))[:40]
        if payload.get("translation"):
            action["translation"] = str(payload.get("translation"))[:240]
        if payload.get("paraphrase"):
            action["paraphrase"] = str(payload.get("paraphrase"))[:220]
        if payload.get("question_id"):
            action["question_id"] = str(payload.get("question_id"))[:96]
        return action

    def _reading_action_context(
        self,
        *,
        events: Sequence[Any],
        recent_actions: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        combined: List[Dict[str, Any]] = [
            self._event_to_helper_action(event) for event in events
        ] + list(recent_actions)
        seen: set[str] = set()
        actions: List[Dict[str, Any]] = []
        for action in combined:
            event_id = str(action.get("event_id") or "").strip()
            fallback = "|".join(
                str(action.get(k) or "")
                for k in ("event_type", "word", "phrase", "question_id", "occurred_at")
            )
            key = event_id or fallback
            if key in seen:
                continue
            seen.add(key)
            actions.append(action)

        looked_up: List[str] = []
        translated: List[str] = []
        selected: List[str] = []
        highlighted: List[str] = []
        explained: List[str] = []
        bolded: List[str] = []
        wrong: List[str] = []
        logic_hits: List[str] = []
        evidence_actions = 0

        def add_unique(rows: List[str], value: Any, limit: int = 160) -> None:
            text = str(value or "").strip()
            if text and text not in rows:
                rows.append(text[:limit])

        for action in actions:
            et = str(action.get("event_type") or action.get("type") or "")
            target = (
                action.get("target") if isinstance(action.get("target"), dict) else {}
            )
            word = action.get("word") or target.get("word")
            phrase = action.get("phrase") or target.get("phrase") or target.get("text")
            payload_result = (
                action.get("result") if isinstance(action.get("result"), dict) else {}
            )
            lowered = " ".join(
                str(x or "") for x in (word, phrase, action.get("sentence"))
            ).lower()
            if et in ("lookup", "word_lookup", "word_click") and word:
                add_unique(looked_up, normalize_token(str(word)), 64)
            if et == "translate" and phrase:
                add_unique(translated, phrase)
            if et == "text_select" and phrase:
                add_unique(selected, phrase)
            if et in ("highlight", "highlight_add") and phrase:
                add_unique(highlighted, phrase)
                evidence_actions += 1
            if et in ("explain", "explain_request", "explain_result") and phrase:
                add_unique(explained, phrase)
            if et == "bold" and word:
                add_unique(bolded, normalize_token(str(word)), 64)
            if et == "reading_answer" and (
                action.get("is_correct") is False
                or payload_result.get("is_correct") is False
            ):
                add_unique(
                    wrong, action.get("phrase") or action.get("question_id") or word
                )
            if any(
                marker in lowered
                for marker in (
                    "although",
                    "however",
                    "therefore",
                    "while",
                    "whereas",
                    "because",
                    "despite",
                )
            ):
                add_unique(logic_hits, phrase or action.get("sentence") or word)

        action_summary = {
            "vocab_friction": {
                "lookups": len(looked_up),
                "translations": len(translated),
                "repeated_words": looked_up[:6],
            },
            "sentence_friction": {
                "selected_phrases": selected[-6:],
                "explained_phrases": explained[-6:],
            },
            "logic_friction": {
                "hits": logic_hits[-6:],
            },
            "evidence_behavior": {
                "highlight_count": evidence_actions,
                "wrong_answer_count": len(wrong),
                "wrong_answers": wrong[-6:],
            },
            "latest_action": actions[-1] if actions else None,
        }
        return {
            "recent_actions": actions[-15:],
            "action_summary": action_summary,
            "looked_up_words": looked_up[:12],
            "translated_phrases": translated[:10],
            "selected_phrases": selected[:10],
            "highlighted_phrases": highlighted[:10],
            "explained_phrases": explained[:10],
            "bolded_words": bolded[:8],
            "wrong_answers": wrong[:8],
        }

    async def reset(self, *, user_id: str) -> Dict[str, Any]:
        archived = await self.repo.archive_active_plans(user_id)
        await self.stats.clear_vocab_calibration(user_id)
        return {"reset": True, "archived_plans": archived}

    async def change_level(
        self, *, user_id: str, cefr_level: str, locale: str = "en"
    ) -> Dict[str, Any]:
        """Switch coaching CEFR and rebuild the 30-day plan (progress resets)."""
        _ = locale
        cefr = str(cefr_level or "").strip().upper()
        if cefr not in CEFR_LEVELS:
            raise ValueError("Invalid CEFR level")
        if cefr == "C2":
            published = await self.content_repo.count_published_units("C2")
            if published < COACHING_C2_RELEASE_DAYS:
                cefr = "C1"
        stats = await self.stats.set_vocab_coaching_level(
            user_id, cefr_level=cefr, source="manual"
        )
        profile = stats.get("vocab_profile") or {}
        plan = await self._create_plan_from_calibration(user_id, profile)
        return await self._plan_view(plan)

    def _recent_actions_for_helper(
        self, recent_actions: Sequence[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Normalize client-side action journal for the helper prompt (no invented actions)."""
        out: List[Dict[str, Any]] = []
        for raw in recent_actions:
            et = str(raw.get("event_type") or raw.get("type") or "").strip()
            if not et:
                continue
            payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
            target = raw.get("target") if isinstance(raw.get("target"), dict) else {}
            context = raw.get("context") if isinstance(raw.get("context"), dict) else {}
            result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
            entry: Dict[str, Any] = {"type": et, "event_type": et}
            if raw.get("event_id"):
                entry["event_id"] = str(raw.get("event_id"))[:96]
            if raw.get("occurred_at"):
                entry["occurred_at"] = str(raw.get("occurred_at"))[:40]
            word = str(raw.get("word") or "").strip()
            phrase = str(raw.get("phrase") or "").strip()
            if word:
                entry["word"] = word[:64]
            if phrase:
                entry["phrase"] = phrase[:160]
            sentence = str(raw.get("sentence") or "").strip()
            if sentence:
                entry["sentence"] = sentence[:400]
            paragraph_index = raw.get("paragraph_index")
            if paragraph_index is None:
                paragraph_index = payload.get("paragraph_index")
            if paragraph_index is not None:
                entry["paragraph_index"] = paragraph_index
            visible = raw.get("visible_paragraph_indexes")
            if isinstance(visible, list):
                entry["visible_paragraph_indexes"] = visible[:8]
            if target:
                entry["target"] = target
                if not entry.get("word") and target.get("word"):
                    entry["word"] = str(target.get("word"))[:64]
                if not entry.get("phrase") and (
                    target.get("phrase") or target.get("text")
                ):
                    entry["phrase"] = str(target.get("phrase") or target.get("text"))[
                        :160
                    ]
                if not entry.get("sentence") and target.get("sentence"):
                    entry["sentence"] = str(target.get("sentence"))[:400]
            if context:
                entry["context"] = context
            if result:
                entry["result"] = result
            if raw.get("is_correct") is not None:
                entry["is_correct"] = bool(raw.get("is_correct"))
            if payload.get("paragraph_index") is not None:
                entry["paragraph_index"] = payload.get("paragraph_index")
            if payload.get("selection_intent"):
                entry["selection_intent"] = str(payload.get("selection_intent"))[:40]
            if et == "translate":
                entry["translation"] = str(payload.get("translation") or "")[:200]
            if et == "explain" and payload.get("paraphrase"):
                entry["paraphrase"] = str(payload.get("paraphrase") or "")[:160]
            if et == "reading_answer":
                if raw.get("is_correct") is not None:
                    entry["is_correct"] = bool(raw.get("is_correct"))
                if payload.get("question_id"):
                    entry["question_id"] = str(payload.get("question_id"))[:64]
            out.append(entry)
        return out[-12:]

    # ================================================================ helpers
    async def _require_plan(self, user_id: str) -> VocabCoachingPlan:
        plan = await self.repo.get_active_plan(user_id)
        if plan is None:
            raise ValueError("No active coaching plan")
        return plan

    async def _action_signals(self, plan_id, day_number: int) -> Dict[str, Any]:
        events = await self.repo.list_events(
            plan_id=plan_id,
            day_number=day_number,
            event_types=None,
        )
        return self._reading_action_context(events=events, recent_actions=[])

    async def _answer_score(
        self, plan_id, day_number: int, event_type: str
    ) -> tuple[int, int]:
        events = await self.repo.list_events(
            plan_id=plan_id, day_number=day_number, event_types=(event_type,)
        )
        # Keep the latest answer per question/word.
        latest: Dict[str, bool] = {}
        for event in events:
            key = str(
                (event.payload or {}).get("question_id") or event.word or event.id
            )
            if event.is_correct is not None:
                latest[key] = bool(event.is_correct)
        total = len(latest)
        correct = sum(1 for ok in latest.values() if ok)
        return correct, total
