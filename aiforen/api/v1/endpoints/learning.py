"""Learning endpoints — exact paths/shapes that `learningService.ts` calls."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.core.deps import (
    CurrentUser,
    get_current_user,
    get_pg,
    get_redis,
)
from aiforen.services.learning_service import LearningService
from aiforen.services.vocab_workout_service import VocabWorkoutService

router = APIRouter()


def _learning_svc(pg: AsyncSession) -> LearningService:
    return LearningService(pg)


def _workout_svc(pg: AsyncSession) -> VocabWorkoutService:
    return VocabWorkoutService(pg)


# ---------- dictionary proxy (Cambridge-style entry) ----------

_DICTIONARY_URL = "https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
_DICTIONARY_CACHE_TTL_SECONDS = 60 * 60 * 24
_DICTIONARY_REDIS_PREFIX = "dict:en:"


def _normalize_dictionary(payload: List[Dict[str, Any]], word: str) -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = []
    for entry in payload or []:
        phonetic = entry.get("phonetic")
        phonetics = entry.get("phonetics") or []
        if not phonetic:
            phonetic = next((p.get("text") for p in phonetics if p.get("text")), None)
        audio = next((p.get("audio") for p in phonetics if p.get("audio")), None)
        meanings_out: List[Dict[str, Any]] = []
        for meaning in entry.get("meanings") or []:
            defs_out: List[Dict[str, Any]] = []
            for d in (meaning.get("definitions") or [])[:4]:
                defs_out.append(
                    {
                        "definition": str(d.get("definition") or "").strip(),
                        "example": str(d.get("example") or "").strip(),
                        "synonyms": (d.get("synonyms") or [])[:6],
                    }
                )
            if defs_out:
                meanings_out.append(
                    {
                        "part_of_speech": str(
                            meaning.get("partOfSpeech") or ""
                        ).strip(),
                        "definitions": defs_out,
                        "synonyms": (meaning.get("synonyms") or [])[:6],
                        "antonyms": (meaning.get("antonyms") or [])[:6],
                    }
                )
        if meanings_out:
            entries.append(
                {
                    "word": entry.get("word") or word,
                    "phonetic": phonetic,
                    "audio": audio,
                    "source": (entry.get("sourceUrls") or [None])[0],
                    "meanings": meanings_out,
                }
            )
    return {
        "word": word,
        "entries": entries,
        "cambridge_link": f"https://dictionary.cambridge.org/dictionary/english/{word}",
        "dictionary_link": f"https://www.lexico.com/en/definition/{word}",
    }


async def _dictionary_from_cache(redis, word: str) -> Optional[Dict[str, Any]]:
    if redis is None:
        return None
    raw = await redis.get(f"{_DICTIONARY_REDIS_PREFIX}{word}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def _dictionary_to_cache(redis, word: str, payload: Dict[str, Any]) -> None:
    if redis is None:
        return
    await redis.setex(
        f"{_DICTIONARY_REDIS_PREFIX}{word}",
        _DICTIONARY_CACHE_TTL_SECONDS,
        json.dumps(payload),
    )


@router.get("/dictionary/{word}")
async def lookup_dictionary(word: str, redis=Depends(get_redis)):
    cleaned = (word or "").strip().lower()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Empty word")
    cached = await _dictionary_from_cache(redis, cleaned)
    if cached is not None:
        return {"success": True, "data": cached, "cached": True}
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(_DICTIONARY_URL.format(word=cleaned))
        if resp.status_code == 404:
            empty = {
                "word": cleaned,
                "entries": [],
                "cambridge_link": f"https://dictionary.cambridge.org/dictionary/english/{cleaned}",
                "dictionary_link": f"https://www.lexico.com/en/definition/{cleaned}",
            }
            await _dictionary_to_cache(redis, cleaned, empty)
            return {"success": True, "data": empty, "cached": False}
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502, detail=f"Dictionary upstream error: {resp.status_code}"
            )
        normalized = _normalize_dictionary(resp.json(), cleaned)
        await _dictionary_to_cache(redis, cleaned, normalized)
        return {"success": True, "data": normalized, "cached": False}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Dictionary lookup failed: {exc}")


class VocabProfileIn(BaseModel):
    current_band: float
    target_band: float
    daily_goal: int = 5


class VocabMcqIn(BaseModel):
    selected_option_id: Optional[str] = None
    question_id: Optional[str] = None
    free_text_answer: Optional[str] = None
    reorder_order: Optional[List[int]] = None
    time_taken: int = 0


class VocabCalibrationAnswer(BaseModel):
    word_id: str
    word: str
    pack_id: Optional[str] = None
    band: Optional[float] = None
    level: int = Field(ge=0, le=3)
    response_time_ms: Optional[int] = Field(default=None, ge=0)


class VocabCalibrationIn(BaseModel):
    answers: List[VocabCalibrationAnswer]
    locale: str = "vi"
    check_size: int = Field(default=32, description="32, 48, or 60")

    @field_validator("check_size", mode="before")
    @classmethod
    def _normalize_check_size(cls, value: Any) -> int:
        from aiforen.domain.quick_vocab_check import normalize_check_size

        return normalize_check_size(value)


class ResetLearningIn(BaseModel):
    confirm: str = Field(..., min_length=1, max_length=32)


def _wrap(data: Any, **extra: Any) -> Dict[str, Any]:
    body = {"success": True, "data": data}
    body.update(extra)
    return body


@router.get("/vocab/profile")
async def vocab_profile(
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    svc = _learning_svc(pg)
    return _wrap(await svc.get_vocab_profile(user.id))


@router.put("/vocab/profile")
async def update_vocab_profile(
    payload: VocabProfileIn,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    svc = _learning_svc(pg)
    return _wrap(
        await svc.update_vocab_profile(
            user.id,
            current_band=payload.current_band,
            target_band=payload.target_band,
            daily_goal=payload.daily_goal,
        )
    )


@router.post("/vocab/reset-learning")
async def reset_vocab_learning(
    payload: ResetLearningIn,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    """Clear learner history (Mongo + Postgres); keeps account and subscription."""
    if payload.confirm.strip().upper() != "RESET":
        raise HTTPException(
            status_code=400,
            detail="Type RESET in confirm to clear learning history.",
        )
    svc = _learning_svc(pg)
    return _wrap(await svc.reset_user_learning_data(user_id=user.id))


@router.get("/vocab/packs")
async def vocab_packs(
    current_band: Optional[float] = None,
    target_band: Optional[float] = None,
    all_packs: bool = False,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    svc = _learning_svc(pg)
    return _wrap(
        await svc.list_vocab_packs(
            user_id=user.id,
            current_band=current_band,
            target_band=target_band,
            all_packs=all_packs,
        )
    )


def _parse_task_progress_query(
    raw: Optional[str],
) -> Optional[Dict[str, Dict[str, int]]]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return LearningService._normalize_task_progress(data) or None


@router.get("/vocab/today-mission")
async def vocab_today_mission(
    locale: str = Query("vi", min_length=2, max_length=8),
    task_progress: Optional[str] = Query(
        None,
        description="JSON map of mission task progress (completed/total per task key)",
    ),
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    svc = _learning_svc(pg)
    return _wrap(
        await svc.get_vocab_today_mission(
            user.id,
            plan_code=user.plan_code,
            locale=locale,
            task_progress=_parse_task_progress_query(task_progress),
        )
    )


class VocabCoachInsightIn(BaseModel):
    locale: str = "vi"
    task_progress: Dict[str, Any] = Field(default_factory=dict)


@router.post("/vocab/today-mission/coach-insight")
async def vocab_coach_insight(
    payload: VocabCoachInsightIn,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    svc = _learning_svc(pg)
    return _wrap(
        await svc.refresh_vocab_coach_insight(
            user.id,
            locale=payload.locale,
            task_progress=payload.task_progress,
            plan_code=user.plan_code,
        )
    )


class VocabWorkoutAnswerIn(BaseModel):
    attempt_id: str = Field(..., min_length=8, max_length=128)
    selected_option_id: Optional[str] = None
    free_text_answer: Optional[str] = None
    reorder_order: Optional[List[int]] = None
    time_taken: int = Field(default=0, ge=0)


@router.get("/vocab/today-workout")
async def vocab_today_workout(
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    return _wrap(
        await _workout_svc(pg).today(user_id=user.id, plan_code=user.plan_code)
    )


@router.get("/vocab/workouts/{workout_id}")
async def vocab_workout(
    workout_id: str,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    try:
        return _wrap(await _workout_svc(pg).get(user_id=user.id, workout_id=workout_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/vocab/workouts/{workout_id}/start")
async def start_vocab_workout(
    workout_id: str,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    try:
        return _wrap(
            await _workout_svc(pg).start(user_id=user.id, workout_id=workout_id)
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/vocab/workouts/{workout_id}/items/{item_id}/answer")
async def answer_vocab_workout_item(
    workout_id: str,
    item_id: str,
    payload: VocabWorkoutAnswerIn,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    try:
        return _wrap(
            await _workout_svc(pg).answer(
                user_id=user.id,
                plan_code=user.plan_code,
                workout_id=workout_id,
                item_id=item_id,
                attempt_id=payload.attempt_id,
                selected_option_id=payload.selected_option_id,
                free_text_answer=payload.free_text_answer,
                reorder_order=payload.reorder_order,
                time_taken=payload.time_taken,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/vocab/workouts/{workout_id}/bonus")
async def start_vocab_workout_bonus(
    workout_id: str,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    try:
        return _wrap(
            await _workout_svc(pg).bonus(user_id=user.id, workout_id=workout_id)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/vocab/calibration-words")
async def vocab_calibration_words(
    limit: Optional[int] = Query(
        None,
        description="Word count: 32, 48, or 60",
    ),
    check_size: Optional[int] = Query(
        None,
        description="Alias for limit (32, 48, or 60)",
    ),
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    from aiforen.domain.quick_vocab_check import normalize_check_size

    svc = _learning_svc(pg)
    raw = check_size if check_size is not None else limit
    size = normalize_check_size(raw if raw is not None else 32)
    return _wrap(await svc.get_vocab_calibration_words(user_id=user.id, limit=size))


@router.post("/vocab/calibration-review")
async def vocab_calibration_review(
    payload: VocabCalibrationIn,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    svc = _learning_svc(pg)
    return _wrap(
        await svc.review_vocab_calibration(
            user_id=user.id,
            answers=[
                answer.model_dump() if hasattr(answer, "model_dump") else answer.dict()
                for answer in payload.answers
            ],
            locale=payload.locale,
            check_size=payload.check_size,
        )
    )


@router.get("/vocab/session")
async def vocab_session(
    pack_id: str = Query(...),
    limit: int = 5,
    word_ids: list[str] | None = Query(None),
    random_any: bool = Query(False),
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    svc = _learning_svc(pg)
    return _wrap(
        await svc.get_vocab_session(
            user_id=user.id,
            pack_id=pack_id,
            limit=limit,
            word_ids=word_ids,
            random_any=random_any,
        )
    )


@router.post("/vocab/words/{word_id}/mark-known")
async def mark_vocab_known(
    word_id: str,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    svc = _learning_svc(pg)
    return _wrap(await svc.mark_vocab_known(user_id=user.id, word_id=word_id))


@router.post("/vocab/words/{word_id}/forgot")
async def forgot_vocab_word(
    word_id: str,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    svc = _learning_svc(pg)
    return _wrap(await svc.forgot_vocab_word(user_id=user.id, word_id=word_id))


@router.post("/vocab/words/{word_id}/learn-recall")
async def vocab_learn_recall(
    word_id: str,
    pack_id: Optional[str] = Query(None),
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    svc = _learning_svc(pg)
    return _wrap(
        await svc.submit_vocab_learn_recall(
            user_id=user.id, word_id=word_id, pack_id=pack_id
        )
    )


@router.post("/vocab/words/{word_id}/mcq-answer")
async def vocab_mcq_answer(
    word_id: str,
    payload: VocabMcqIn,
    pack_id: Optional[str] = Query(None),
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    svc = _learning_svc(pg)
    return _wrap(
        await svc.submit_vocab_mcq(
            user_id=user.id,
            plan_code=user.plan_code,
            word_id=word_id,
            selected_option_id=payload.selected_option_id,
            question_id=payload.question_id,
            free_text_answer=payload.free_text_answer,
            reorder_order=payload.reorder_order,
            pack_id=pack_id,
            time_taken=payload.time_taken,
        )
    )


@router.get("/vocab/stats")
async def vocab_stats(
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    svc = _learning_svc(pg)
    return _wrap(await svc.get_vocab_stats(user.id, plan_code=user.plan_code))
