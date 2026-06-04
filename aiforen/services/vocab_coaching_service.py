"""Adaptive 31-day vocab coaching orchestration.

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
from aiforen.domain.sql_models import VocabCoachingDay, VocabCoachingPlan
from aiforen.domain.vocab_coaching_reading import (
    CURATED_DIFFICULT_WORDS,
    build_reading_payload,
    find_sentence,
    normalize_token,
    passage_tokens,
)
from aiforen.integrations.llm import get_llm_provider
from aiforen.integrations.llm.json_utils import (
    build_reading_helper_note_prompt,
    build_reading_helper_prompt,
    extract_json,
    mock_reading_helper_note,
    mock_reading_helper_text,
    normalize_coaching_notes_payload,
    normalize_reading_explain_payload,
    normalize_reading_helper_note_payload,
    normalize_reading_questions_payload,
)
from aiforen.integrations.translate import get_translate_client
from aiforen.repositories.pg.user_stats import VN_TZ, UserStatsRepo
from aiforen.repositories.pg.vocab_coaching import VocabCoachingRepo
from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo

CEFR_LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]
TOTAL_DAYS = 31

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
            source="calibration",
            start_date=datetime.now(VN_TZ).date(),
            total_days=TOTAL_DAYS,
            meta={
                "ielts_range": _ielts_range(cefr),
                "mix": {"current": 10, "lower": 2, "stretch": 3},
            },
        )
        day_rows = []
        for number in range(1, TOTAL_DAYS + 1):
            day_rows.append(
                {
                    "day_number": number,
                    "status": "ready" if number == 1 else "locked",
                    "title": (
                        "Day 1 · Establish your vocabulary rhythm"
                        if number == 1
                        else f"Day {number} · Adaptive focus"
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
        return plan

    def _locked_preview(self, number: int, cefr: str) -> str:
        return (
            f"Day {number} unlocks after Day {number - 1}. The coach will analyze your "
            f"Day {number - 1} results — the words you looked up, your reading score and "
            f"weak spots — and design Day {number}'s {cefr} word mix and reading focus "
            "around them."
        )

    async def _plan_view(self, plan: VocabCoachingPlan) -> Dict[str, Any]:
        days = await self.repo.list_days(plan.id)
        timeline = []
        for day in days:
            unlocked = day.day_number <= plan.current_day
            timeline.append(
                {
                    "day_number": day.day_number,
                    "status": day.status,
                    "title": day.title,
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
        if day.words:
            return
        words = await self._build_word_mix(plan.cefr_level)
        difficult = await self._detect_difficult_words(plan.cefr_level)
        reading = build_reading_payload(difficult)
        day.words = await self._enrich_coaching_words(words, plan_cefr=plan.cefr_level)
        day.reading = reading
        day.sessions = {
            "recall": {"status": "pending"},
            "reading": {"status": "pending"},
            "notes": {"status": "pending"},
        }
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

    async def _detect_difficult_words(self, cefr: str) -> List[Dict[str, Any]]:
        tokens = passage_tokens()
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
            return {
                "locked": True,
                "day_number": day_number,
                "status": "locked",
                "preview": (day.analysis or {}).get("preview")
                or self._locked_preview(day_number, plan.cefr_level),
            }

        if day.status == "locked":
            day.status = "ready"
        await self._ensure_day_content(plan, day)

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
                "message": "Rate each word from yesterday: Remember or Forgot. Forgot words join today's Adaptive focus.",
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
            "title": day.title,
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
                stream = await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.35,
                    max_tokens=700,
                    stream=True,
                )
                async for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        yield delta
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("coaching helper OpenAI stream failed: {}", exc)

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
            "level": plan.cefr_level,
            "title": reading.get("title"),
            "paragraphs": para_payload,
            "visible_paragraph_indexes": visible,
            "reading_state": reading_state or {},
            "recent_actions": recent,
            **action_context,
        }
        prompt = build_reading_helper_note_prompt(context=context)
        settings = get_settings()
        api_key = settings.openai_api_key or ""
        model = settings.openai_vocab_eval_model or settings.openai_model

        if api_key:
            try:
                from openai import AsyncOpenAI

                client = AsyncOpenAI(api_key=api_key)
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=900,
                    response_format={"type": "json_object"},
                )
                raw = (resp.choices[0].message.content or "").strip()
                if raw:
                    card = normalize_reading_helper_note_payload(extract_json(raw))
                    feed = self._append_reading_coach_card(day, card)
                    await self.s.flush()
                    return {
                        "card": card if card.get("should_show") else None,
                        "feed": feed,
                    }
            except Exception as exc:  # noqa: BLE001
                logger.warning("coaching helper note OpenAI failed: {}", exc)

        card = mock_reading_helper_note(context=context)
        feed = self._append_reading_coach_card(day, card)
        await self.s.flush()
        return {"card": card if card.get("should_show") else None, "feed": feed}

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
        signals = await self._action_signals(plan.id, day_number)
        reading = day.reading or {}
        difficult = reading.get("difficult_words") or []
        fallback = reading.get("questions") or []
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
            "reading_coach_feed": (day.analysis or {}).get("reading_coach_feed") or [],
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

    def _append_reading_coach_card(
        self, day: Optional[VocabCoachingDay], card: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        if day is None:
            return []
        analysis = dict(day.analysis or {})
        feed = [
            row
            for row in analysis.get("reading_coach_feed") or []
            if isinstance(row, dict)
        ]
        if not card.get("should_show"):
            return feed
        card = dict(card)
        card["id"] = card.get("id") or self._coach_card_id(card)
        card.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        signature = (
            card.get("card_type"),
            tuple(card.get("trigger_event_ids") or []),
            ((card.get("target") or {}).get("text") or "").strip().lower(),
        )
        for existing in feed:
            existing_signature = (
                existing.get("card_type"),
                tuple(existing.get("trigger_event_ids") or []),
                ((existing.get("target") or {}).get("text") or "").strip().lower(),
            )
            if existing.get("id") == card["id"] or existing_signature == signature:
                return feed
        feed = [*feed, card][-30:]
        analysis["reading_coach_feed"] = feed
        day.analysis = analysis
        return feed

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
