"""Vocab Coaching endpoints — 31-day adaptive plan, reading, DB-first lookup, AI."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.core.deps import CurrentUser, get_current_user, get_pg, get_redis
from aiforen.services.vocab_coaching_service import VocabCoachingService

# Reuse the existing external dictionary proxy helpers for the fallback path.
from .learning import (
    _DICTIONARY_URL,
    _dictionary_from_cache,
    _dictionary_to_cache,
    _normalize_dictionary,
)

router = APIRouter()


def _svc(pg: AsyncSession) -> VocabCoachingService:
    return VocabCoachingService(pg)


def _ok(data: Any) -> Dict[str, Any]:
    return {"success": True, "data": data}


_COACHING_USER_MESSAGE = "We couldn't prepare this coaching day right now. Please try again in a few minutes."


def _guard(exc: ValueError) -> HTTPException:
    logger.warning("vocab coaching request failed: {}", exc)
    return HTTPException(status_code=400, detail=_COACHING_USER_MESSAGE)


class CoachingEventIn(BaseModel):
    event_type: str = Field(..., max_length=32)
    event_id: Optional[str] = Field(default=None, max_length=96)
    occurred_at: Optional[str] = Field(default=None, max_length=40)
    paragraph_index: Optional[int] = Field(default=None, ge=0, le=100)
    visible_paragraph_indexes: List[int] = Field(default_factory=list)
    word: Optional[str] = None
    phrase: Optional[str] = None
    sentence: Optional[str] = None
    is_correct: Optional[bool] = None
    target: Dict[str, Any] = Field(default_factory=dict)
    context: Dict[str, Any] = Field(default_factory=dict)
    result: Dict[str, Any] = Field(default_factory=dict)
    payload: Dict[str, Any] = Field(default_factory=dict)


class CoachingDayProgressIn(BaseModel):
    workspace: Dict[str, Any] = Field(default_factory=dict)


class CoachingFocusPlanIn(BaseModel):
    recall_answers: Dict[str, bool] = Field(default_factory=dict)
    reading_answers: Dict[str, str] = Field(default_factory=dict)
    reading_vocab_signals: List[Dict[str, Any]] = Field(default_factory=list)
    min_words: int = Field(default=12, ge=1, le=30)
    max_words: int = Field(default=20, ge=1, le=30)


class CoachingEventsIn(BaseModel):
    day_number: int = Field(..., ge=1, le=366)
    events: List[CoachingEventIn]


class CoachingExplainIn(BaseModel):
    day_number: int = Field(..., ge=1, le=366)
    phrase: str = Field(..., min_length=1)
    sentence: Optional[str] = None


class CoachingQuestionsIn(BaseModel):
    count: int = Field(default=4, ge=2, le=8)


class CoachingTranslateIn(BaseModel):
    day_number: int = Field(..., ge=1, le=366)
    text: str = Field(..., min_length=1, max_length=1200)
    target_language: str = Field(default="vi", min_length=2, max_length=8)


class ReadingCoachActionIn(BaseModel):
    event_type: str = Field(..., max_length=32)
    event_id: Optional[str] = Field(default=None, max_length=96)
    occurred_at: Optional[str] = Field(default=None, max_length=40)
    paragraph_index: Optional[int] = Field(default=None, ge=0, le=100)
    visible_paragraph_indexes: List[int] = Field(default_factory=list)
    word: Optional[str] = None
    phrase: Optional[str] = None
    sentence: Optional[str] = None
    target: Dict[str, Any] = Field(default_factory=dict)
    context: Dict[str, Any] = Field(default_factory=dict)
    result: Dict[str, Any] = Field(default_factory=dict)
    payload: Optional[Dict[str, Any]] = None


class ReadingCoachStreamIn(BaseModel):
    day_number: int = Field(..., ge=1, le=366)
    locale: str = Field(default="en", min_length=2, max_length=8)
    paragraph_index: int = Field(default=0, ge=0, le=50)
    recent_actions: List[ReadingCoachActionIn] = Field(default_factory=list)


class ReadingCoachSelectionIn(BaseModel):
    selection_type: str = Field(..., pattern="^(word|sentence)$")
    selected_text: str = Field(..., min_length=1, max_length=1200)
    sentence_text: str = Field(default="", max_length=1200)
    paragraph_text: str = Field(default="", max_length=2400)
    passage_title: str = Field(default="", max_length=200)
    user_level: Optional[str] = Field(default=None, max_length=32)


class ReadingCoachNoteIn(BaseModel):
    day_number: int = Field(..., ge=1, le=366)
    locale: str = Field(default="en", min_length=2, max_length=8)
    paragraph_index: int = Field(default=0, ge=0, le=50)
    visible_paragraph_indexes: List[int] = Field(default_factory=list)
    reading_selection: Optional[ReadingCoachSelectionIn] = None
    reading_state: Optional[Dict[str, Any]] = None
    recent_actions: List[ReadingCoachActionIn] = Field(default_factory=list)


@router.get("/plan")
async def coaching_plan(
    locale: str = Query("en", min_length=2, max_length=8),
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    try:
        return _ok(await _svc(pg).get_plan(user_id=user.id, locale=locale))
    except ValueError as exc:
        raise _guard(exc)


@router.post("/plan")
async def coaching_create_plan(
    locale: str = Query("en", min_length=2, max_length=8),
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    try:
        return _ok(await _svc(pg).create_plan(user_id=user.id, locale=locale))
    except ValueError as exc:
        raise _guard(exc)


@router.get("/days/{day_number}")
async def coaching_day(
    day_number: int,
    locale: str = Query("en", min_length=2, max_length=8),
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    try:
        return _ok(
            await _svc(pg).get_day(
                user_id=user.id, day_number=day_number, locale=locale
            )
        )
    except ValueError as exc:
        raise _guard(exc)


@router.post("/days/{day_number}/start")
async def coaching_start_day(
    day_number: int,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    try:
        return _ok(await _svc(pg).start_day(user_id=user.id, day_number=day_number))
    except ValueError as exc:
        raise _guard(exc)


@router.put("/days/{day_number}/progress")
async def coaching_save_progress(
    day_number: int,
    payload: CoachingDayProgressIn,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    try:
        return _ok(
            await _svc(pg).save_day_progress(
                user_id=user.id,
                day_number=day_number,
                workspace=payload.workspace,
            )
        )
    except ValueError as exc:
        raise _guard(exc)


@router.post("/days/{day_number}/focus-plan")
async def coaching_focus_plan(
    day_number: int,
    payload: CoachingFocusPlanIn,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    try:
        return _ok(
            await _svc(pg).build_focus_plan(
                user_id=user.id,
                day_number=day_number,
                recall_answers=payload.recall_answers,
                reading_answers=payload.reading_answers,
                reading_vocab_signals=payload.reading_vocab_signals,
                min_words=payload.min_words,
                max_words=payload.max_words,
            )
        )
    except ValueError as exc:
        raise _guard(exc)


@router.post("/events")
async def coaching_events(
    payload: CoachingEventsIn,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    try:
        return _ok(
            await _svc(pg).record_events(
                user_id=user.id,
                day_number=payload.day_number,
                events=[event.model_dump() for event in payload.events],
            )
        )
    except ValueError as exc:
        raise _guard(exc)


@router.post("/reading/explain")
async def coaching_explain(
    payload: CoachingExplainIn,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    try:
        return _ok(
            await _svc(pg).explain_phrase(
                user_id=user.id,
                day_number=payload.day_number,
                phrase=payload.phrase,
                sentence=payload.sentence or "",
            )
        )
    except ValueError as exc:
        raise _guard(exc)


@router.post("/reading/helper-stream")
async def coaching_helper_stream(
    payload: ReadingCoachStreamIn,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    """SSE stream of AI Helper recommendations (markdown, token by token)."""

    async def gen():
        try:
            async for token in _svc(pg).stream_helper_recommendations(
                user_id=user.id,
                day_number=payload.day_number,
                locale=payload.locale,
                paragraph_index=payload.paragraph_index,
                recent_actions=[
                    action.model_dump() for action in payload.recent_actions
                ],
            ):
                yield (
                    f"data: {json.dumps({'type': 'token', 'text': token}, ensure_ascii=False)}\n\n"
                ).encode()
            yield f"data: {json.dumps({'type': 'done'})}\n\n".encode()
        except ValueError as exc:
            yield (
                f"data: {json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"
            ).encode()
        except Exception as exc:  # noqa: BLE001
            yield (
                f"data: {json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"
            ).encode()

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/reading/helper-note")
async def coaching_helper_note(
    payload: ReadingCoachNoteIn,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    try:
        return _ok(
            await _svc(pg).generate_helper_note(
                user_id=user.id,
                day_number=payload.day_number,
                locale=payload.locale,
                paragraph_index=payload.paragraph_index,
                visible_paragraph_indexes=payload.visible_paragraph_indexes,
                reading_selection=(
                    payload.reading_selection.model_dump()
                    if payload.reading_selection
                    else None
                ),
                reading_state=payload.reading_state,
                recent_actions=[
                    action.model_dump() for action in payload.recent_actions
                ],
            )
        )
    except ValueError as exc:
        raise _guard(exc)


@router.post("/reading/translate")
async def coaching_translate(
    payload: CoachingTranslateIn,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    try:
        return _ok(
            await _svc(pg).translate_text(
                user_id=user.id,
                day_number=payload.day_number,
                text=payload.text,
                target=payload.target_language,
            )
        )
    except ValueError as exc:
        raise _guard(exc)


@router.post("/days/{day_number}/questions")
async def coaching_questions(
    day_number: int,
    payload: CoachingQuestionsIn,
    locale: str = Query("en", min_length=2, max_length=8),
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    try:
        return _ok(
            await _svc(pg).generate_questions(
                user_id=user.id,
                day_number=day_number,
                count=payload.count,
                locale=locale,
            )
        )
    except ValueError as exc:
        raise _guard(exc)


@router.post("/days/{day_number}/complete")
async def coaching_complete(
    day_number: int,
    locale: str = Query("en", min_length=2, max_length=8),
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    try:
        return _ok(
            await _svc(pg).complete_day(
                user_id=user.id, day_number=day_number, locale=locale
            )
        )
    except ValueError as exc:
        raise _guard(exc)


@router.post("/reset")
async def coaching_reset(
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
):
    return _ok(await _svc(pg).reset(user_id=user.id))


@router.get("/lookup/{word}")
async def coaching_lookup(
    word: str,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
    redis=Depends(get_redis),
):
    """DB-first dictionary entry; falls back to the external dictionary proxy."""
    cleaned = (word or "").strip().lower()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Empty word")

    db_entry = await _svc(pg).lookup_dictionary(word=cleaned)
    if db_entry is not None and db_entry.get("entries"):
        return _ok(db_entry)

    cached = await _dictionary_from_cache(redis, cleaned)
    if cached is not None:
        cached["source"] = cached.get("source") or "external"
        return _ok(cached)
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(_DICTIONARY_URL.format(word=cleaned))
        if resp.status_code == 404:
            empty = {
                "word": cleaned,
                "entries": [],
                "source": "external",
                "cambridge_link": f"https://dictionary.cambridge.org/dictionary/english/{cleaned}",
                "dictionary_link": f"https://www.merriam-webster.com/dictionary/{cleaned}",
            }
            await _dictionary_to_cache(redis, cleaned, empty)
            return _ok(empty)
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502, detail=f"Dictionary upstream error: {resp.status_code}"
            )
        normalized = _normalize_dictionary(resp.json(), cleaned)
        normalized["source"] = "external"
        await _dictionary_to_cache(redis, cleaned, normalized)
        return _ok(normalized)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Dictionary lookup failed: {exc}")
