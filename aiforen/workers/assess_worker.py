"""Standalone assessment worker.

Consumes the `stream:assess` Redis stream, runs the LLM, publishes
chunks to the per-submission channel, caches the chunks for replay,
and writes the final assessment back into Postgres.
"""

from __future__ import annotations

import asyncio
import json
import signal
from datetime import datetime
from typing import Any, Dict

from loguru import logger

from aiforen.core import db as core_db
from aiforen.core.config import get_settings
from aiforen.integrations.llm import get_llm_provider
from aiforen.repositories.pg.writing import WritingSubmissionRepo, WritingTaskRepo
from aiforen.services.writing_service import (
    CHANNEL_PREFIX,
    JOB_STREAM,
    RESULT_LIST_PREFIX,
    RESULT_TTL_SECONDS,
)

CONSUMER_GROUP = "assess-workers"
CONSUMER_NAME = "worker-1"

settings = get_settings()


async def _ensure_group(redis):
    try:
        await redis.xgroup_create(JOB_STREAM, CONSUMER_GROUP, id="$", mkstream=True)
        logger.info("Created consumer group {}", CONSUMER_GROUP)
    except Exception as exc:
        if "BUSYGROUP" in str(exc):
            return
        raise


async def _publish_event(redis, submission_id: str, payload: Dict[str, Any]) -> None:
    payload = {**payload, "submission_id": submission_id}
    raw = json.dumps(payload, default=str)
    channel = f"{CHANNEL_PREFIX}{submission_id}"
    replay_key = f"{RESULT_LIST_PREFIX}{submission_id}"
    pipeline = redis.pipeline(transaction=False)
    pipeline.publish(channel, raw)
    pipeline.rpush(replay_key, raw)
    pipeline.expire(replay_key, RESULT_TTL_SECONDS)
    await pipeline.execute()


async def _process(submission_id: str, redis) -> None:
    async with core_db.pg_session() as session:
        submissions = WritingSubmissionRepo(session)
        tasks = WritingTaskRepo(session)

        sub = await submissions.get(submission_id)
        if not sub:
            logger.warning("Submission {} not found, skipping", submission_id)
            return

        task = await tasks.get(sub["task_id"])
        if not task:
            await submissions.update_status(
                submission_id, "failed", error_message="Task missing"
            )
            await _publish_event(
                redis,
                submission_id,
                {"status": "error", "message": "Task no longer exists"},
            )
            return

        await submissions.update_status(
            submission_id, "processing", started_at=datetime.utcnow()
        )

        provider = get_llm_provider()
        final_assessment: Dict[str, Any] | None = None
        try:
            await _publish_event(
                redis,
                submission_id,
                {"status": "processing", "message": "Initializing evaluation…"},
            )
            async for event in provider.evaluate_writing(
                task=task, answer=sub["answer"]
            ):
                payload = event.to_payload()
                await _publish_event(redis, submission_id, payload)
                if event.step == "final" and event.data is not None:
                    final_assessment = event.data

            if final_assessment is not None:
                await submissions.attach_assessment(submission_id, final_assessment)
                logger.info("Submission {} completed", submission_id)
            else:
                await submissions.update_status(
                    submission_id,
                    "failed",
                    error_message="LLM did not return final assessment",
                )
                await _publish_event(
                    redis,
                    submission_id,
                    {"status": "error", "message": "Evaluation did not finish"},
                )
        except Exception as exc:
            logger.exception("Assessment failed for {}", submission_id)
            await submissions.update_status(
                submission_id, "failed", error_message=str(exc)
            )
            await _publish_event(
                redis,
                submission_id,
                {"status": "error", "message": str(exc)},
            )


async def main() -> None:
    logger.info("Worker starting (provider={})", settings.llm_provider)
    core_db.init_pg()
    redis = core_db.init_redis()
    await _ensure_group(redis)

    stop_event = asyncio.Event()

    def _stop(*_: Any) -> None:
        logger.info("Worker stop signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass

    semaphore = asyncio.Semaphore(settings.worker_concurrency)
    in_flight: set[asyncio.Task[None]] = set()

    async def _run_one(submission_id: str, msg_id: str) -> None:
        async with semaphore:
            try:
                await _process(submission_id, redis)
            finally:
                await redis.xack(JOB_STREAM, CONSUMER_GROUP, msg_id)

    try:
        while not stop_event.is_set():
            try:
                response = await redis.xreadgroup(
                    CONSUMER_GROUP,
                    CONSUMER_NAME,
                    {JOB_STREAM: ">"},
                    count=settings.worker_concurrency,
                    block=2000,
                )
            except Exception as exc:
                logger.error("xreadgroup error: {}", exc)
                await asyncio.sleep(1)
                continue

            if not response:
                continue

            for _stream, entries in response:
                for msg_id, data in entries:
                    submission_id = data.get("submission_id")
                    if not submission_id:
                        await redis.xack(JOB_STREAM, CONSUMER_GROUP, msg_id)
                        continue
                    task = asyncio.create_task(_run_one(submission_id, msg_id))
                    in_flight.add(task)
                    task.add_done_callback(in_flight.discard)
    finally:
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)
        await core_db.shutdown_all()
        logger.info("Worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
