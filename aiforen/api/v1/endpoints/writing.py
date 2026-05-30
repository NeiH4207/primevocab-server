"""Writing endpoints — match the FE writingService contract exactly."""

from __future__ import annotations

import json
import secrets
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Query,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.core.deps import (
    CurrentUser,
    get_current_user,
    get_pg,
    get_redis,
)
from aiforen.core.errors import Forbidden, NotFound
from aiforen.domain.enums import QuotaKind
from aiforen.repositories.pg.payments import PublicAssessmentRepo
from aiforen.repositories.pg.plans import SubscriptionRepo
from aiforen.repositories.pg.users import UserRepo
from aiforen.services.quota_service import QuotaService
from aiforen.services.writing_service import WritingService

router = APIRouter()


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------


@router.get("/task-groups")
async def task_groups(
    pg: AsyncSession = Depends(get_pg), redis: Redis = Depends(get_redis)
):
    svc = WritingService(pg=pg, redis=redis)
    groups = await svc.list_groups()
    return {"status": "success", "data": groups}


@router.get("/tasks")
async def list_tasks(
    group_id: int = Query(...),
    group_name: Optional[str] = Query(None),
    pg: AsyncSession = Depends(get_pg),
    redis: Redis = Depends(get_redis),
):
    svc = WritingService(pg=pg, redis=redis)
    tasks = await svc.list_tasks(group_id=group_id, group_name=group_name)
    return {"status": "success", "data": tasks}


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: int, pg: AsyncSession = Depends(get_pg), redis: Redis = Depends(get_redis)
):
    svc = WritingService(pg=pg, redis=redis)
    task = await svc.get_task(task_id)
    return {"status": "success", "data": task}


@router.get("/personal-tasks")
async def list_personal(
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
    redis: Redis = Depends(get_redis),
):
    svc = WritingService(pg=pg, redis=redis)
    tasks = await svc.list_personal(user.id)
    return {"status": "success", "data": tasks}


@router.post("/personal-tasks")
async def create_personal_task(
    task_type: str = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    image: Optional[UploadFile] = File(None),
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
    redis: Redis = Depends(get_redis),
):
    # Plan gate: only paid users
    sub_repo = SubscriptionRepo(pg)
    user_repo = UserRepo(pg)
    user_row = await user_repo.get(user.id)
    if not user_row:
        raise NotFound("User missing")
    sub = await sub_repo.active_for_user(user_row.id)
    plan = sub.plan_code if sub else "free"
    if plan == "free":
        raise Forbidden("Personal tasks require Basic or Pro")

    svc = WritingService(pg=pg, redis=redis)
    image_url = None
    if image is not None:
        # Local-dev: skip real S3 — store filename only
        image_url = f"/uploads/{secrets.token_urlsafe(8)}-{image.filename}"
    task = await svc.create_personal(
        user_id=user.id,
        task_type=task_type,
        title=title,
        description=description,
        image_url=image_url,
    )
    return {"status": "success", "data": task}


# ---------------------------------------------------------------------------
# Assessments
# ---------------------------------------------------------------------------


class AssessmentIn(BaseModel):
    task_id: int
    answer: str = Field(..., min_length=1)


@router.post("/assessments")
async def submit_assessment(
    payload: AssessmentIn,
    user: CurrentUser = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
    pg: AsyncSession = Depends(get_pg),
):
    """Streaming response.

    The FE reads NDJSON chunks and renders incrementally.  We:
        1. Enforce monthly quota.
        2. Enqueue the job for the worker.
        3. Stream events live from Redis Pub/Sub.
    """

    quota = QuotaService(pg)
    await quota.consume(user.id, QuotaKind.assessment)

    svc = WritingService(pg=pg, redis=redis)
    submission_id = await svc.submit(
        user_id=user.id, task_id=payload.task_id, answer=payload.answer
    )

    async def gen():
        # First chunk so the FE has the submission id immediately
        first = {
            "status": "processing",
            "submission_id": submission_id,
            "message": "Queued for evaluation",
        }
        yield (json.dumps(first) + "\n").encode()
        async for event in svc.stream(submission_id):
            yield (json.dumps(event, default=str) + "\n").encode()

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@router.get("/assessments")
async def list_assessments(
    task_id: int = Query(...),
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
    redis: Redis = Depends(get_redis),
):
    svc = WritingService(pg=pg, redis=redis)
    rows = await svc.list_assessments(user_id=user.id, task_id=task_id)
    return {"status": "success", "data": rows}


@router.get("/assessments/recent")
async def recent_assessments(
    limit: int = Query(5, ge=1, le=20),
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
    redis: Redis = Depends(get_redis),
):
    svc = WritingService(pg=pg, redis=redis)
    rows = await svc.recent_assessments(user_id=user.id, limit=limit)
    total = await svc.count_user_submissions(user_id=user.id)
    return {"status": "success", "data": rows, "total": total}


@router.get("/assessments/{submission_id}/stream")
async def stream_assessment(
    submission_id: str,
    user: CurrentUser = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
    pg: AsyncSession = Depends(get_pg),
):
    """SSE replay endpoint — survives an F5 in the browser."""

    svc = WritingService(pg=pg, redis=redis)
    await svc.assert_owner(submission_id, user.id)

    async def gen():
        async for event in svc.stream(submission_id):
            yield f"data: {json.dumps(event, default=str)}\n\n".encode()

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------


@router.post("/assessments/{submission_id}/publish")
async def publish_assessment(
    submission_id: str,
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
    redis: Redis = Depends(get_redis),
):
    svc = WritingService(pg=pg, redis=redis)
    sub = await svc.assert_owner(submission_id, user.id)
    if sub.get("status") != "completed":
        raise Forbidden("Submission has not finished evaluating yet")

    repo = PublicAssessmentRepo(pg)
    existing = await repo.by_submission(submission_id)
    if existing:
        return {"status": "success", "data": {"public_id": existing.public_id}}

    public_id = secrets.token_urlsafe(10)
    user_repo = UserRepo(pg)
    user_row = await user_repo.get(user.id)
    if not user_row:
        raise NotFound("User not found")
    await repo.create(
        public_id=public_id, submission_id=submission_id, user_id=user_row.id
    )
    return {"status": "success", "data": {"public_id": public_id}}


@router.get("/assessments/public/{public_id}")
async def public_assessment(
    public_id: str,
    pg: AsyncSession = Depends(get_pg),
    redis: Redis = Depends(get_redis),
):
    repo = PublicAssessmentRepo(pg)
    pa = await repo.get(public_id)
    if not pa:
        raise NotFound("Public assessment not found")

    svc = WritingService(pg=pg, redis=redis)
    sub = await svc.get_submission(pa.submission_id)
    task = await svc.get_task(sub["task_id"])
    return {
        "status": "success",
        "data": {
            "task_details": {
                "title": task.get("title"),
                "description": task.get("description"),
                "image_url": task.get("image_url"),
            },
            "answer": sub.get("answer"),
            "assessment": sub.get("assessment"),
        },
    }
