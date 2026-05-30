"""Writing repositories on Postgres."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.domain.sql_models import WritingGroup, WritingSubmission, WritingTask


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _group_to_dict(row: WritingGroup) -> Dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "icon": row.icon,
        "sort_order": row.sort_order,
        "total_tasks": row.total_tasks,
        "is_active": row.is_active,
    }


def _task_to_dict(row: WritingTask) -> Dict[str, Any]:
    return {
        "id": row.id,
        "group_id": row.group_id,
        "group_name": row.group_name,
        "task_type": row.task_type,
        "title": row.title,
        "description": row.description,
        "image_url": row.image_url,
        "data_description": row.data_description,
        "time_limit": row.time_limit,
        "difficulty": row.difficulty,
        "tags": list(row.tags or []),
        "access": dict(row.access or {}),
        "tests_taken": row.tests_taken,
        "average_score": float(row.average_score or 0),
        "created_by": row.created_by,
        "is_personal": row.is_personal,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _submission_to_dict(row: WritingSubmission) -> Dict[str, Any]:
    return {
        "submission_id": row.submission_id,
        "user_id": str(row.user_id),
        "task_id": row.task_id,
        "answer": row.answer,
        "word_count": row.word_count,
        "status": row.status,
        "task_snapshot": dict(row.task_snapshot or {}),
        "assessment": row.assessment,
        "error_message": row.error_message,
        "prompt_version": row.prompt_version,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class WritingGroupRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def list(self) -> List[Dict[str, Any]]:
        rows = (
            await self.s.execute(
                select(WritingGroup)
                .where(WritingGroup.is_active.is_(True))
                .order_by(WritingGroup.sort_order)
            )
        ).scalars()
        return [_group_to_dict(r) for r in rows]

    async def get(self, group_id: int) -> Optional[Dict[str, Any]]:
        row = await self.s.get(WritingGroup, group_id)
        return _group_to_dict(row) if row else None

    async def get_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        row = (
            await self.s.execute(select(WritingGroup).where(WritingGroup.name == name))
        ).scalar_one_or_none()
        return _group_to_dict(row) if row else None

    async def upsert(self, group: Dict[str, Any]) -> None:
        stmt = pg_insert(WritingGroup).values(
            id=group["id"],
            name=group["name"],
            description=group.get("description"),
            icon=group.get("icon"),
            sort_order=int(group.get("sort_order") or 0),
            total_tasks=int(group.get("total_tasks") or 0),
            is_active=bool(group.get("is_active", True)),
        )
        await self.s.execute(
            stmt.on_conflict_do_update(
                index_elements=[WritingGroup.id],
                set_={
                    "name": group["name"],
                    "description": group.get("description"),
                    "icon": group.get("icon"),
                    "sort_order": int(group.get("sort_order") or 0),
                    "total_tasks": int(group.get("total_tasks") or 0),
                    "is_active": bool(group.get("is_active", True)),
                },
            )
        )


class WritingTaskRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def get(self, task_id: int) -> Optional[Dict[str, Any]]:
        row = await self.s.get(WritingTask, task_id)
        return _task_to_dict(row) if row else None

    async def list_by_group(
        self, *, group_id: int, group_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        q = select(WritingTask).where(
            WritingTask.group_id == group_id,
            WritingTask.is_personal.is_(False),
        )
        if group_name:
            q = q.where(WritingTask.group_name == group_name)
        rows = (await self.s.execute(q.order_by(WritingTask.id))).scalars()
        return [_task_to_dict(r) for r in rows]

    async def list_personal_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        rows = (
            await self.s.execute(
                select(WritingTask)
                .where(
                    WritingTask.is_personal.is_(True),
                    WritingTask.created_by == user_id,
                )
                .order_by(WritingTask.created_at.desc())
            )
        ).scalars()
        return [_task_to_dict(r) for r in rows]

    async def insert(self, task: Dict[str, Any]) -> None:
        if "id" not in task:
            max_id = await self.s.scalar(select(func.max(WritingTask.id)))
            task["id"] = int(max_id or 0) + 1
        self.s.add(
            WritingTask(
                id=int(task["id"]),
                group_id=int(task["group_id"]),
                group_name=task["group_name"],
                task_type=task["task_type"],
                title=task["title"],
                description=task.get("description") or "",
                image_url=task.get("image_url"),
                data_description=task.get("data_description"),
                time_limit=int(task.get("time_limit") or 1200),
                difficulty=task.get("difficulty") or "intermediate",
                tags=list(task.get("tags") or []),
                access=dict(task.get("access") or {}),
                tests_taken=int(task.get("tests_taken") or 0),
                average_score=float(task.get("average_score") or 0),
                created_by=task.get("created_by"),
                is_personal=bool(task.get("is_personal")),
                created_at=task.get("created_at") or datetime.utcnow(),
                updated_at=task.get("updated_at") or datetime.utcnow(),
            )
        )
        await self.s.flush()
        task["id"] = int(task["id"])

    async def upsert(self, task: Dict[str, Any]) -> None:
        if "id" not in task:
            max_id = await self.s.scalar(select(func.max(WritingTask.id)))
            task["id"] = int(max_id or 0) + 1
        stmt = pg_insert(WritingTask).values(
            id=int(task["id"]),
            group_id=int(task["group_id"]),
            group_name=task["group_name"],
            task_type=task["task_type"],
            title=task["title"],
            description=task.get("description") or "",
            image_url=task.get("image_url"),
            data_description=task.get("data_description"),
            time_limit=int(task.get("time_limit") or 1200),
            difficulty=task.get("difficulty") or "intermediate",
            tags=list(task.get("tags") or []),
            access=dict(task.get("access") or {}),
            tests_taken=int(task.get("tests_taken") or 0),
            average_score=float(task.get("average_score") or 0),
            created_by=task.get("created_by"),
            is_personal=bool(task.get("is_personal")),
            created_at=task.get("created_at") or datetime.utcnow(),
            updated_at=task.get("updated_at") or datetime.utcnow(),
        )
        await self.s.execute(
            stmt.on_conflict_do_update(
                index_elements=[WritingTask.id],
                set_={
                    "group_id": int(task["group_id"]),
                    "group_name": task["group_name"],
                    "task_type": task["task_type"],
                    "title": task["title"],
                    "description": task.get("description") or "",
                    "image_url": task.get("image_url"),
                    "data_description": task.get("data_description"),
                    "time_limit": int(task.get("time_limit") or 1200),
                    "difficulty": task.get("difficulty") or "intermediate",
                    "tags": list(task.get("tags") or []),
                    "access": dict(task.get("access") or {}),
                    "created_by": task.get("created_by"),
                    "is_personal": bool(task.get("is_personal")),
                    "updated_at": datetime.utcnow(),
                },
            )
        )
        await self.s.flush()

    async def increment_attempts(self, task_id: int) -> None:
        await self.s.execute(
            update(WritingTask)
            .where(WritingTask.id == task_id)
            .values(tests_taken=WritingTask.tests_taken + 1)
        )


class WritingSubmissionRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def delete_all_for_user(self, user_id: str) -> int:
        result = await self.s.execute(
            delete(WritingSubmission).where(WritingSubmission.user_id == _uuid(user_id))
        )
        return int(result.rowcount or 0)

    async def create(self, submission: Dict[str, Any]) -> None:
        self.s.add(
            WritingSubmission(
                submission_id=submission["submission_id"],
                user_id=_uuid(submission["user_id"]),
                task_id=int(submission["task_id"]),
                answer=submission["answer"],
                word_count=int(submission.get("word_count") or 0),
                status=submission.get("status") or "queued",
                task_snapshot=dict(submission.get("task_snapshot") or {}),
                assessment=submission.get("assessment"),
                error_message=submission.get("error_message"),
                prompt_version=submission.get("prompt_version"),
                started_at=submission.get("started_at"),
                finished_at=submission.get("finished_at"),
                created_at=submission.get("created_at") or datetime.utcnow(),
                updated_at=submission.get("updated_at") or datetime.utcnow(),
            )
        )

    async def get(self, submission_id: str) -> Optional[Dict[str, Any]]:
        row = await self.s.get(WritingSubmission, submission_id)
        return _submission_to_dict(row) if row else None

    async def list_for_task(
        self, *, user_id: str, task_id: int, limit: int = 20
    ) -> List[Dict[str, Any]]:
        rows = (
            await self.s.execute(
                select(WritingSubmission)
                .where(
                    WritingSubmission.user_id == _uuid(user_id),
                    WritingSubmission.task_id == task_id,
                )
                .order_by(WritingSubmission.created_at.desc())
                .limit(limit)
            )
        ).scalars()
        return [_submission_to_dict(r) for r in rows]

    async def list_recent_for_user(
        self, *, user_id: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        rows = (
            await self.s.execute(
                select(WritingSubmission)
                .where(WritingSubmission.user_id == _uuid(user_id))
                .order_by(WritingSubmission.created_at.desc())
                .limit(limit)
            )
        ).scalars()
        return [_submission_to_dict(r) for r in rows]

    async def count_for_user(self, *, user_id: str) -> int:
        return int(
            await self.s.scalar(
                select(func.count())
                .select_from(WritingSubmission)
                .where(WritingSubmission.user_id == _uuid(user_id))
            )
            or 0
        )

    async def update_status(
        self, submission_id: str, status: str, **fields: Any
    ) -> None:
        values: Dict[str, Any] = {
            "status": status,
            "updated_at": datetime.utcnow(),
            **fields,
        }
        await self.s.execute(
            update(WritingSubmission)
            .where(WritingSubmission.submission_id == submission_id)
            .values(**values)
        )

    async def attach_assessment(
        self, submission_id: str, assessment: Dict[str, Any]
    ) -> None:
        await self.s.execute(
            update(WritingSubmission)
            .where(WritingSubmission.submission_id == submission_id)
            .values(
                status="completed",
                assessment=assessment,
                finished_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
