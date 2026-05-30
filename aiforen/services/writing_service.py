"""High-level writing orchestration.

Submits an assessment job, streams progress to the FE via SSE backed by
Redis Pub/Sub, and surfaces task/group listings.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional

import redis.asyncio as redis_async
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.core.errors import Forbidden, NotFound
from aiforen.repositories.pg.writing import (
    WritingGroupRepo,
    WritingSubmissionRepo,
    WritingTaskRepo,
)

JOB_STREAM = "stream:assess"
CHANNEL_PREFIX = "assess:"
RESULT_LIST_PREFIX = "assess:replay:"
RESULT_TTL_SECONDS = 60 * 60  # 1h


class WritingService:
    def __init__(
        self,
        *,
        pg: AsyncSession,
        redis: redis_async.Redis,
    ):
        self.groups = WritingGroupRepo(pg)
        self.tasks = WritingTaskRepo(pg)
        self.submissions = WritingSubmissionRepo(pg)
        self.redis = redis

    # ---------- listings ----------

    async def list_groups(self) -> List[Dict[str, Any]]:
        return await self.groups.list()

    async def list_tasks(
        self, *, group_id: int, group_name: Optional[str]
    ) -> List[Dict[str, Any]]:
        return await self.tasks.list_by_group(group_id=group_id, group_name=group_name)

    async def get_task(self, task_id: int) -> Dict[str, Any]:
        task = await self.tasks.get(task_id)
        if not task:
            raise NotFound(f"Writing task {task_id} not found")
        return task

    async def list_personal(self, user_id: str) -> List[Dict[str, Any]]:
        return await self.tasks.list_personal_for_user(user_id)

    async def create_personal(
        self,
        *,
        user_id: str,
        task_type: str,
        title: str,
        description: str,
        image_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        personal_group = await self.groups.get_by_name("Personal Tasks")
        if not personal_group:
            personal_group = {
                "id": 999,
                "name": "Personal Tasks",
                "description": "Custom tasks created by users",
                "icon": "User",
                "sort_order": 0,
                "total_tasks": 0,
                "is_active": True,
            }
            await self.groups.upsert(personal_group)

        task = {
            "group_id": personal_group["id"],
            "group_name": personal_group["name"],
            "task_type": task_type,
            "title": title,
            "description": description,
            "image_url": image_url,
            "data_description": "",
            "time_limit": 1200 if task_type == "task_1" else 2400,
            "difficulty": "intermediate",
            "tags": [],
            "access": {"free_access": True, "required_plan": None, "daily_limit": None},
            "tests_taken": 0,
            "average_score": 0.0,
            "created_by": user_id,
            "is_personal": True,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        await self.tasks.insert(task)
        return task

    # ---------- submission lifecycle ----------

    async def submit(self, *, user_id: str, task_id: int, answer: str) -> str:
        task = await self.get_task(task_id)
        submission_id = f"sub_{secrets.token_urlsafe(12)}"
        word_count = len([w for w in answer.split() if w])
        now = datetime.utcnow()
        await self.submissions.create(
            {
                "submission_id": submission_id,
                "user_id": user_id,
                "task_id": task_id,
                "answer": answer,
                "word_count": word_count,
                "status": "queued",
                "task_snapshot": {
                    "id": task["id"],
                    "title": task.get("title"),
                    "task_type": task.get("task_type"),
                    "description": task.get("description"),
                    "image_url": task.get("image_url"),
                },
                "created_at": now,
                "updated_at": now,
            }
        )
        await self.tasks.increment_attempts(task_id)
        await self.redis.xadd(
            JOB_STREAM,
            {
                "submission_id": submission_id,
                "user_id": user_id,
                "task_id": str(task_id),
            },
            maxlen=10_000,
            approximate=True,
        )
        logger.info("Enqueued submission {} (task {})", submission_id, task_id)
        return submission_id

    async def stream(self, submission_id: str) -> AsyncIterator[Dict[str, Any]]:
        """Replay any chunks already cached, then live-tail the channel."""

        replay_key = f"{RESULT_LIST_PREFIX}{submission_id}"
        channel = f"{CHANNEL_PREFIX}{submission_id}"

        cached = await self.redis.lrange(replay_key, 0, -1)
        for raw in cached:
            yield json.loads(raw)

        sub = await self.submissions.get(submission_id)
        if sub and sub.get("status") == "completed":
            yield {
                "status": "completed",
                "step": "final",
                "data": sub.get("assessment"),
                "submission_id": submission_id,
            }
            return

        pubsub = self.redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for message in pubsub.listen():
                if message is None:
                    continue
                if message.get("type") != "message":
                    continue
                payload = json.loads(message["data"])
                yield payload
                if payload.get("step") == "final" or payload.get("status") == "error":
                    break
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    async def list_assessments(
        self, *, user_id: str, task_id: int
    ) -> List[Dict[str, Any]]:
        rows = await self.submissions.list_for_task(user_id=user_id, task_id=task_id)
        return [self._wire_submission(r) for r in rows]

    async def recent_assessments(
        self, *, user_id: str, limit: int = 5
    ) -> List[Dict[str, Any]]:
        rows = await self.submissions.list_recent_for_user(user_id=user_id, limit=limit)
        out: List[Dict[str, Any]] = []
        for r in rows:
            wire = self._wire_submission(r)
            task = None
            try:
                task = await self.tasks.get(int(r.get("task_id")))
            except Exception:
                task = None
            wire["task_title"] = (task or {}).get("title") or f"Task {r.get('task_id')}"
            wire["task_type"] = (task or {}).get("task_type")
            wire["word_count"] = r.get("word_count")
            out.append(wire)
        return out

    async def count_user_submissions(self, *, user_id: str) -> int:
        return await self.submissions.count_for_user(user_id=user_id)

    @staticmethod
    def _wire_submission(s: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": s["submission_id"],
            "task_id": s["task_id"],
            "user_id": s["user_id"],
            "answer": s["answer"],
            "status": s.get("status"),
            "assessment": s.get("assessment"),
            "created_at": (s.get("created_at") or datetime.utcnow()).isoformat(),
        }

    async def assert_owner(self, submission_id: str, user_id: str) -> Dict[str, Any]:
        sub = await self.submissions.get(submission_id)
        if not sub:
            raise NotFound("Submission not found")
        if sub.get("user_id") != user_id:
            raise Forbidden("You do not own this submission")
        return sub

    async def get_submission(self, submission_id: str) -> Dict[str, Any]:
        sub = await self.submissions.get(submission_id)
        if not sub:
            raise NotFound("Submission not found")
        return sub
